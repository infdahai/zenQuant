use options_workbench::{curve_api, curve_http};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    thread,
};
fn exchange(make: impl FnOnce(&str) -> String) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let host = listener.local_addr().unwrap().to_string();
    let request = make(&host);
    let h = host.clone();
    let server = thread::spawn(move || {
        curve_http::handle(listener.accept().unwrap().0, &h, &BTreeMap::new()).unwrap()
    });
    let mut client = TcpStream::connect(host).unwrap();
    client.write_all(request.as_bytes()).unwrap();
    let mut out = String::new();
    client.read_to_string(&mut out).unwrap();
    server.join().unwrap();
    out
}
fn post(host: &str, body: &str) -> String {
    format!(
        "POST /api/curve HTTP/1.1\r\nHost: {host}\r\nOrigin: http://{host}\r\nX-ZenQuant-Request: 1\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
        body.len()
    )
}
#[test]
fn batch_api_preserves_exact_times_and_missing_evidence_semantics() {
    let mut r = options_core::fixtures::mathematical_curve().unwrap();
    r.grid.as_mut().unwrap().count = 2;
    let body = serde_json::to_string(&r).unwrap();
    let out = exchange(|h| post(h, &body));
    assert!(out.starts_with("HTTP/1.1 200 OK"));
    let response: serde_json::Value =
        serde_json::from_str(out.split("\r\n\r\n").nth(1).unwrap()).unwrap();
    assert_eq!(response["report"]["points"].as_array().unwrap().len(), 2);
    r.candidate = None;
    r.grid = None;
    r.cutoff_ns = 1_900_000_000_000_000_001;
    let bytes = curve_api::evaluate("/api/curve", &serde_json::to_vec(&r).unwrap()).unwrap();
    let response: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert!(
        response["request_json"]
            .as_str()
            .unwrap()
            .contains("1900000000000000001")
    );
    assert_eq!(response["report"]["missing"].as_array().unwrap().len(), 2);
}
#[test]
fn server_rejects_cross_origin_rebinding_ambiguous_length_and_oversize() {
    for change in 0..6 {
        let out = exchange(|h| {
            let valid = post(h, "{}");
            match change {
                0 => valid.replace(
                    &format!("Origin: http://{h}"),
                    "Origin: https://evil.example",
                ),
                1 => valid.replace(&format!("Host: {h}"), "Host: evil.example"),
                2 => valid.replace(
                    "Content-Length: 2",
                    "Content-Length: 2\r\nContent-Length: 2",
                ),
                3 => valid.replace(
                    "Content-Length: 2",
                    "Transfer-Encoding: chunked\r\nContent-Length: 2",
                ),
                4 => valid.replace("Content-Length: 2", "Content-Length: 999999999"),
                _ => valid.replace("X-ZenQuant-Request: 1\r\n", ""),
            }
        });
        assert!(
            out.starts_with("HTTP/1.1 400 Bad Request"),
            "{change}: {out}"
        );
    }
}
#[test]
fn api_errors_are_typed_and_no_arbitrary_file_route_exists() {
    assert!(exchange(|h| post(h, "{\"unknown\":1}")).starts_with("HTTP/1.1 422"));
    assert!(
        exchange(|h| format!("GET /../../.env HTTP/1.1\r\nHost: {h}\r\n\r\n"))
            .starts_with("HTTP/1.1 404")
    );
}

#[test]
fn synthetic_controls_supply_visible_two_sided_quote_without_bypassing_cost_validation() {
    let mut p = serde_json::json!({"option_type":"C","strike":"100","quantity":".01","ask":"2","bid":"1.9","fee_rate":".0003","terminal_fee":".001","first":"50","step":".1","count":2,"signal_hypothesis":null,"signal_known_ns":"10","signal_valid_until_ns":"1000"});
    let evaluate = |p: &serde_json::Value| -> serde_json::Value {
        serde_json::from_slice(
            &curve_api::evaluate("/api/synthetic", &serde_json::to_vec(p).unwrap()).unwrap(),
        )
        .unwrap()
    };
    let response = evaluate(&p);
    assert_eq!(
        response["report"]["points"][0]["conditional_model_pnl"],
        "-0.0213"
    );
    assert_eq!(
        response["cargo_lock_sha256"],
        options_core::canonical::bytes_sha256(include_bytes!("../../Cargo.lock"))
    );
    p["fee_rate"] = serde_json::Value::Null;
    assert!(evaluate(&p)["report"]["points"][0]["conditional_model_pnl"].is_null());
    p["bid"] = "3".into();
    assert_eq!(
        curve_api::evaluate("/api/synthetic", &serde_json::to_vec(&p).unwrap())
            .unwrap_err()
            .code,
        options_core::error::ErrorCode::ConflictingEvidence
    );
}
