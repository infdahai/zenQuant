#![cfg(target_os = "linux")]
use serde_json::Value;
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Output},
    sync::atomic::{AtomicUsize, Ordering},
};
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let path = fs::canonicalize(std::env::temp_dir())
            .unwrap()
            .join(format!(
                "intent-cli-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn store(&self) -> PathBuf {
        self.0.join("store")
    }
    fn init(&self) {
        assert!(
            command("init", &self.store())
                .args(["--store-id", "sim-test"])
                .output()
                .unwrap()
                .status
                .success()
        );
    }
    fn request(&self) -> PathBuf {
        let p = self.0.join("request.json");
        fs::copy(fixture("simulated-intent"), &p).unwrap();
        p
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!("fixtures/{name}.json"))
}
fn command(action: &str, store: &Path) -> Command {
    let mut c = Command::new(env!("CARGO_BIN_EXE_simulated-intents"));
    c.env("PATH", "/nonexistent")
        .arg(action)
        .arg("--store-dir")
        .arg(store);
    c
}
fn record(store: &Path, request: &Path) -> Output {
    command("record", store)
        .arg("--request-file")
        .arg(request)
        .output()
        .unwrap()
}
fn decode(out: &Output) -> Value {
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    serde_json::from_slice(&out.stdout).unwrap()
}
fn snapshot(t: &Temp) -> Vec<u8> {
    fs::read(t.store().join("snapshot.json")).unwrap()
}

#[test]
fn fresh_process_retries_and_replays_preserve_simulated_identity_and_exact_values() {
    let t = Temp::new();
    t.init();
    let input = t.request();
    let first = decode(&record(&t.store(), &input));
    let before = snapshot(&t);
    assert_eq!(first["result"], "RECORDED");
    assert_eq!(first["mode"], "SIMULATED");
    assert_eq!(first["record_count"], 1);
    assert_eq!(first["funds_reserved"], false);
    assert_eq!(first["risk_assessment"], "NOT_ASSESSED");
    assert_eq!(first["account_evidence"], "UNVERIFIED");
    assert_eq!(first["execution_authority"], "NONE");
    assert_eq!(first["execution_evidence"], "UNVERIFIED");
    assert_eq!(first["records"][0]["state"], "RECORDED_UNASSESSED");
    assert_eq!(
        first["records"][0]["intent"]["leg"]["quantity_native"],
        "0.01"
    );
    let mut v: Value = serde_json::from_slice(&fs::read(&input).unwrap()).unwrap();
    v["limit_price_native"] = "2.000".into();
    fs::write(&input, serde_json::to_vec(&v).unwrap()).unwrap();
    let retry = decode(&record(&t.store(), &input));
    assert_eq!(retry["result"], "ALREADY_RECORDED");
    assert_eq!(retry["records"], first["records"]);
    assert_eq!(snapshot(&t), before);
    let a = command("replay", &t.store()).output().unwrap();
    let b = command("replay", &t.store()).output().unwrap();
    assert_eq!(decode(&a)["records"], first["records"]);
    assert_eq!(a.stdout, b.stdout);
}

#[test]
fn conflicts_invalid_inputs_and_diagnostic_files_cannot_change_state() {
    let t = Temp::new();
    t.init();
    let input = t.request();
    decode(&record(&t.store(), &input));
    let old = snapshot(&t);
    let mut v: Value = serde_json::from_slice(&fs::read(&input).unwrap()).unwrap();
    v["limit_price_native"] = "3".into();
    fs::write(&input, serde_json::to_vec(&v).unwrap()).unwrap();
    let out = record(&t.store(), &input);
    assert_eq!(out.status.code(), Some(2));
    assert_eq!(out.stderr, b"INTENT_ID_CONFLICT\n");
    v["mode"] = "LIVE".into();
    fs::write(&input, serde_json::to_vec(&v).unwrap()).unwrap();
    assert_eq!(record(&t.store(), &input).stderr, b"INVALID_INTENT_INPUT\n");
    for f in [
        "quote-costs",
        "terminal-call-scenarios",
        "terminal-eth-call",
    ] {
        let out = record(&t.store(), &fixture(f));
        assert_eq!(out.status.code(), Some(2));
        assert_eq!(out.stderr, b"INVALID_INTENT_INPUT\n");
    }
    assert_eq!(snapshot(&t), old);
}

#[test]
fn explicit_init_and_strict_flags_do_not_reset_or_implicitly_create_a_store() {
    let t = Temp::new();
    assert_eq!(
        record(&t.store(), &fixture("simulated-intent"))
            .status
            .code(),
        Some(2)
    );
    assert!(!t.store().exists());
    for (action, flags) in [
        ("init", vec!["--store-id", "test", "--request-file", "x"]),
        ("init", vec!["--store-id", "test", "--store-id", "again"]),
        ("record", vec!["--request-file", "x", "--output-dir", "y"]),
        ("replay", vec!["--store-id", "test"]),
        ("submit-order", vec![]),
    ] {
        assert_eq!(
            command(action, &t.store())
                .args(flags)
                .output()
                .unwrap()
                .status
                .code(),
            Some(2)
        );
        assert!(!t.store().exists());
    }
    t.init();
    let old = snapshot(&t);
    assert_eq!(
        command("init", &t.store())
            .args(["--store-id", "new"])
            .output()
            .unwrap()
            .status
            .code(),
        Some(2)
    );
    assert_eq!(snapshot(&t), old);
}

#[test]
fn actual_process_race_records_one_semantic_request_once_and_unique_requests_without_loss() {
    let t = Temp::new();
    t.init();
    let input = fixture("simulated-intent");
    let mut children: Vec<_> = (0..8)
        .map(|_| {
            command("record", &t.store())
                .arg("--request-file")
                .arg(&input)
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .spawn()
                .unwrap()
        })
        .collect();
    let mut inserted = 0;
    for child in children.drain(..) {
        let out = child.wait_with_output().unwrap();
        if out.status.code() == Some(3) {
            assert_eq!(out.stderr, b"STORE_BUSY\n");
        } else {
            let value = decode(&out);
            inserted += usize::from(value["result"] == "RECORDED");
        }
    }
    assert_eq!(inserted, 1);
    assert_eq!(
        decode(&command("replay", &t.store()).output().unwrap())["record_count"],
        1
    );
    let workers: Vec<_> = (0..8)
        .map(|n| {
            let mut value: Value = serde_json::from_slice(&fs::read(&input).unwrap()).unwrap();
            value["request_id"] = format!("unique-{n}").into();
            let file = t.0.join(format!("request-{n}"));
            fs::write(&file, serde_json::to_vec(&value).unwrap()).unwrap();
            let store = t.store();
            std::thread::spawn(move || {
                for _ in 0..100 {
                    let out = record(&store, &file);
                    if out.status.code() != Some(3) {
                        assert_eq!(decode(&out)["result"], "RECORDED");
                        return;
                    }
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                panic!("bounded lock retry exhausted");
            })
        })
        .collect();
    for worker in workers {
        worker.join().unwrap();
    }
    let replay = decode(&command("replay", &t.store()).output().unwrap());
    assert_eq!(replay["record_count"], 9);
    for (n, r) in replay["records"].as_array().unwrap().iter().enumerate() {
        assert_eq!(r["sequence"], n + 1);
    }
}

#[test]
fn corrupt_truncated_and_missing_snapshots_fail_closed_in_fresh_processes() {
    let t = Temp::new();
    t.init();
    let input = fixture("simulated-intent");
    decode(&record(&t.store(), &input));
    let old = snapshot(&t);
    for bad in [b"{}".to_vec(), old[..old.len() / 2].to_vec()] {
        fs::write(t.store().join("snapshot.json"), &bad).unwrap();
        for out in [
            command("replay", &t.store()).output().unwrap(),
            record(&t.store(), &input),
        ] {
            assert_eq!(out.status.code(), Some(2));
            assert_eq!(out.stderr, b"CORRUPT_OR_MISSING_STORE\n");
        }
        assert_eq!(snapshot(&t), bad);
    }
    fs::remove_file(t.store().join("snapshot.json")).unwrap();
    assert_eq!(
        record(&t.store(), &input).stderr,
        b"CORRUPT_OR_MISSING_STORE\n"
    );
    assert!(!t.store().join("snapshot.json").exists());
}

#[test]
fn input_overlap_symlinks_and_sealed_roots_remain_protected() {
    use std::os::unix::fs::symlink;
    let t = Temp::new();
    t.init();
    let old = snapshot(&t);
    fs::copy(fixture("simulated-intent"), t.store().join("pending.json")).unwrap();
    assert_eq!(
        record(&t.store(), &t.store().join("pending.json")).stderr,
        b"INVALID_INTENT_INPUT\n"
    );
    symlink(fixture("simulated-intent"), t.0.join("link")).unwrap();
    assert_eq!(
        record(&t.store(), &t.0.join("link")).stderr,
        b"INVALID_INTENT_INPUT\n"
    );
    for root in options_workbench::io::trusted_roots().unwrap() {
        let path = root.join("intent-store-forbidden");
        assert_eq!(
            command("init", &path)
                .args(["--store-id", "test"])
                .output()
                .unwrap()
                .status
                .code(),
            Some(2)
        );
        assert!(!path.exists());
    }
    assert_eq!(snapshot(&t), old);
}

#[test]
fn failed_receipt_delivery_is_uncertain_and_retry_does_not_duplicate_commit() {
    use std::os::fd::FromRawFd;
    let t = Temp::new();
    t.init();
    // A pipe with no reader gives a real EPIPE after the state has been committed.
    let mut fds = [0; 2];
    assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0);
    assert_eq!(unsafe { libc::close(fds[0]) }, 0);
    let writer = unsafe { fs::File::from_raw_fd(fds[1]) };
    let out = command("record", &t.store())
        .arg("--request-file")
        .arg(fixture("simulated-intent"))
        .stdout(writer)
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap()
        .wait_with_output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(out.stderr, b"STORE_DURABILITY_UNCONFIRMED\n");
    let retry = decode(&record(&t.store(), &fixture("simulated-intent")));
    assert_eq!(retry["result"], "ALREADY_RECORDED");
    assert_eq!(retry["record_count"], 1);
}

#[test]
fn third_module_snapshot_and_receipts_match_published_baseline_byte_for_byte() {
    let t = Temp::new();
    let baseline = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../docs/reports/simulated-intents-20261011");
    let init = command("init", &t.store())
        .args(["--store-id", "cloud-simulated"])
        .output()
        .unwrap();
    let first = record(&t.store(), &fixture("simulated-intent"));
    let retry = record(&t.store(), &fixture("simulated-intent"));
    let replay = command("replay", &t.store()).output().unwrap();
    for (name, out) in [
        ("init.json", init),
        ("record.json", first),
        ("retry.json", retry),
        ("replay.json", replay),
    ] {
        assert!(out.status.success());
        assert_eq!(out.stdout, fs::read(baseline.join(name)).unwrap());
    }
    assert_eq!(
        snapshot(&t),
        fs::read(baseline.join("snapshot.json")).unwrap()
    );
}
