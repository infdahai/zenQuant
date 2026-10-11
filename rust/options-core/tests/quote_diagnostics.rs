use options_core::{
    canonical::canonical_hash,
    contracts::*,
    decimal::Dec,
    fixtures::{mathematical_quote, mathematical_spec},
    inputs,
    quote_diagnostics::{self as diagnostics, QuoteCostRequest, QuoteDiagnosticRequest},
};

fn d(s: &str) -> Dec {
    Dec::parse(s).unwrap()
}
fn request() -> QuoteDiagnosticRequest {
    let quote = mathematical_quote(mathematical_spec(OptionType::C, d("100")), d("2"), d("1.9"));
    QuoteDiagnosticRequest {
        schema_version: diagnostics::REQUEST_SCHEMA.into(),
        cutoff_ns: 10,
        report_currency: "USDC".into(),
        requests: vec![QuoteCostRequest {
            request_id: "buy-call".into(),
            leg: CandidateLeg {
                instrument: InstrumentDescriptor::from_spec(&quote.book.spec).unwrap(),
                side: Side::Buy,
                quantity_native: d(".01"),
            },
        }],
        quotes: vec![quote],
        source_identities: vec![(
            "input".into(),
            "mathematical_fixture_not_market_data".into(),
        )],
    }
}
fn decode(r: &QuoteDiagnosticRequest) -> options_core::error::Result<QuoteDiagnosticRequest> {
    diagnostics::decode_request(&serde_json::to_vec(r).unwrap())
}

#[test]
fn independent_buy_sell_costs_match_hand_oracles_without_view_or_budget() {
    let mut r = request();
    let mut sell = r.requests[0].clone();
    sell.request_id = "sell-call".into();
    sell.leg.side = Side::Sell;
    r.requests.push(sell);
    let results = diagnostics::analyze(&decode(&r).unwrap()).unwrap();
    for (result, premium, total) in [
        (&results[0], ".02", ".0203"),
        (&results[1], ".019", ".0187"),
    ] {
        assert_eq!(result.status.status, Status::Valid);
        let cost = result.cost.as_ref().unwrap();
        assert_eq!(cost.premium_native, Some(d(premium)));
        assert_eq!(cost.fee_native, Some(d(".0003")));
        assert_eq!(cost.total_report_currency, Some(d(total)));
        assert_eq!(result.account_eligibility.status, Status::Unverified);
        assert_eq!(result.execution_evidence.status, Status::Unverified);
        assert_eq!(result.input_hash, canonical_hash(&r).unwrap());
        assert_eq!(
            result.selected_quote_hash,
            Some(canonical_hash(&r.quotes[0]).unwrap())
        );
    }
    let value = serde_json::to_value(r).unwrap();
    for field in [
        "views",
        "view_id",
        "scenarios",
        "risk_policy",
        "hypothetical_balances",
    ] {
        assert!(value.get(field).is_none());
    }
}

#[test]
fn missing_quote_does_not_erase_another_independent_cost() {
    let mut r = request();
    let mut missing = r.requests[0].clone();
    missing.request_id = "missing".into();
    missing.leg.instrument.spec_hash = "0".repeat(64);
    r.requests.push(missing);
    let results = diagnostics::analyze(&r).unwrap();
    assert_eq!(results[0].status.status, Status::Valid);
    assert_eq!(results[1].status.reason, "MISSING_VISIBLE_EXACT_SPEC_QUOTE");
    assert!(results[1].cost.is_none());
    assert!(results[1].selected_quote_hash.is_none());
    r.quotes.clear();
    assert!(
        diagnostics::analyze(&r)
            .unwrap()
            .iter()
            .all(|d| d.cost.is_none())
    );
}

#[test]
fn latest_visible_quote_never_borrows_fees_or_uses_future_evidence() {
    let mut r = request();
    let mut next = r.quotes[0].clone();
    next.quote_id = "later".into();
    next.book.source_ns = 11;
    next.book.received_ns = 11;
    next.book.available_ns = 12;
    next.fee_policy = None;
    next.fee_known_ns = None;
    r.quotes.push(next);
    let original = diagnostics::analyze(&r).unwrap();
    assert_eq!(
        original[0].selected_quote_id.as_deref(),
        Some(r.quotes[0].quote_id.as_str())
    );
    r.cutoff_ns = 11;
    let still_visible = diagnostics::analyze(&r).unwrap().remove(0);
    assert_eq!(
        still_visible.selected_quote_hash,
        original[0].selected_quote_hash
    );
    assert_eq!(
        still_visible.cost.unwrap().total_report_currency,
        Some(d(".0203"))
    );
    r.cutoff_ns = 12;
    let selected = diagnostics::analyze(&r).unwrap().remove(0);
    assert_eq!(selected.selected_quote_id.as_deref(), Some("later"));
    assert_eq!(selected.status.status, Status::NotEvaluable);
    assert!(selected.cost.unwrap().total_report_currency.is_none());
    r.quotes.reverse();
    assert_eq!(
        diagnostics::analyze(&r).unwrap()[0].selected_quote_id,
        selected.selected_quote_id
    );
}

#[test]
fn missing_late_stale_and_insufficient_evidence_never_produce_a_total() {
    for variant in 0..9 {
        let mut r = request();
        let q = &mut r.quotes[0];
        match variant {
            0 => {
                q.fee_policy = None;
                q.fee_known_ns = None;
            }
            1 => q.fee_known_ns = Some(11),
            2 => q.metadata_known_ns = Some(11),
            3 => {
                q.quote_to_report = None;
                q.fx_pair = None;
            }
            4 => q.quote_to_report.as_mut().unwrap().available_ns = 11,
            5 => {
                q.index_in_quote = None;
                q.index_currency = None;
            }
            6 => q.book.source_ns = 0,
            7 => q.book.asks[0].quantity_native = d(".001"),
            _ => {
                q.book.received_ns = 11;
                q.book.available_ns = 11;
            }
        }
        q.timing.max_age_ns = 5;
        let result = diagnostics::analyze(&decode(&r).unwrap())
            .unwrap()
            .remove(0);
        assert_eq!(
            result.status.status,
            Status::NotEvaluable,
            "variant {variant}"
        );
        assert!(
            result
                .cost
                .is_none_or(|c| c.total_report_currency.is_none())
        );
    }
}

#[test]
fn structural_conflicts_are_rejected_at_json_and_public_typed_boundaries() {
    for variant in 0..10 {
        let mut r = request();
        match variant {
            0 => r.requests.push(r.requests[0].clone()),
            1 => r.requests[0].leg.instrument.venue = "wrong".into(),
            2 => r.quotes[0].book.spec.multiplier = Dec::zero(),
            3 => r.quotes[0].book.available_ns = 9,
            4 => {
                let mut q = r.quotes[0].clone();
                q.quote_id = "conflict".into();
                q.book.asks[0].price = d("3");
                r.quotes.push(q);
            }
            5 => r.source_identities.push(r.source_identities[0].clone()),
            6 => r.requests[0].leg.quantity_native = d("-1"),
            7 => r.quotes[0].fx_pair = Some(("BTC".into(), "USDC".into())),
            8 => r.schema_version = "future".into(),
            _ => r.quotes[0].book.raw_hash = "unbound".into(),
        }
        assert!(decode(&r).is_err(), "JSON variant {variant}");
        assert!(diagnostics::analyze(&r).is_err(), "typed variant {variant}");
    }
}

#[test]
fn strict_versioned_contracts_do_not_silently_cross_admit() {
    let original = include_bytes!("../../options-workbench/fixtures/terminal-eth-call.json");
    assert!(diagnostics::decode_request(original).is_err());
    assert!(inputs::decode_request(&serde_json::to_vec(&request()).unwrap()).is_err());
    let mut v: serde_json::Value = serde_json::from_slice(original).unwrap();
    v.as_object_mut().unwrap().remove("views");
    assert!(inputs::decode_request(&serde_json::to_vec(&v).unwrap()).is_err());
    for field in [
        "views",
        "view",
        "risk_policy",
        "execution_authority",
        "strategy",
        "panel_file",
    ] {
        let mut v = serde_json::to_value(request()).unwrap();
        v[field] = serde_json::Value::Null;
        assert!(
            diagnostics::decode_request(&serde_json::to_vec(&v).unwrap()).is_err(),
            "{field}"
        );
    }
    let mut v = serde_json::to_value(request()).unwrap();
    v["requests"][0]["leg"]["authority"] = true.into();
    assert!(diagnostics::decode_request(&serde_json::to_vec(&v).unwrap()).is_err());
    let json = serde_json::to_string(&request()).unwrap();
    for bad in [
        json.replacen("\"cutoff_ns\":10", "\"cutoff_ns\":10,\"cutoff_ns\":10", 1),
        json.replacen("\"cutoff_ns\":10", "\"cutoff_ns\":10.0", 1),
        json.replacen("\"cutoff_ns\":10", "\"cutoff_ns\":true", 1),
        json.replacen(
            "\"quantity_native\":\"0.01\"",
            "\"quantity_native\":0.01",
            1,
        ),
        format!("{json} {{}}"),
    ] {
        assert_ne!(bad, json);
        assert!(diagnostics::decode_request(bad.as_bytes()).is_err());
    }
}

#[test]
fn typed_limits_and_transport_limits_are_both_enforced() {
    let mut r = request();
    r.requests = vec![r.requests[0].clone(); diagnostics::MAX_DIAGNOSTICS + 1];
    assert!(diagnostics::analyze(&r).is_err());
    r = request();
    r.quotes = vec![r.quotes[0].clone(); MAX_QUOTES + 1];
    assert!(diagnostics::analyze(&r).is_err());
    r = request();
    r.requests.clear();
    assert!(decode(&r).is_err());
    r = request();
    r.quotes[0].assumptions = vec!["x".repeat(MAX_STRING_BYTES); 257];
    assert!(diagnostics::analyze(&r).is_err());
    assert!(diagnostics::decode_request(&vec![b' '; MAX_REQUEST_BYTES + 1]).is_err());
}

#[test]
fn unknown_proxy_metadata_is_retained_and_never_becomes_execution_proof() {
    let mut r = request();
    let q = &mut r.quotes[0];
    q.book.spec.provenance = Provenance::SealedProxy;
    q.book.spec.exercise_style = ExerciseStyle::Unknown;
    q.book.spec.settlement_rule = SettlementRule::Unknown;
    q.book.spec.product = Product::InverseOption;
    r.requests[0].leg.instrument = InstrumentDescriptor::from_spec(&q.book.spec).unwrap();
    let decoded = decode(&r).unwrap();
    assert_eq!(decoded, r);
    let result = diagnostics::analyze(&decoded).unwrap().remove(0);
    assert_eq!(result.status.status, Status::Valid);
    assert_eq!(result.execution_evidence.status, Status::Unverified);
    assert_eq!(
        decoded.quotes[0].book.spec.exercise_style,
        ExerciseStyle::Unknown
    );
}

#[test]
fn quantity_and_depth_rules_remain_diagnostic_not_silent_rounding() {
    let mut r = request();
    r.requests[0].leg.quantity_native = d(".015");
    let result = diagnostics::analyze(&r).unwrap().remove(0);
    assert_eq!(result.status.status, Status::Rejected);
    assert!(result.cost.unwrap().total_report_currency.is_none());
    r.requests[0].leg.quantity_native = d(".1");
    r.quotes[0].book.asks = vec![
        BookLevel {
            price: d("2"),
            quantity_native: d(".04"),
        },
        BookLevel {
            price: d("3"),
            quantity_native: d(".06"),
        },
    ];
    let cost = diagnostics::analyze(&r).unwrap().remove(0).cost.unwrap();
    assert_eq!(cost.premium_native, Some(d(".26")));
    assert_eq!(cost.total_report_currency, Some(d(".263")));
}
