use options_core::{
    canonical::canonical_hash,
    contracts::*,
    decimal::Dec,
    fixtures::mathematical_reservation_case,
    reservations::{self as risk, EvaluationFailure as E, Funding, Request},
};
fn d(s: &str) -> Dec {
    Dec::parse(s).unwrap()
}
fn case() -> (Funding, Request) {
    mathematical_reservation_case(100, d("1")).unwrap()
}
fn bind(f: &Funding, r: &mut Request) {
    r.intent.leg.instrument = InstrumentDescriptor::from_spec(&r.intent.spec).unwrap();
    r.intent_sha256 = canonical_hash(&r.intent).unwrap();
    r.funding_sha256 = canonical_hash(f).unwrap();
}

#[test]
fn hand_oracles_cover_native_contract_units_and_conservative_fee_cap() {
    for (contracts, per_contract, premium, fee, total) in [
        (false, false, ".02", ".0025", ".0225"),
        (true, false, ".2", ".025", ".225"),
        (true, true, ".02", ".0025", ".0225"),
    ] {
        let (f, mut r) = case();
        if contracts {
            r.intent.spec.quantity_unit = QuantityUnit::Contracts;
            r.intent.spec.multiplier = d("10");
        }
        if per_contract {
            r.intent.spec.price_unit = PriceUnit::QuotePerContract;
        }
        bind(&f, &mut r);
        let b = risk::evaluate(&f, &r, 101).unwrap();
        assert_eq!(
            (b.premium, b.opening_fee, b.total),
            (d(premium), d(fee), d(total))
        );
    }
}
#[test]
fn conservation_is_exact_without_epsilon_and_rejects_overbooking() {
    let (available, held) = risk::checked_remaining(&d(".045"), [d(".0225"), d(".0225")]).unwrap();
    assert_eq!(available, Dec::zero());
    assert_eq!(held, d(".045"));
    assert!(
        risk::checked_remaining(
            &d(".0449999999999999999999999999"),
            [d(".0225"), d(".0225")]
        )
        .is_err()
    );
    assert!(risk::checked_remaining(&d("1"), [d("-.01")]).is_err());
}
#[test]
fn actual_evaluation_time_checks_expiry_freshness_and_fee_coverage() {
    let (f, r) = case();
    assert_eq!(
        risk::evaluate(&f, &r, 99).unwrap_err(),
        E::FundingNotAvailable
    );
    let mut r = r;
    r.intent.expires_ns = 102;
    bind(&f, &mut r);
    assert!(risk::evaluate(&f, &r, 101).is_ok());
    assert_eq!(
        risk::evaluate(&f, &r, 102).unwrap_err(),
        E::IntentNotCurrent
    );
    let (mut f, mut r) = case();
    f.max_spec_age_ns = 1;
    bind(&f, &mut r);
    assert_eq!(risk::evaluate(&f, &r, 102).unwrap_err(), E::StaleSpec);
    let (f, mut r) = case();
    r.intent.spec.valid_until_ns = None;
    bind(&f, &mut r);
    assert_eq!(
        risk::evaluate(&f, &r, 101).unwrap_err(),
        E::UnknownSpecValidity
    );
    let (mut f, mut r) = case();
    f.fee_cap.valid_until_ns = r.intent.expires_ns - 1;
    bind(&f, &mut r);
    assert_eq!(
        risk::evaluate(&f, &r, 101).unwrap_err(),
        E::FeeBoundNotCurrent
    );
    let (mut f, mut r) = case();
    f.fee_cap.known_ns = 102;
    bind(&f, &mut r);
    assert_eq!(
        risk::evaluate(&f, &r, 101).unwrap_err(),
        E::FeeBoundNotCurrent
    );
}
#[test]
fn only_matching_fully_paid_same_currency_cash_domain_is_admitted() {
    for variant in 0..4 {
        let (f, mut r) = case();
        match variant {
            0 => r.intent.spec.settlement_rule = SettlementRule::LinearViaFuture,
            1 => r.intent.spec.margin_currency = "BTC".into(),
            2 => r.owner.currency = "USDT".into(),
            _ => r.intent.leg.side = Side::Sell,
        }
        bind(&f, &mut r);
        assert!(risk::evaluate(&f, &r, 101).is_err());
    }
    let (mut f, _) = case();
    f.owner.currency = "usdc".into();
    assert!(risk::validate_funding(&f).is_err());
}
#[test]
fn missing_zero_unidentified_fee_evidence_and_forged_bindings_reject() {
    for variant in 0..5 {
        let (mut f, mut r) = case();
        match variant {
            0 => f.fee_cap.premium_fraction = Dec::zero(),
            1 => f.fee_cap.source_ref.clear(),
            2 => f.fee_cap.source_sha256 = "0".repeat(64),
            3 => f.source_sha256 = "0".repeat(64),
            _ => f.max_spec_age_ns = 0,
        }
        bind(&f, &mut r);
        assert!(risk::validate_funding(&f).is_err());
        assert!(risk::evaluate(&f, &r, 101).is_err());
    }
    let (f, mut r) = case();
    r.funding_sha256 = "1".repeat(64);
    assert_eq!(
        risk::evaluate(&f, &r, 101).unwrap_err(),
        E::IdentityMismatch
    );
    r.intent_sha256 = "1".repeat(64);
    assert!(risk::validate_request(&r).is_err());
}
#[test]
fn strict_new_contracts_do_not_accept_recorded_state_or_authority_claims() {
    let (f, r) = case();
    for field in [
        "risk_approved",
        "execution_authority",
        "RECORDED_UNASSESSED",
        "available",
    ] {
        let mut v = serde_json::to_value(&r).unwrap();
        v[field] = true.into();
        assert!(risk::decode_request(&serde_json::to_vec(&v).unwrap()).is_err());
    }
    let mut v = serde_json::to_value(&f).unwrap();
    v.as_object_mut().unwrap().remove("fee_cap");
    assert!(risk::decode_funding(&serde_json::to_vec(&v).unwrap()).is_err());
    let json = serde_json::to_string(&r).unwrap();
    for bad in [
        json.replace("SIMULATED", "LIVE"),
        json.replace("MAIN", "CUSTOM_SPLIT"),
        json.replace(
            "\"created_ns\":100",
            "\"created_ns\":100,\"created_ns\":100",
        ),
        json.replace("\"limit_price_native\":\"2\"", "\"limit_price_native\":2.0"),
        format!("{json} {{}}"),
    ] {
        assert_ne!(bad, json);
        assert!(risk::decode_request(bad.as_bytes()).is_err());
    }
    assert!(
        risk::decode_request(include_bytes!(
            "../../options-workbench/fixtures/simulated-intent.json"
        ))
        .is_err()
    );
}
