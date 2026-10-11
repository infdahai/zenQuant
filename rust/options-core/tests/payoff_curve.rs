use options_core::{
    canonical::canonical_hash, contracts::*, decimal::Dec, error::ErrorCode,
    fixtures::mathematical_curve, payoff_curve as curve, terminal_diagnostics as terminal,
};
fn d(s: &str) -> Dec {
    Dec::parse(s).unwrap()
}

#[test]
fn batch_matches_existing_model_at_every_price_and_hand_oracle() {
    let r = mathematical_curve().unwrap();
    let out = curve::analyze(&r).unwrap();
    assert_eq!(out.points.len(), 1001);
    assert_eq!(out.points[0].gross_payoff, Some(d("0")));
    assert_eq!(out.points[500].conditional_model_pnl, Some(d("-.0213")));
    assert_eq!(out.points[1000].conditional_model_pnl, Some(d(".4787")));
    assert_eq!(out.input_hash, canonical_hash(&r).unwrap());
    let c = r.candidate.unwrap();
    let g = r.grid.unwrap();
    for points in out.points.chunks(MAX_SCENARIOS) {
        let old = terminal::TerminalDiagnosticRequest {
            schema_version: terminal::REQUEST_SCHEMA.into(),
            request_id: "oracle".into(),
            cutoff_ns: r.cutoff_ns,
            report_currency: r.report_currency.clone(),
            leg: c.leg.clone(),
            spec: c.spec.clone(),
            metadata_known_ns: c.metadata_known_ns,
            scenarios: terminal::TerminalScenarios {
                meta: g.meta.clone(),
                spec_hash: g.spec_hash.clone(),
                scenarios: points
                    .iter()
                    .enumerate()
                    .map(|(i, p)| terminal::TerminalScenario {
                        scenario_id: i.to_string(),
                        at_ns: g.at_ns,
                        delivery_price: p.delivery_price.clone(),
                    })
                    .collect(),
            },
            quotes: c.quotes.clone(),
            terminal_fee: c.terminal_fee.clone(),
            source_identities: vec![],
        };
        let reference = terminal::analyze(&old).unwrap();
        for (a, b) in points.iter().zip(reference.outcomes) {
            assert_eq!(a.gross_payoff, b.gross_payoff);
            assert_eq!(a.conditional_model_pnl, b.conditional_model_pnl);
        }
    }
}

#[test]
fn missing_evidence_never_fabricates_net_or_candidate() {
    let mut r = mathematical_curve().unwrap();
    r.candidate.as_mut().unwrap().terminal_fee = None;
    let out = curve::analyze(&r).unwrap();
    assert!(
        out.points
            .iter()
            .all(|p| p.gross_payoff.is_some() && p.conditional_model_pnl.is_none())
    );
    assert_eq!(
        out.diagnostic.unwrap().net_status.reason,
        "MISSING_TERMINAL_FEE_ASSUMPTION"
    );
    r.candidate.as_mut().unwrap().quotes.clear();
    assert_eq!(
        curve::analyze(&r)
            .unwrap()
            .diagnostic
            .unwrap()
            .net_status
            .reason,
        "MISSING_VISIBLE_EXACT_SPEC_QUOTE"
    );
    r.candidate = None;
    r.grid = None;
    let out = curve::analyze(&r).unwrap();
    assert_eq!(
        out.missing,
        vec!["CANDIDATE_AND_CONTRACT", "EXPLICIT_PRICE_SCENARIOS"]
    );
    assert!(out.points.is_empty());
}

#[test]
fn manual_signal_is_optional_time_bounded_and_does_not_change_options_math() {
    let mut r = mathematical_curve().unwrap();
    let independent = curve::analyze(&r).unwrap();
    r.signal = Some(curve::ManualSignal {
        schema_version: curve::SIGNAL_SCHEMA.into(),
        source_ref: "manual hypothesis".into(),
        source_sha256: "a".repeat(64),
        observed_ns: 8,
        known_ns: 9,
        valid_until_ns: 11,
        hypothesis: "Explicit upward price scenarios; no probability".into(),
    });
    let out = curve::analyze(&r).unwrap();
    assert_eq!(out.signal_status.status, Status::Unverified);
    assert_eq!(
        serde_json::to_value(&out.points).unwrap(),
        serde_json::to_value(independent.points).unwrap()
    );
    r.signal.as_mut().unwrap().known_ns = 11;
    r.signal.as_mut().unwrap().valid_until_ns = 12;
    assert_eq!(
        curve::analyze(&r).unwrap().signal_status.reason,
        "SIGNAL_NOT_YET_KNOWN"
    );
    r.signal.as_mut().unwrap().known_ns = 9;
    r.signal.as_mut().unwrap().valid_until_ns = 10;
    assert_eq!(
        curve::analyze(&r).unwrap().signal_status.reason,
        "SIGNAL_EXPIRED"
    );
}

#[test]
fn strict_new_boundary_preserves_precision_limits_and_old_v1() {
    let mut r = mathematical_curve().unwrap();
    let mut v = serde_json::to_value(&r).unwrap();
    v["view"] = serde_json::json!({});
    assert!(curve::decode_request(&serde_json::to_vec(&v).unwrap()).is_err());
    assert!(options_core::inputs::decode_request(&serde_json::to_vec(&r).unwrap()).is_err());
    let bytes = serde_json::to_vec(&r).unwrap();
    let mut duplicate = bytes[..bytes.len() - 1].to_vec();
    duplicate.extend_from_slice(b",\"cutoff_ns\":10}");
    assert_eq!(
        curve::decode_request(&duplicate).unwrap_err().code,
        ErrorCode::DuplicateKey
    );
    r.grid.as_mut().unwrap().count = 4097;
    assert_eq!(
        curve::analyze(&r).unwrap_err().code,
        ErrorCode::LimitExceeded
    );
    r.grid.as_mut().unwrap().count = 2;
    r.grid.as_mut().unwrap().step = d("0");
    assert!(curve::analyze(&r).is_err());
    r.grid.as_mut().unwrap().step = d(".1");
    r.grid.as_mut().unwrap().spec_hash = "f".repeat(64);
    assert_eq!(
        curve::analyze(&r).unwrap_err().code,
        ErrorCode::InvalidIdentity
    );
}

#[test]
fn unsupported_side_expiry_and_stale_cost_never_get_net_values() {
    let mut r = mathematical_curve().unwrap();
    r.candidate.as_mut().unwrap().leg.side = Side::Sell;
    assert!(
        curve::analyze(&r)
            .unwrap()
            .points
            .iter()
            .all(|p| p.gross_payoff.is_none())
    );
    r.candidate.as_mut().unwrap().leg.side = Side::Buy;
    r.grid.as_mut().unwrap().at_ns = 999;
    assert!(
        curve::analyze(&r)
            .unwrap()
            .points
            .iter()
            .all(|p| p.gross_payoff.is_none())
    );
    r.grid.as_mut().unwrap().at_ns = 1000;
    r.cutoff_ns = 200;
    let out = curve::analyze(&r).unwrap();
    assert!(
        out.points
            .iter()
            .all(|p| p.gross_payoff.is_some() && p.conditional_model_pnl.is_none())
    );
    assert_eq!(
        out.diagnostic.unwrap().support.execution_evidence.status,
        Status::Unverified
    );
}

#[test]
fn maximum_grid_has_exact_ordered_endpoints_without_accumulated_float_error() {
    let mut r = mathematical_curve().unwrap();
    r.grid.as_mut().unwrap().count = 4096;
    r.grid.as_mut().unwrap().first = d("0");
    r.grid.as_mut().unwrap().step = d("0.0000000000000000000000000001");
    let out = curve::analyze(&r).unwrap();
    assert_eq!(
        out.points.last().unwrap().delivery_price,
        d("0.0000000000000000000000004095")
    );
    assert!(
        out.points
            .windows(2)
            .all(|p| p[0].delivery_price < p[1].delivery_price)
    );
    assert_eq!(out.points.len(), 4096);
    r.candidate.as_mut().unwrap().quotes = vec![r.candidate.as_ref().unwrap().quotes[0].clone(); 5];
    assert_eq!(
        curve::analyze(&r).unwrap_err().code,
        ErrorCode::LimitExceeded
    );
}
