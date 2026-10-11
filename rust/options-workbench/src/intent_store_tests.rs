use super::*;
use std::{
    fs,
    process::{Command, Stdio},
    sync::atomic::{AtomicUsize, Ordering},
    time::Duration,
};

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let p = fs::canonicalize(std::env::temp_dir())
            .unwrap()
            .join(format!(
                "intent-store-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
    fn store(&self) -> PathBuf {
        self.0.join("store")
    }
    fn setup(&self) {
        initialize(&self.store(), "sim-demo").unwrap();
        record(&self.store(), &request("one")).unwrap();
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
fn request(id: &str) -> Vec<u8> {
    let mut r =
        intents::decode_request(include_bytes!("../fixtures/simulated-intent.json")).unwrap();
    r.request_id = id.into();
    serde_json::to_vec(&r).unwrap()
}
fn bytes(t: &Temp) -> Vec<u8> {
    fs::read(t.store().join(SNAPSHOT)).unwrap()
}

#[test]
fn idempotency_is_semantic_and_global_across_simulated_accounts() {
    let t = Temp::new();
    t.setup();
    let original = bytes(&t);
    let original_inode = fs::metadata(t.store().join(SNAPSHOT)).unwrap().ino();
    let mut r = request("one");
    r.push(b'\n');
    let out = record(&t.store(), &r).unwrap();
    assert_eq!(out.result, "ALREADY_RECORDED");
    assert_eq!(out.record_count, 1);
    assert_eq!(
        out.records[0].first_request_bytes_sha256,
        bytes_sha256(&request("one"))
    );
    assert_eq!(bytes(&t), original);
    assert_eq!(
        fs::metadata(t.store().join(SNAPSHOT)).unwrap().ino(),
        original_inode
    );
    for field in ["simulated_account_id", "limit_price_native", "source"] {
        let mut v: serde_json::Value = serde_json::from_slice(&r).unwrap();
        match field {
            "source" => v[field]["kind"] = "ALGORITHM".into(),
            "limit_price_native" => v[field] = "3".into(),
            _ => v[field] = "another-simulated-account".into(),
        }
        assert_eq!(
            record(&t.store(), &serde_json::to_vec(&v).unwrap()).unwrap_err(),
            StoreError::Conflict
        );
        assert_eq!(bytes(&t), original);
    }
    let out = record(&t.store(), &request("two")).unwrap();
    assert_eq!(out.record_count, 2);
    assert_eq!(out.records[0].sequence, 2);
    let a = replay(&t.store()).unwrap();
    let b = replay(&t.store()).unwrap();
    assert_eq!(canonical_bytes(&a).unwrap(), canonical_bytes(&b).unwrap());
    assert_eq!(
        a.records
            .iter()
            .map(|r| r.intent.request_id.as_str())
            .collect::<Vec<_>>(),
        ["one", "two"]
    );
    assert_eq!(a.risk_assessment, "NOT_ASSESSED");
    assert_eq!(a.execution_authority, "NONE");
    assert!(!a.funds_reserved);
}

#[test]
fn invalid_inputs_do_not_initialize_or_modify_state() {
    let t = Temp::new();
    assert_eq!(
        record(&t.store(), b"{}").unwrap_err(),
        StoreError::InvalidInput
    );
    assert!(!t.store().exists());
    t.setup();
    let old = bytes(&t);
    for bad in [
        b"{}".to_vec(),
        request("bad/id"),
        vec![b' '; intents::MAX_INTENT_BYTES + 1],
    ] {
        assert_eq!(
            record(&t.store(), &bad).unwrap_err(),
            StoreError::InvalidInput
        );
        assert_eq!(bytes(&t), old);
        assert!(!t.store().join(PENDING).exists());
    }
    assert_eq!(
        initialize(&t.store(), "sim-demo").unwrap_err(),
        StoreError::InvalidStore
    );
    assert_eq!(bytes(&t), old);
}

#[test]
fn truncated_corrupt_and_semantically_invalid_snapshots_never_repair_themselves() {
    let t = Temp::new();
    t.setup();
    let original = bytes(&t);
    for case in 0..8 {
        let mut e: Envelope = serde_json::from_slice(&original).unwrap();
        let bad = match case {
            0 => vec![],
            1 => original[..original.len() / 2].to_vec(),
            2 => {
                e.sha256 = "0".repeat(64);
                canonical_bytes(&e).unwrap()
            }
            3 => {
                e.snapshot.records[0].sequence = 2;
                e.sha256 = canonical_hash(&e.snapshot).unwrap();
                canonical_bytes(&e).unwrap()
            }
            4 => {
                e.snapshot.records.push(e.snapshot.records[0].clone());
                e.snapshot.records[1].sequence = 2;
                e.sha256 = canonical_hash(&e.snapshot).unwrap();
                canonical_bytes(&e).unwrap()
            }
            5 => {
                e.snapshot.records[0].intent_sha256 = "0".repeat(64);
                e.sha256 = canonical_hash(&e.snapshot).unwrap();
                canonical_bytes(&e).unwrap()
            }
            6 => {
                let mut b = original.clone();
                b.push(b'\n');
                b
            }
            _ => String::from_utf8(original.clone())
                .unwrap()
                .replace("SIMULATED", "LIVE")
                .into_bytes(),
        };
        fs::write(t.store().join(SNAPSHOT), &bad).unwrap();
        assert_eq!(
            replay(&t.store()).unwrap_err(),
            StoreError::CorruptStore,
            "{case}"
        );
        assert_eq!(
            record(&t.store(), &request("two")).unwrap_err(),
            StoreError::CorruptStore
        );
        assert_eq!(bytes(&t), bad);
        assert!(!t.store().join(PENDING).exists());
    }
    fs::remove_file(t.store().join(SNAPSHOT)).unwrap();
    assert_eq!(
        record(&t.store(), &request("two")).unwrap_err(),
        StoreError::CorruptStore
    );
    assert!(!t.store().join(SNAPSHOT).exists());
}

#[test]
fn write_and_sync_failures_do_not_lie_about_commit_or_duplicate_records() {
    for point in [
        "before_create",
        "temp_created",
        "before_file_sync",
        "file_synced",
        "renamed",
        "directory_synced",
    ] {
        let t = Temp::new();
        t.setup();
        let old = bytes(&t);
        let error = record_with(
            &t.store(),
            &request("two"),
            Hooks {
                fail_at: Some(point),
                observe: None,
            },
        )
        .unwrap_err();
        let committed = matches!(point, "renamed" | "directory_synced");
        assert_eq!(
            error,
            if committed {
                StoreError::DurabilityUnconfirmed
            } else {
                StoreError::WriteFailed
            }
        );
        if !committed {
            assert_eq!(bytes(&t), old);
        }
        assert_eq!(
            replay(&t.store()).unwrap().record_count,
            if committed { 2 } else { 1 }
        );
        let retry = record(&t.store(), &request("two")).unwrap();
        assert_eq!(
            retry.result,
            if committed {
                "ALREADY_RECORDED"
            } else {
                "RECORDED"
            }
        );
        assert_eq!(retry.record_count, 2);
        assert!(!t.store().join(PENDING).exists());
    }
}

#[test]
fn leftover_temporary_bytes_are_never_promoted_and_corrupt_committed_bytes_win() {
    let t = Temp::new();
    t.setup();
    let old = bytes(&t);
    fs::write(t.store().join(PENDING), b"{truncated").unwrap();
    let recovered = replay(&t.store()).unwrap();
    assert_eq!(recovered.record_count, 1);
    assert!(recovered.uncommitted_pending);
    assert_eq!(bytes(&t), old);
    record(&t.store(), &request("two")).unwrap();
    assert!(!t.store().join(PENDING).exists());
    fs::write(t.store().join(PENDING), &old).unwrap();
    fs::write(t.store().join(SNAPSHOT), b"bad").unwrap();
    assert_eq!(replay(&t.store()).unwrap_err(), StoreError::CorruptStore);
    assert_eq!(
        record(&t.store(), &request("three")).unwrap_err(),
        StoreError::CorruptStore
    );
    assert_eq!(bytes(&t), b"bad");
    assert_eq!(fs::read(t.store().join(PENDING)).unwrap(), old);
}

#[test]
fn limits_and_path_guards_fail_without_mutating_existing_state() {
    use std::os::unix::fs::symlink;
    let t = Temp::new();
    t.setup();
    let old = bytes(&t);
    symlink(t.store(), t.0.join("link")).unwrap();
    assert_eq!(
        replay(&t.0.join("link")).unwrap_err(),
        StoreError::InvalidStore
    );
    assert_eq!(
        replay(&t.0.join("store/../store")).unwrap_err(),
        StoreError::InvalidStore
    );
    for root in io::trusted_roots().unwrap() {
        assert_eq!(
            initialize(&root.join("intent-forbidden"), "test").unwrap_err(),
            StoreError::InvalidStore
        );
    }
    symlink(t.0.join("external"), t.store().join(PENDING)).unwrap();
    assert_eq!(
        record(&t.store(), &request("two")).unwrap_err(),
        StoreError::CorruptStore
    );
    assert_eq!(bytes(&t), old);
    assert!(!t.0.join("external").exists());
    fs::remove_file(t.store().join(PENDING)).unwrap();
    fs::hard_link(t.store().join(SNAPSHOT), t.0.join("alias")).unwrap();
    assert_eq!(replay(&t.store()).unwrap_err(), StoreError::CorruptStore);
    fs::remove_file(t.0.join("alias")).unwrap();
    let mut e: Envelope = serde_json::from_slice(&old).unwrap();
    let first = e.snapshot.records[0].clone();
    e.snapshot.records = (0..MAX_RECORDS)
        .map(|n| {
            let mut record = first.clone();
            record.sequence = n as u64 + 1;
            record.intent.request_id = format!("id-{n}");
            record.intent_sha256 = canonical_hash(&record.intent).unwrap();
            record
        })
        .collect();
    e.sha256 = canonical_hash(&e.snapshot).unwrap();
    let full = canonical_bytes(&e).unwrap();
    fs::write(t.store().join(SNAPSHOT), &full).unwrap();
    assert_eq!(
        record(&t.store(), &request("overflow")).unwrap_err(),
        StoreError::Limit
    );
    assert_eq!(bytes(&t), full);
    e.snapshot.records.push(first);
    assert_eq!(
        validate_snapshot(&e.snapshot).unwrap_err(),
        StoreError::CorruptStore
    );
    fs::write(t.store().join(SNAPSHOT), vec![b' '; MAX_STORE_BYTES + 1]).unwrap();
    assert_eq!(replay(&t.store()).unwrap_err(), StoreError::CorruptStore);
}

// Fault control is compiled only into this test binary, never the production CLI.
fn observe(point: &str) {
    if std::env::var("ZQ_TEST_PHASE").ok().as_deref() == Some(point) {
        fs::write(std::env::var_os("ZQ_TEST_MARKER").unwrap(), point).unwrap();
        loop {
            std::thread::park();
        }
    }
}
#[test]
fn crash_worker() {
    let Some(store) = std::env::var_os("ZQ_TEST_STORE") else {
        return;
    };
    let store = PathBuf::from(store);
    match std::env::var("ZQ_TEST_OPERATION").unwrap().as_str() {
        "lock" => {
            let _guard = lock(&store).unwrap();
            observe("locked");
        }
        "init" => {
            initialize_with(
                &store,
                "crash-init",
                Hooks {
                    observe: Some(observe),
                    fail_at: None,
                },
            )
            .unwrap();
        }
        "write_limit" => {
            unsafe {
                libc::signal(libc::SIGXFSZ, libc::SIG_IGN);
                let limit = libc::rlimit {
                    rlim_cur: 128,
                    rlim_max: 128,
                };
                assert_eq!(libc::setrlimit(libc::RLIMIT_FSIZE, &limit), 0);
            }
            assert_eq!(
                record(&store, &request("two")).unwrap_err(),
                StoreError::WriteFailed
            );
        }
        _ => {
            record_with(
                &store,
                &request("two"),
                Hooks {
                    observe: Some(observe),
                    fail_at: None,
                },
            )
            .unwrap();
        }
    }
}
fn worker(t: &Temp, operation: &str, phase: &str) -> std::process::Child {
    Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "intent_store::tests::crash_worker",
            "--nocapture",
        ])
        .env("ZQ_TEST_STORE", t.store())
        .env("ZQ_TEST_OPERATION", operation)
        .env("ZQ_TEST_PHASE", phase)
        .env("ZQ_TEST_MARKER", t.0.join("ready"))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap()
}
fn kill_at_marker(t: &Temp, child: &mut std::process::Child) {
    for _ in 0..500 {
        if t.0.join("ready").exists() {
            child.kill().unwrap();
            child.wait().unwrap();
            return;
        }
        if child.try_wait().unwrap().is_some() {
            panic!("fault child ended before checkpoint");
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    child.kill().unwrap();
    child.wait().unwrap();
    panic!("fault child timeout");
}

#[test]
fn sigkill_before_and_after_rename_recovers_old_or_new_complete_state() {
    for phase in ["temp_created", "file_synced", "renamed", "directory_synced"] {
        let t = Temp::new();
        t.setup();
        let old = bytes(&t);
        let mut child = worker(&t, "record", phase);
        kill_at_marker(&t, &mut child);
        let committed = matches!(phase, "renamed" | "directory_synced");
        let replayed = replay(&t.store()).unwrap();
        assert_eq!(replayed.record_count, if committed { 2 } else { 1 });
        if !committed {
            assert_eq!(bytes(&t), old);
        }
        let retry = record(&t.store(), &request("two")).unwrap();
        assert_eq!(
            retry.result,
            if committed {
                "ALREADY_RECORDED"
            } else {
                "RECORDED"
            }
        );
        assert_eq!(replay(&t.store()).unwrap().record_count, 2);
        println!(
            "SIGKILL {phase}: complete count {}, retry {}, final count 2",
            replayed.record_count, retry.result
        );
    }
}

#[test]
fn process_lock_excludes_readers_and_writers_and_death_releases_it() {
    let t = Temp::new();
    t.setup();
    let old = bytes(&t);
    let mut child = worker(&t, "lock", "locked");
    for _ in 0..500 {
        if t.0.join("ready").exists() {
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    if !t.0.join("ready").exists() {
        child.kill().unwrap();
        child.wait().unwrap();
        panic!("lock timeout");
    }
    assert_eq!(
        record(&t.store(), &request("two")).unwrap_err(),
        StoreError::Busy
    );
    assert_eq!(replay(&t.store()).unwrap_err(), StoreError::Busy);
    assert_eq!(bytes(&t), old);
    child.kill().unwrap();
    child.wait().unwrap();
    assert_eq!(record(&t.store(), &request("two")).unwrap().record_count, 2);
}

#[test]
fn actual_partial_write_failure_preserves_committed_snapshot() {
    let t = Temp::new();
    t.setup();
    let old = bytes(&t);
    let output = worker(&t, "write_limit", "").wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(bytes(&t), old);
    assert_eq!(fs::metadata(t.store().join(PENDING)).unwrap().len(), 128);
    assert_eq!(replay(&t.store()).unwrap().record_count, 1);
    assert_eq!(record(&t.store(), &request("two")).unwrap().record_count, 2);
    println!(
        "RLIMIT_FSIZE: actual 128-byte partial temporary write; old snapshot intact; retry count 2"
    );
}

#[test]
fn interrupted_initialization_is_not_implicitly_reset() {
    let t = Temp::new();
    let mut child = worker(&t, "init", "temp_created");
    kill_at_marker(&t, &mut child);
    assert_eq!(replay(&t.store()).unwrap_err(), StoreError::CorruptStore);
    assert_eq!(
        record(&t.store(), &request("one")).unwrap_err(),
        StoreError::CorruptStore
    );
    assert_eq!(
        initialize(&t.store(), "new").unwrap_err(),
        StoreError::InvalidStore
    );
    assert!(!t.store().join(SNAPSHOT).exists());
}
