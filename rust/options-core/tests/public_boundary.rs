use options_core::{
    canonical::canonical_hash,
    constraints::certify_vertical,
    contracts::*,
    expressions::analyze_expression,
    inputs::{decode_request, load_analysis},
};
fn request(vertical: bool) -> Request {
    decode_request(if vertical {
        include_bytes!("../../options-workbench/fixtures/terminal-vertical.json")
    } else {
        include_bytes!("../../options-workbench/fixtures/terminal-eth-call.json")
    })
    .unwrap()
}
fn invalid_variants(vertical: bool) -> Vec<AnalysisInput> {
    let base = load_analysis(request(vertical)).unwrap();
    let mut variants = vec![];
    let mut a = base.clone();
    a.scenarios.view_hash = "0".repeat(64);
    variants.push(a);
    let mut a = base.clone();
    a.scenarios.meta.received_ns = a.cutoff_ns + 1;
    a.scenarios.meta.available_ns = a.cutoff_ns + 1;
    a.scenarios.meta.cutoff_ns = a.cutoff_ns + 1;
    variants.push(a);
    let mut a = base.clone();
    a.view.mechanism = "changed outside history".into();
    a.scenarios.view_hash = canonical_hash(&a.view).unwrap();
    variants.push(a);
    let mut a = base.clone();
    a.scenarios.scenarios.clear();
    variants.push(a);
    let mut a = base.clone();
    a.view_history.clear();
    variants.push(a);
    let mut a = base.clone();
    a.scenarios.scenarios[0].points.clear();
    variants.push(a);
    let mut a = base.clone();
    a.scenarios.meta.received_ns = a.scenarios.meta.available_ns + 1;
    variants.push(a);
    let mut a = base.clone();
    a.view_history[0].meta.parent_hash = Some("0".repeat(64));
    variants.push(a);
    let mut a = base;
    a.quotes[0].book.spec.multiplier = options_core::decimal::Dec::zero();
    variants.push(a);
    variants
}
#[test]
fn cash_zero_legs_survives_decode_admission_and_keeps_unknown_version_unsupported() {
    let mut r = request(false);
    r.fixture_name = None;
    r.candidates[0].template_id = "cash".into();
    r.candidates[0].legs.clear();
    for version in ["v1", "future", "1"] {
        r.candidates[0].template_version = version.into();
        let decoded = decode_request(&serde_json::to_vec(&r).unwrap()).unwrap();
        let a = load_analysis(decoded).expect("canonical zero-leg cash is admissible");
        let result = analyze_expression(&a, &a.requests[0]);
        assert_eq!(
            result.support.model_support.status,
            if version == "v1" {
                Status::Valid
            } else {
                Status::Unsupported
            }
        );
        if version == "v1" {
            assert_eq!(result.model_funding.unwrap().status.status, Status::Valid);
            assert!(
                result
                    .outcomes
                    .iter()
                    .all(|o| o.conditional_model_pnl == Some(options_core::decimal::Dec::zero()))
            );
        }
    }
    r.candidates[0].template_id = "long_call".into();
    assert!(load_analysis(r).is_err());
}
#[test]
fn public_expression_rejects_unbound_late_empty_or_history_mismatched_input() {
    for a in invalid_variants(false) {
        let result = analyze_expression(&a, &a.requests[0]);
        assert_eq!(result.support.input_validity.status, Status::Invalid);
        assert_ne!(result.support.model_support.status, Status::Valid);
        assert!(result.model_funding.is_none());
        assert!(result.modeled_candidate.is_none());
        assert!(result.conditional_ev.is_none());
        assert!(
            result
                .outcomes
                .iter()
                .all(|o| o.conditional_model_pnl.is_none())
        );
    }
}
#[test]
fn public_vertical_rejects_invalid_causal_input() {
    for a in invalid_variants(true) {
        let result = certify_vertical(&a, &a.requests[0]);
        assert_eq!(result.status.status, Status::Invalid);
        assert!(result.conditional_model_lower.is_none());
        assert!(result.gross_payoff_lower.is_none());
    }
}
#[test]
fn public_candidate_must_match_an_admitted_request() {
    let a = load_analysis(request(false)).unwrap();
    let mut r = a.requests[0].clone();
    r.terminal_fee_native = None;
    assert_eq!(
        analyze_expression(&a, &r).support.input_validity.status,
        Status::Invalid
    );
    let a = load_analysis(request(true)).unwrap();
    let mut r = a.requests[0].clone();
    r.terminal_fee_native = None;
    assert_eq!(certify_vertical(&a, &r).status.status, Status::Invalid);
}

#[test]
fn deserialized_inputs_and_adapter_gaps_use_the_same_public_boundary() {
    use options_core::inputs::ValidatedAnalysis;
    let mut a = load_analysis(request(false)).unwrap();
    a.adapter_version = "sealed_panel.rust.v1".into();
    a.gaps.push(StatusResult::new(
        Status::Unverified,
        "EXPORTED_PROJECTION_UNATTESTED",
    ));
    let a: AnalysisInput = serde_json::from_slice(&serde_json::to_vec(&a).unwrap()).unwrap();
    let validated = ValidatedAnalysis::new(&a).unwrap();
    assert_eq!(
        validated.analyze_expression(&a.requests[0]),
        analyze_expression(&a, &a.requests[0])
    );
    assert_eq!(
        validated
            .analyze_expression(&a.requests[0])
            .support
            .input_validity
            .status,
        Status::Valid
    );
    let mut outside = a.requests[0].clone();
    outside.request_id = "outside".into();
    assert_eq!(
        validated
            .analyze_expression(&outside)
            .support
            .input_validity
            .status,
        Status::Invalid
    );
    assert_eq!(
        validated.certify_vertical(&outside).status.status,
        Status::Invalid
    );
}
#[test]
fn public_boundary_keeps_late_fee_and_spec_evidence_unfunded() {
    for field in 0..3 {
        let mut a = load_analysis(request(false)).unwrap();
        match field {
            0 => a.quotes[0].fee_known_ns = Some(a.cutoff_ns + 1),
            1 => a.quotes[0].metadata_known_ns = Some(a.cutoff_ns + 1),
            _ => {
                let fx = a.quotes[0].quote_to_report.as_mut().unwrap();
                fx.received_ns = a.cutoff_ns + 1;
                fx.available_ns = a.cutoff_ns + 1;
            }
        }
        let result = analyze_expression(&a, &a.requests[0]);
        assert!(result.model_funding.is_none());
        assert!(
            result
                .outcomes
                .iter()
                .all(|o| o.conditional_model_pnl.is_none())
        );
        assert_ne!(result.support.modeled_feasibility.status, Status::Valid);
    }
}
