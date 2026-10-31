use super::*;
use serde_json::{Value, json};
use std::{
    fs,
    sync::{
        Arc, Barrier,
        atomic::{AtomicUsize, Ordering},
    },
};
const NOW: u64 = 1_800_000_000_000;
struct Temp {
    root: PathBuf,
    store: Store,
}
impl Temp {
    fn new() -> Self {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let root = std::env::temp_dir().join(format!(
            "paper-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        let store = Store::engineering(root.join("journal"));
        Self { root, store }
    }
    fn call(&self, path: &str, v: &Value, now: u64, hooks: Hooks) -> Result<Value> {
        serde_json::from_slice(&self.store.api_at(
            path,
            &serde_json::to_vec(v).unwrap(),
            now,
            hooks,
        )?)
        .map_err(err)
    }
    fn preview(&self, v: &Value) -> Value {
        self.call("/api/paper/preview", v, NOW, Hooks::default())
            .unwrap()["preview"]
            .clone()
    }
    fn commit(&self, p: Value) -> Value {
        self.call(
            "/api/paper/confirm",
            &json!({"preview":p,"acknowledged_record_only":true}),
            NOW,
            Hooks::default(),
        )
        .unwrap()
    }
    fn init(&self) {
        self.commit(self.preview(&config()));
    }
    fn state(&self) -> Value {
        self.call("/api/paper/state", &json!({}), NOW, Hooks::default())
            .unwrap()
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}
fn config() -> Value {
    serde_json::from_slice(include_bytes!("../fixtures/manual-paper-settings.json")).unwrap()
}
fn intent() -> Value {
    serde_json::from_slice(include_bytes!("../fixtures/manual-paper-intent.json")).unwrap()
}
#[test]
fn preview_is_disk_read_only_and_reopen_replay_is_exact() {
    let t = Temp::new();
    assert!(t.state()["view"]["settings"].is_null());
    let p = t.preview(&config());
    assert!(!t.store.path.exists());
    t.commit(p);
    let before = fs::read(t.store.path.join("snapshot.json")).unwrap();
    let p = t.preview(&intent());
    assert_eq!(
        fs::read(t.store.path.join("snapshot.json")).unwrap(),
        before
    );
    let r = t.commit(p.clone());
    assert_eq!(r["view"]["intents"][0]["status"], "RECORDED_UNASSESSED");
    assert_eq!(r["storage_scope"], "ENGINEERING_TEST_ONLY");
    let bytes = fs::read(t.store.path.join("snapshot.json")).unwrap();
    assert_eq!(t.commit(p)["replayed"], true);
    assert_eq!(fs::read(t.store.path.join("snapshot.json")).unwrap(), bytes);
    assert_eq!(t.state()["view"], r["view"]);
}
#[test]
fn failed_and_uncertain_writes_reopen_to_one_whole_revision() {
    for point in [
        "before_create",
        "temp_created",
        "before_file_sync",
        "file_synced",
        "renamed",
        "directory_synced",
    ] {
        let t = Temp::new();
        t.init();
        let p = t.preview(&intent());
        let c = json!({"preview":p,"acknowledged_record_only":true});
        assert!(
            t.call(
                "/api/paper/confirm",
                &c,
                NOW,
                Hooks {
                    fail_at: Some(point),
                    observe: None
                }
            )
            .is_err()
        );
        let committed = matches!(point, "renamed" | "directory_synced");
        let v = t.state();
        assert_eq!(v["view"]["revision"], if committed { 2 } else { 1 });
        let r = t
            .call("/api/paper/confirm", &c, NOW, Hooks::default())
            .unwrap();
        assert_eq!(r["replayed"], committed);
        assert_eq!(r["view"]["revision"], 2);
        assert_eq!(r["view"]["unallocated_usd"], "10000");
        assert_eq!(r["view"]["intents"].as_array().unwrap().len(), 1);
    }
}
#[test]
fn missing_or_corrupt_journal_never_reinitializes_budget() {
    let t = Temp::new();
    t.init();
    fs::write(t.store.path.join("snapshot.json"), b"{}").unwrap();
    assert!(t.store.read().is_err());
    assert!(
        t.store
            .api_at(
                "/api/paper/preview",
                &serde_json::to_vec(&config()).unwrap(),
                NOW,
                Hooks::default()
            )
            .is_err()
    );
    fs::remove_file(t.store.path.join("snapshot.json")).unwrap();
    assert!(t.store.read().is_err());
    let t = Temp::new();
    let p = t.preview(&config());
    assert!(
        t.call(
            "/api/paper/confirm",
            &json!({"preview":p,"acknowledged_record_only":true}),
            NOW,
            Hooks {
                fail_at: Some("file_synced"),
                observe: None
            }
        )
        .is_err()
    );
    assert!(t.store.read().is_ok());
    t.commit(p);
}
#[test]
fn unsafe_existing_permissions_are_rejected() {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    for target in ["parent", "journal", "snapshot"] {
        let t = Temp::new();
        t.init();
        assert_eq!(fs::metadata(&t.store.path).unwrap().mode() & 0o077, 0);
        let path = match target {
            "parent" => t.root.clone(),
            "journal" => t.store.path.clone(),
            _ => t.store.path.join("snapshot.json"),
        };
        fs::set_permissions(&path, fs::Permissions::from_mode(0o777)).unwrap();
        assert!(t.store.read().is_err(), "{target}");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
    }
}
#[test]
fn first_initialization_failures_are_retryable() {
    for point in [
        "before_create",
        "temp_created",
        "before_file_sync",
        "file_synced",
        "renamed",
        "directory_synced",
        "before_publish",
        "published",
        "parent_synced",
    ] {
        let t = Temp::new();
        let c = json!({"preview":t.preview(&config()),"acknowledged_record_only":true});
        assert!(
            t.call(
                "/api/paper/confirm",
                &c,
                NOW,
                Hooks {
                    fail_at: Some(point),
                    observe: None
                }
            )
            .is_err()
        );
        let r = t
            .call("/api/paper/confirm", &c, NOW, Hooks::default())
            .unwrap();
        assert_eq!(
            r["replayed"],
            matches!(point, "published" | "parent_synced"),
            "{point}"
        );
        assert_eq!(r["view"]["revision"], 1);
    }
}
#[test]
fn two_concurrent_confirmations_cannot_allocate_twice() {
    let t = Temp::new();
    t.init();
    let mut a = config();
    a["request_id"] = "reallocation".into();
    let mut b = a.clone();
    b["request_id"] = "conflicting-reallocation".into();
    b["action"]["value"]["deribit_usdc"]["allocation_usd"] = "50000".into();
    let previews = [t.preview(&a), t.preview(&b)];
    let barrier = Arc::new(Barrier::new(2));
    let handles: Vec<_> = previews
        .into_iter()
        .map(|p| {
            let store = t.store.clone();
            let barrier = Arc::clone(&barrier);
            std::thread::spawn(move || {
                barrier.wait();
                store.api_at(
                    "/api/paper/confirm",
                    &serde_json::to_vec(&json!({"preview":p,"acknowledged_record_only":true}))
                        .unwrap(),
                    NOW,
                    Hooks::default(),
                )
            })
        })
        .collect();
    let results: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    assert_eq!(results.iter().filter(|r| r.is_ok()).count(), 1);
    assert_eq!(t.state()["view"]["revision"], 2);
}
#[test]
fn ambiguous_json_and_file_links_are_rejected() {
    let t = Temp::new();
    assert!(
        t.store
            .api_at(
                "/api/paper/state",
                b"{\"path\":\"/etc/passwd\"}",
                NOW,
                Hooks::default()
            )
            .is_err()
    );
    assert!(
        t.store
            .api_at(
                "/api/paper/preview",
                b"{\"request_id\":\"a\",\"request_id\":\"a\"}",
                NOW,
                Hooks::default()
            )
            .is_err()
    );
    t.init();
    let path = t.store.path.join("snapshot.json");
    fs::rename(&path, t.root.join("saved")).unwrap();
    std::os::unix::fs::symlink(t.root.join("saved"), &path).unwrap();
    assert!(t.store.read().is_err());
}

fn crash_checkpoint(point: &str) {
    if std::env::var("ZQ_PAPER_CRASH_PHASE").ok().as_deref() == Some(point) {
        fs::write(std::env::var_os("ZQ_PAPER_CRASH_MARKER").unwrap(), point).unwrap();
        if let Some(release) = std::env::var_os("ZQ_PAPER_RELEASE") {
            while !Path::new(&release).exists() {
                std::thread::sleep(std::time::Duration::from_millis(5));
            }
        } else {
            loop {
                std::thread::park();
            }
        }
    }
}
#[test]
fn process_worker() {
    let Some(path) = std::env::var_os("ZQ_PAPER_CRASH_STORE") else {
        return;
    };
    let store = Store::engineering(path.into());
    let bytes = fs::read(std::env::var_os("ZQ_PAPER_CRASH_INPUT").unwrap()).unwrap();
    let result = store.api_at(
        "/api/paper/confirm",
        &bytes,
        NOW,
        Hooks {
            fail_at: None,
            observe: Some(crash_checkpoint),
        },
    );
    if let Some(output) = std::env::var_os("ZQ_PAPER_RESULT") {
        fs::write(output, result.as_ref().unwrap()).unwrap();
    }
    assert!(result.is_ok(), "{result:?}");
}
#[test]
fn sigkill_before_and_after_rename_preserves_allocation_and_idempotency() {
    for phase in ["temp_created", "file_synced", "renamed", "directory_synced"] {
        let t = Temp::new();
        t.init();
        let c = json!({"preview":t.preview(&intent()),"acknowledged_record_only":true});
        let input = t.root.join("confirmation.json");
        fs::write(&input, serde_json::to_vec(&c).unwrap()).unwrap();
        let marker = t.root.join("ready");
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "paper_store::tests::process_worker",
                "--nocapture",
            ])
            .env("ZQ_PAPER_CRASH_STORE", &t.store.path)
            .env("ZQ_PAPER_CRASH_INPUT", input)
            .env("ZQ_PAPER_CRASH_PHASE", phase)
            .env("ZQ_PAPER_CRASH_MARKER", &marker)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !marker.exists() && std::time::Instant::now() < deadline {
            assert!(child.try_wait().unwrap().is_none());
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        child.kill().unwrap();
        child.wait().unwrap();
        assert!(marker.exists(), "checkpoint {phase}");
        let committed = matches!(phase, "renamed" | "directory_synced");
        assert_eq!(t.state()["view"]["revision"], if committed { 2 } else { 1 });
        let r = t
            .call("/api/paper/confirm", &c, NOW, Hooks::default())
            .unwrap();
        assert_eq!(r["replayed"], committed);
        assert_eq!(r["view"]["unallocated_usd"], "10000");
        assert_eq!(r["view"]["intents"].as_array().unwrap().len(), 1);
    }
}

#[test]
fn confirmation_reads_clock_under_lock_and_rechecks_deadline() {
    let t = Temp::new();
    t.init();
    let p = t.preview(&intent());
    let c: Confirmation = codec::decode(
        &serde_json::to_vec(&json!({"preview":p,"acknowledged_record_only":true})).unwrap(),
        100000,
    )
    .unwrap();
    let clock = || {
        assert!(matches!(
            disk::lock(&t.store.path, false),
            Err(disk::StoreError::Busy)
        ));
        Ok(c.preview.expires_ms)
    };
    assert_eq!(
        t.store.confirm(&c, &clock, Hooks::default()).unwrap_err(),
        "StalePreview"
    );
    assert_eq!(t.state()["view"]["revision"], 1);
}
