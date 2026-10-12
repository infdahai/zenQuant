use options_workbench::{curve_http, paper_store::Store};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    sync::atomic::{AtomicUsize, Ordering},
};
struct Temp {
    root: std::path::PathBuf,
    store: Store,
}
impl Temp {
    fn new() -> Self {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let root = std::env::temp_dir().join(format!(
            "paper-http-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&root).unwrap();
        let store = Store::engineering(root.join("journal"));
        Self { root, store }
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}
fn exchange(store: &Store, path: &str, body: &Value, origin: bool) -> (u16, Value) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let host = listener.local_addr().unwrap().to_string();
    let data = serde_json::to_string(body).unwrap();
    let request = format!(
        "POST {path} HTTP/1.1\r\nHost: {host}\r\nOrigin: http://{}\r\nX-ZenQuant-Request: 1\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{data}",
        if origin {
            host.as_str()
        } else {
            "evil.example"
        },
        data.len()
    );
    let owned = store.clone();
    let h = host.clone();
    let server = std::thread::spawn(move || {
        curve_http::handle_with_paper(
            &mut listener.accept().unwrap().0,
            &h,
            &BTreeMap::new(),
            Some(&owned),
        )
        .unwrap()
    });
    let mut client = TcpStream::connect(host).unwrap();
    client.write_all(request.as_bytes()).unwrap();
    let mut out = String::new();
    client.read_to_string(&mut out).unwrap();
    server.join().unwrap();
    (
        out.split_whitespace().nth(1).unwrap().parse().unwrap(),
        serde_json::from_str(out.split("\r\n\r\n").nth(1).unwrap()).unwrap(),
    )
}
#[test]
fn actual_http_records_withdraws_replays_without_execution_or_capital_duplication() {
    let t = Temp::new();
    let c: Value =
        serde_json::from_slice(include_bytes!("../fixtures/manual-paper-settings.json")).unwrap();
    let record: Value =
        serde_json::from_slice(include_bytes!("../fixtures/manual-paper-intent.json")).unwrap();
    let (status, state) = exchange(&t.store, "/api/paper/state", &json!({}), true);
    assert_eq!(status, 200);
    assert!(state["view"]["settings"].is_null());
    let (status, p) = exchange(&t.store, "/api/paper/preview", &c, true);
    assert_eq!(status, 200);
    assert!(!t.root.join("journal").exists());
    let confirmation = json!({"preview":p["preview"],"acknowledged_record_only":true});
    assert_eq!(
        exchange(&t.store, "/api/paper/confirm", &confirmation, false).0,
        400
    );
    assert!(!t.root.join("journal").exists());
    assert_eq!(
        exchange(&t.store, "/api/paper/confirm", &confirmation, true).0,
        200
    );
    let (status, p) = exchange(&t.store, "/api/paper/preview", &record, true);
    assert_eq!(status, 200);
    assert!(
        p["blockers"]
            .as_array()
            .unwrap()
            .contains(&json!("MARGIN_MISSING"))
    );
    let confirmation = json!({"preview":p["preview"],"acknowledged_record_only":true});
    let (status, r) = exchange(&t.store, "/api/paper/confirm", &confirmation, true);
    assert_eq!(status, 200);
    assert_eq!(r["view"]["execution_authority"], "NONE");
    let first = &r["view"]["intents"][0];
    let w = json!({"schema_version":"manual-paper-command.rust.v1","request_id":"withdraw-http","action":{"kind":"WITHDRAW_INTENT","value":{"intent_id":first["intent"]["intent_id"],"intent_sha256":first["intent_sha256"]}}});
    let (_, p) = exchange(&t.store, "/api/paper/preview", &w, true);
    assert_eq!(
        exchange(
            &t.store,
            "/api/paper/confirm",
            &json!({"preview":p["preview"],"acknowledged_record_only":true}),
            true
        )
        .0,
        200
    );
    let reopened = Store::engineering(t.root.join("journal"));
    let (status, r) = exchange(&reopened, "/api/paper/confirm", &confirmation, true);
    assert_eq!(status, 200);
    assert_eq!(r["replayed"], true);
    assert_eq!(r["view"]["revision"], 3);
    assert_eq!(r["view"]["intents"][0]["status"], "WITHDRAWN_RECORD_ONLY");
    assert_eq!(r["view"]["unallocated_usd"], "10000");
    assert_eq!(
        exchange(
            &reopened,
            "/api/paper/state",
            &json!({"path":"/etc/passwd"}),
            true
        )
        .0,
        422
    );
}
