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

#[test]
fn vix_http_uses_real_core_and_preserves_full_audit_identity() {
    let body = "{\"scenario\":\"BACKWARDATION\"}";
    let out = exchange(|h| post(h, body).replace("/api/curve", "/api/vix/demo"));
    assert!(out.starts_with("HTTP/1.1 200 OK"));
    let response: serde_json::Value =
        serde_json::from_str(out.split("\r\n\r\n").nth(1).unwrap()).unwrap();
    assert_eq!(
        response["report"]["futures"]["metrics"]["spread_points"],
        "4"
    );
    assert_eq!(
        response["report"]["spot"]["metrics"]["classification"],
        "SHORT_HORIZON_HIGHER"
    );
    assert_eq!(response["report"]["mode"], "DEMONSTRATION");
    let request = response["request_json"].as_str().unwrap();
    let parsed = options_core::vix_observation::decode_request(request.as_bytes()).unwrap();
    assert_eq!(
        response["report"]["input_sha256"],
        options_core::canonical::canonical_hash(&parsed).unwrap()
    );
    assert_eq!(
        response["transport_sha256"],
        options_core::canonical::bytes_sha256(body.as_bytes())
    );
    assert_eq!(
        response["cargo_lock_sha256"],
        options_core::canonical::bytes_sha256(include_bytes!("../../Cargo.lock"))
    );
    assert_eq!(response["code_sha256"].as_str().unwrap().len(), 64);
    let evaluated: serde_json::Value = serde_json::from_slice(
        &options_workbench::vix_api::evaluate("/api/vix/evaluate", request.as_bytes()).unwrap(),
    )
    .unwrap();
    assert_eq!(response["report"], evaluated["report"]);
}

#[test]
fn vix_demo_failure_scenarios_preserve_unavailable_and_roll_semantics() {
    for (scenario, status, classification) in [
        ("BACKWARDATION", "READY", Some("BACKWARDATION")),
        ("CONTANGO", "READY", Some("CONTANGO")),
        ("FLAT", "READY", Some("FLAT")),
        ("MISSING", "MISSING", None),
        ("STALE", "STALE", None),
        ("ASYNCHRONOUS", "INVALID", None),
        ("ROLL", "READY", Some("CONTANGO")),
    ] {
        let body = serde_json::to_vec(&serde_json::json!({"scenario":scenario})).unwrap();
        let response: serde_json::Value = serde_json::from_slice(
            &options_workbench::vix_api::evaluate("/api/vix/demo", &body).unwrap(),
        )
        .unwrap();
        let f = &response["report"]["futures"];
        assert_eq!(f["status"], status);
        assert_eq!(f["metrics"]["classification"].as_str(), classification);
        if classification.is_none() {
            assert!(f["metrics"].is_null());
        }
        if scenario == "ROLL" {
            assert_eq!(f["roll"], "CHANGED_FROM_SUPPLIED_PAIR");
            assert_eq!(f["selected_contracts"][0]["symbol"], "VX/X6");
        }
    }
}

#[test]
fn vix_endpoint_rejects_unknown_fields_paths_cross_origin_and_missing_header() {
    for body in [
        "{}",
        "{\"scenario\":\"LIVE\"}",
        "{\"scenario\":\"FLAT\",\"path\":\"/tmp/anything\"}",
        "{\"scenario\":\"FLAT\",\"scenario\":\"MISSING\"}",
    ] {
        assert!(
            exchange(|h| post(h, body).replace("/api/curve", "/api/vix/demo"))
                .starts_with("HTTP/1.1 422")
        );
    }
    for case in 0..2 {
        let out = exchange(|h| {
            let request = post(h, "{}").replace("/api/curve", "/api/vix/evaluate");
            if case == 0 {
                request.replace(
                    &format!("Origin: http://{h}"),
                    "Origin: https://evil.example",
                )
            } else {
                request.replace("X-ZenQuant-Request: 1\r\n", "")
            }
        });
        assert!(out.starts_with("HTTP/1.1 400"));
    }
    assert!(
        exchange(|h| post(h, "{}").replace("/api/curve", "/api/vix/live"))
            .starts_with("HTTP/1.1 404")
    );
}
