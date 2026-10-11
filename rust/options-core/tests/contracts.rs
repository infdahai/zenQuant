use options_core::{
    canonical::{canonical_bytes, canonical_hash},
    contracts::*,
    decimal::Dec,
    error::ErrorCode,
    fixtures::*,
};
fn d(s: &str) -> Dec {
    Dec::parse(s).unwrap()
}
fn policy() -> RiskPolicy {
    RiskPolicy {
        per_intent_report: d("10"),
        active_and_pending_report: d("50"),
        rolling_tail_paid_report: d("20"),
        rolling_window_ns: 2592000000000000,
        report_currency: "USDC".into(),
        fx_timing: None,
    }
}
#[test]
fn venue_and_rules_bind_identity() {
    let spec = mathematical_spec(OptionType::C, d("100"));
    let a = InstrumentDescriptor::from_spec(&spec).unwrap();
    let mut other = spec.clone();
    other.venue = "OTHER".into();
    let b = InstrumentDescriptor::from_spec(&other).unwrap();
    assert_ne!(a.spec_hash, b.spec_hash);
    other = spec;
    other.rule_version = "different".into();
    assert_ne!(
        a.spec_hash,
        InstrumentDescriptor::from_spec(&other).unwrap().spec_hash
    );
}
#[test]
fn timestamp_and_budget_are_validated() {
    let mut v = mathematical_view(d("10"), d("10")).unwrap();
    v.meta.available_ns = 9;
    assert_eq!(v.validate().unwrap_err().code, ErrorCode::InvalidTimestamp);
    v.meta.available_ns = 10;
    v.horizon_ns = 10;
    assert!(v.validate().is_err());
    assert!(mathematical_view(d("1"), d("1.01")).is_err());
    v.horizon_ns = 1000;
    v.meta.parent_hash = Some("a".repeat(64));
    assert!(v.validate().is_err());
}
#[test]
fn request_roundtrip_unknown_and_scalar_rejection() {
    let request =
        mathematical_request("terminal-eth-call", d("10"), d("10"), vec![], policy()).unwrap();
    let bytes = canonical_bytes(&request).unwrap();
    assert_eq!(serde_json::from_slice::<Request>(&bytes).unwrap(), request);
    let mut value = serde_json::to_value(&request).unwrap();
    value["execution_authority"] = serde_json::json!("LIVE");
    assert!(serde_json::from_value::<Request>(value).is_err());
    let mut value = serde_json::to_value(&request).unwrap();
    value["cutoff_ns"] = serde_json::json!(true);
    assert!(serde_json::from_value::<Request>(value).is_err());
    let mut value = serde_json::to_value(&request).unwrap();
    value["views"][0]["initial_capital"] = serde_json::json!(1.0);
    assert!(serde_json::from_value::<Request>(value).is_err());
    let mut value = serde_json::to_value(&request).unwrap();
    value["quotes"][0]["book"]["spec"]["hidden"] = serde_json::json!(0);
    assert!(serde_json::from_value::<Request>(value).is_err());
}
#[test]
fn collection_limits_and_binding() {
    let mut r =
        mathematical_request("terminal-eth-call", d("10"), d("10"), vec![], policy()).unwrap();
    assert!(r.validate_limits().is_ok());
    assert_eq!(r.scenarios.view_hash, canonical_hash(&r.views[0]).unwrap());
    r.views = vec![r.views[0].clone(); 129];
    assert_eq!(
        r.validate_limits().unwrap_err().code,
        ErrorCode::LimitExceeded
    );
}
#[test]
fn canonical_keys_and_equivalent_decimals() {
    let a = serde_json::json!({"z":{"b":2,"a":1},"a":"0.01"});
    let b = serde_json::json!({"a":"0.01","z":{"a":1,"b":2}});
    assert_eq!(canonical_hash(&a).unwrap(), canonical_hash(&b).unwrap());
    assert_eq!(
        canonical_hash(&d(".01")).unwrap(),
        canonical_hash(&d("0.0100")).unwrap()
    );
    assert_eq!(
        String::from_utf8(canonical_bytes(&a).unwrap()).unwrap(),
        "{\"a\":\"0.01\",\"z\":{\"a\":1,\"b\":2}}"
    );
}
#[test]
fn canonical_rejects_unbounded_and_floating_payloads() {
    assert!(canonical_hash(&serde_json::json!({"value":0.25})).is_err());
    assert!(canonical_hash(&"x".repeat(MAX_STRING_BYTES + 1)).is_err());
    let mut nested = serde_json::json!(0);
    for _ in 0..MAX_JSON_DEPTH + 1 {
        nested = serde_json::json!([nested]);
    }
    assert!(canonical_bytes(&nested).is_err());
}
#[test]
fn generic_serialization_rejects_nested_nonfinite_before_json_conversion() {
    #[derive(serde::Serialize)]
    struct Nested {
        values: Vec<Option<f64>>,
    }
    for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 0.25] {
        assert!(
            canonical_hash(&Nested {
                values: vec![Some(value)]
            })
            .is_err()
        );
    }
    assert!(canonical_bytes(&vec![Some(f32::NAN)]).is_err());
}
