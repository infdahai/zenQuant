use options_core::{
    canonical::canonical_hash,
    contracts::*,
    decimal::Dec,
    intents::{self, DecisionKind, Intent},
};

fn request() -> Intent {
    intents::decode_request(include_bytes!(
        "../../options-workbench/fixtures/simulated-intent.json"
    ))
    .unwrap()
}
fn rebind(r: &mut Intent) {
    r.leg.instrument = InstrumentDescriptor::from_spec(&r.spec).unwrap();
}

#[test]
fn manual_algorithm_and_decimal_equivalence_remain_simulation_requests() {
    let manual = request();
    assert_eq!(manual.mode, intents::Mode::Simulated);
    let mut algorithm = manual.clone();
    algorithm.source.kind = DecisionKind::Algorithm;
    intents::validate(&algorithm).unwrap();
    assert_ne!(
        canonical_hash(&manual).unwrap(),
        canonical_hash(&algorithm).unwrap()
    );
    let json = serde_json::to_string(&manual).unwrap().replace(
        "\"limit_price_native\":\"2\"",
        "\"limit_price_native\":\"2.00\"",
    );
    assert_eq!(intents::decode_request(json.as_bytes()).unwrap(), manual);
}

#[test]
fn invalid_evidence_identity_and_chronology_reject_in_typed_and_json_boundaries() {
    for case in 0..11 {
        let mut r = request();
        match case {
            0 => r.request_id = "bad/id".into(),
            1 => r.simulated_account_id.clear(),
            2 => r.source.evidence_sha256 = "bad".into(),
            3 => r.source.evidence_available_ns = 11,
            4 => r.metadata_known_ns = 11,
            5 => r.evidence_cutoff_ns = 11,
            6 => r.expires_ns = r.created_ns,
            7 => r.expires_ns = r.spec.expiry_ns + 1,
            8 => {
                r.spec.valid_until_ns = Some(998);
                rebind(&mut r);
            }
            9 => r.leg.instrument.spec_hash = "0".repeat(64),
            _ => r.source.source_id = " ".into(),
        }
        assert!(intents::validate(&r).is_err(), "typed {case}");
        assert!(
            intents::decode_request(&serde_json::to_vec(&r).unwrap()).is_err(),
            "json {case}"
        );
    }
}

#[test]
fn first_contract_limits_product_side_quantity_and_price_without_risk_claims() {
    for case in 0..8 {
        let mut r = request();
        match case {
            0 => r.leg.side = Side::Sell,
            1 => r.spec.product = Product::InverseOption,
            2 => r.spec.exercise_style = ExerciseStyle::American,
            3 => r.spec.settlement_rule = SettlementRule::Unknown,
            4 => r.leg.quantity_native = Dec::parse(".015").unwrap(),
            5 => r.limit_price_native = Dec::zero(),
            6 => r.limit_price_native = Dec::parse("2.001").unwrap(),
            _ => r.spec.price_unit = PriceUnit::QuotePerContract,
        }
        rebind(&mut r);
        assert!(intents::validate(&r).is_err(), "{case}");
    }
    let mut r = request();
    r.spec.quantity_unit = QuantityUnit::Contracts;
    r.spec.multiplier = Dec::parse("10").unwrap();
    r.spec.price_unit = PriceUnit::QuotePerContract;
    rebind(&mut r);
    intents::validate(&r).unwrap();
}

#[test]
fn live_mode_authority_unknown_fields_diagnostic_requests_and_bad_json_reject() {
    for field in [
        "execution_authority",
        "risk_approved",
        "diagnostic_only",
        "view",
    ] {
        let mut v = serde_json::to_value(request()).unwrap();
        v[field] = true.into();
        assert!(intents::decode_request(&serde_json::to_vec(&v).unwrap()).is_err());
    }
    let json = serde_json::to_string(&request()).unwrap();
    for bad in [
        json.replace("SIMULATED", "LIVE"),
        json.replace(intents::REQUEST_SCHEMA, "future"),
        json.replace("\"created_ns\":10", "\"created_ns\":10,\"created_ns\":10"),
        json.replace("\"created_ns\":10", "\"created_ns\":true"),
        json.replace("\"limit_price_native\":\"2\"", "\"limit_price_native\":2.0"),
        json.replace(
            "\"limit_price_native\":\"2\"",
            &format!("\"limit_price_native\":\"{}\"", "9".repeat(65)),
        ),
        format!("{json} {{}}"),
    ] {
        assert_ne!(bad, json);
        assert!(intents::decode_request(bad.as_bytes()).is_err());
    }
    for bytes in [
        include_bytes!("../../options-workbench/fixtures/terminal-call-scenarios.json").as_slice(),
        include_bytes!("../../options-workbench/fixtures/quote-costs.json").as_slice(),
    ] {
        assert!(intents::decode_request(bytes).is_err());
    }
    assert!(intents::decode_request(&vec![b' '; intents::MAX_INTENT_BYTES + 1]).is_err());
}
