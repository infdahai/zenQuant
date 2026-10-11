use options_core::{
    canonical::canonical_hash,
    contracts::*,
    decimal::Dec,
    fixtures::{mathematical_meta, mathematical_quote, mathematical_spec},
    inputs,
    terminal_diagnostics::{
        self as terminal, TerminalDiagnosticRequest, TerminalFee, TerminalScenario,
        TerminalScenarios,
    },
};

fn d(value: &str) -> Dec {
    Dec::parse(value).unwrap()
}
fn request(kind: OptionType) -> TerminalDiagnosticRequest {
    let spec = mathematical_spec(kind, d("100"));
    let mut meta = mathematical_meta(terminal::SCENARIOS_SCHEMA);
    meta.object_id = "independent-terminal-scenarios".into();
    TerminalDiagnosticRequest {
        schema_version: terminal::REQUEST_SCHEMA.into(),
        request_id: "single-leg".into(),
        cutoff_ns: 10,
        report_currency: "USDC".into(),
        leg: CandidateLeg {
            instrument: InstrumentDescriptor::from_spec(&spec).unwrap(),
            side: Side::Buy,
            quantity_native: d(".01"),
        },
        metadata_known_ns: Some(0),
        scenarios: TerminalScenarios {
            meta,
            spec_hash: canonical_hash(&spec).unwrap(),
            scenarios: ["0", "90", "100", "110"]
                .iter()
                .map(|price| TerminalScenario {
                    scenario_id: format!("price-{price}"),
                    at_ns: 1000,
                    delivery_price: d(price),
                })
                .collect(),
        },
        quotes: vec![mathematical_quote(spec.clone(), d("2"), d("1.9"))],
        spec,
        terminal_fee: Some(TerminalFee {
            amount_report: d(".001"),
            known_ns: 0,
            source_ref: "mathematical_total_fee_assumption".into(),
        }),
        source_identities: vec![],
    }
}
fn rebind(r: &mut TerminalDiagnosticRequest) {
    r.leg.instrument = InstrumentDescriptor::from_spec(&r.spec).unwrap();
    r.scenarios.spec_hash = canonical_hash(&r.spec).unwrap();
    for q in &mut r.quotes {
        q.book.spec = r.spec.clone();
    }
}
fn roundtrip(
    r: &TerminalDiagnosticRequest,
) -> options_core::error::Result<TerminalDiagnosticRequest> {
    terminal::decode_request(&serde_json::to_vec(r).unwrap())
}

#[test]
fn call_put_hand_oracles_have_no_funding_or_execution_authority() {
    for (kind, expected) in [
        (OptionType::C, ["0", "0", "0", ".1"]),
        (OptionType::P, ["1", ".1", "0", "0"]),
    ] {
        let r = request(kind);
        let out = terminal::analyze(&roundtrip(&r).unwrap()).unwrap();
        assert_eq!(out.support.model_support.status, Status::Valid);
        assert_eq!(out.net_status.status, Status::Valid);
        assert_eq!(out.support.modeled_feasibility.status, Status::NotEvaluable);
        assert_eq!(
            out.support.modeled_feasibility.reason,
            "FUNDING_NOT_ASSESSED"
        );
        assert_eq!(out.support.account_eligibility.status, Status::Unverified);
        assert_eq!(out.support.execution_evidence.status, Status::Unverified);
        assert_eq!(
            out.cost.as_ref().unwrap().total_report_currency,
            Some(d(".0203"))
        );
        for (o, gross) in out.outcomes.iter().zip(expected) {
            assert_eq!(o.gross_payoff, Some(d(gross)));
            assert_eq!(
                o.conditional_model_pnl,
                Some(d(gross).checked_sub(&d(".0213")).unwrap())
            );
        }
        assert_eq!(out.input_hash, canonical_hash(&r).unwrap());
        assert_eq!(out.scenario_hash, canonical_hash(&r.scenarios).unwrap());
    }
}

#[test]
fn existing_v1_single_call_economics_are_preserved_without_its_view() {
    let old = inputs::decode_request(include_bytes!(
        "../../options-workbench/fixtures/terminal-eth-call.json"
    ))
    .unwrap();
    let a = inputs::load_analysis(old.clone()).unwrap();
    let original = options_core::expressions::analyze_expression(&a, &a.requests[0]);
    let mut r = request(OptionType::C);
    r.spec = old.quotes[0].book.spec.clone();
    r.leg = old.candidates[0].legs[0].clone();
    r.quotes = old.quotes;
    r.terminal_fee.as_mut().unwrap().amount_report =
        old.candidates[0].terminal_fee_native.clone().unwrap();
    r.scenarios.scenarios = old
        .scenarios
        .scenarios
        .iter()
        .map(|s| TerminalScenario {
            scenario_id: s.scenario_id.clone(),
            at_ns: s.points[0].at_ns,
            delivery_price: s.points[0].delivery_by_spec_hash[0].1.clone(),
        })
        .collect();
    rebind(&mut r);
    let standalone = terminal::analyze(&r).unwrap();
    assert_eq!(standalone.outcomes, original.outcomes);
    assert_eq!(standalone.cost, original.costs.first().cloned());
    assert_eq!(
        standalone.support.modeled_feasibility.status,
        Status::NotEvaluable
    );
}

#[test]
fn dependency_ablation_removes_quotes_without_changing_gross_math() {
    let baseline = request(OptionType::C);
    let original = terminal::analyze(&baseline).unwrap();
    for variant in 0..6 {
        let mut r = baseline.clone();
        match variant {
            0 => r.quotes.clear(),
            1 => r.terminal_fee = None,
            2 => r.terminal_fee.as_mut().unwrap().known_ns = 11,
            3 => {
                r.quotes[0].fee_policy = None;
                r.quotes[0].fee_known_ns = None;
            }
            4 => {
                r.quotes[0].quote_to_report = None;
                r.quotes[0].fx_pair = None;
            }
            _ => r.quotes[0].fee_known_ns = Some(11),
        }
        let out = terminal::analyze(&r).unwrap();
        assert_eq!(out.support.model_support.status, Status::Valid);
        assert_eq!(out.net_status.status, Status::NotEvaluable);
        assert_eq!(out.scenario_hash, original.scenario_hash);
        assert_ne!(out.input_hash, original.input_hash);
        for (actual, expected) in out.outcomes.iter().zip(&original.outcomes) {
            assert_eq!(actual.gross_payoff, expected.gross_payoff);
            assert!(actual.conditional_model_pnl.is_none());
        }
    }
    let mut r = baseline;
    r.quotes[0].book.asks[0].price = d("3");
    let out = terminal::analyze(&r).unwrap();
    assert_eq!(
        out.outcomes[3].gross_payoff,
        original.outcomes[3].gross_payoff
    );
    assert_eq!(out.outcomes[3].conditional_model_pnl, Some(d(".0687")));
}

#[test]
fn unsupported_product_side_currency_and_any_nonterminal_point_return_no_payoffs() {
    for variant in 0..8 {
        let mut r = request(OptionType::C);
        match variant {
            0 => r.spec.product = Product::InverseOption,
            1 => r.spec.exercise_style = ExerciseStyle::American,
            2 => r.spec.exercise_style = ExerciseStyle::Unknown,
            3 => r.spec.settlement_rule = SettlementRule::Unknown,
            4 => r.spec.margin_currency = "BTC".into(),
            5 => r.leg.side = Side::Sell,
            6 => r.scenarios.scenarios[0].at_ns = 999,
            _ => r.scenarios.scenarios[0].at_ns = 1001,
        }
        rebind(&mut r);
        let out = terminal::analyze(&roundtrip(&r).unwrap()).unwrap();
        assert_eq!(
            out.support.model_support.status,
            Status::Unsupported,
            "variant {variant}"
        );
        assert_ne!(out.net_status.status, Status::Valid);
        assert!(
            out.outcomes
                .iter()
                .all(|o| o.gross_payoff.is_none() && o.conditional_model_pnl.is_none())
        );
        assert!(out.cost.is_some(), "independent quote diagnostic retained");
    }
}

#[test]
fn missing_spec_knowledge_and_unknown_fixing_never_produce_terminal_values() {
    for variant in 0..4 {
        let mut r = request(OptionType::C);
        match variant {
            0 => r.metadata_known_ns = None,
            1 => r.metadata_known_ns = Some(11),
            2 => r.spec.settlement_index = "UNKNOWN".into(),
            _ => r.spec.valid_until_ns = Some(r.spec.expiry_ns),
        }
        rebind(&mut r);
        let out = terminal::analyze(&r).unwrap();
        assert_eq!(out.support.model_support.status, Status::NotEvaluable);
        assert!(out.outcomes.iter().all(|o| o.gross_payoff.is_none()));
    }
    let mut r = request(OptionType::C);
    r.cutoff_ns = r.spec.expiry_ns;
    assert_eq!(
        terminal::analyze(&r).unwrap().support.model_support.status,
        Status::Unsupported
    );
}

#[test]
fn future_scenario_evidence_and_structural_conflicts_fail_both_public_boundaries() {
    for variant in 0..10 {
        let mut r = request(OptionType::C);
        match variant {
            0 => {
                r.scenarios.meta.received_ns = 11;
                r.scenarios.meta.available_ns = 11;
                r.scenarios.meta.cutoff_ns = 11;
            }
            1 => r.scenarios.spec_hash = "0".repeat(64),
            2 => r.scenarios.scenarios.push(r.scenarios.scenarios[0].clone()),
            3 => r.scenarios.scenarios[0].delivery_price = d("-1"),
            4 => r.terminal_fee.as_mut().unwrap().amount_report = d("-1"),
            5 => r.leg.instrument.venue = "wrong".into(),
            6 => r.quotes[0].book.spec.rule_version = "different".into(),
            7 => {
                let mut q = r.quotes[0].clone();
                q.quote_id = "conflict".into();
                q.book.asks[0].price = d("3");
                r.quotes.push(q);
            }
            8 => {
                r.scenarios.meta.revision = 2;
                r.scenarios.meta.parent_hash = Some("0".repeat(64));
            }
            _ => r.scenarios.scenarios.clear(),
        }
        assert!(roundtrip(&r).is_err(), "JSON {variant}");
        assert!(terminal::analyze(&r).is_err(), "typed {variant}");
    }
}

#[test]
fn contracts_reject_extra_models_numbers_versions_and_oversize_input() {
    for field in [
        "view",
        "risk_policy",
        "probabilities",
        "execution_authority",
    ] {
        let mut value = serde_json::to_value(request(OptionType::C)).unwrap();
        value[field] = true.into();
        assert!(terminal::decode_request(&serde_json::to_vec(&value).unwrap()).is_err());
    }
    let mut value = serde_json::to_value(request(OptionType::C)).unwrap();
    value["scenarios"]["scenarios"][0]["iv_shift"] = "0".into();
    assert!(terminal::decode_request(&serde_json::to_vec(&value).unwrap()).is_err());
    let json = serde_json::to_string(&request(OptionType::C)).unwrap();
    for bad in [
        json.replacen("\"cutoff_ns\":10", "\"cutoff_ns\":10,\"cutoff_ns\":10", 1),
        json.replacen("\"cutoff_ns\":10", "\"cutoff_ns\":true", 1),
        json.replacen("\"delivery_price\":\"0\"", "\"delivery_price\":0.0", 1),
        json.replacen(terminal::REQUEST_SCHEMA, "future", 1),
        format!("{json} {{}}"),
    ] {
        assert_ne!(bad, json);
        assert!(terminal::decode_request(bad.as_bytes()).is_err());
    }
    assert!(terminal::decode_request(&vec![b' '; MAX_REQUEST_BYTES + 1]).is_err());
    let mut r = request(OptionType::C);
    r.scenarios.scenarios = vec![r.scenarios.scenarios[0].clone(); MAX_SCENARIOS + 1];
    assert!(terminal::analyze(&r).is_err());
}

#[test]
fn quantity_units_and_via_future_keep_exact_conditional_semantics() {
    let mut r = request(OptionType::P);
    r.spec.quantity_unit = QuantityUnit::Contracts;
    r.spec.multiplier = d("10");
    r.spec.settlement_rule = SettlementRule::LinearViaFuture;
    rebind(&mut r);
    let out = terminal::analyze(&r).unwrap();
    assert_eq!(out.outcomes[1].gross_payoff, Some(d("1")));
    assert_eq!(out.outcomes[1].conditional_model_pnl, Some(d(".796")));
    assert!(
        out.reasons
            .iter()
            .any(|s| s.contains("NO_FUTURE_EVENTS_OR_ACCOUNT_PERMISSION"))
    );
    r.leg.quantity_native = d(".015");
    let out = terminal::analyze(&r).unwrap();
    assert_eq!(out.support.model_support.status, Status::Rejected);
    assert!(out.outcomes.iter().all(|o| o.gross_payoff.is_none()));
}

#[test]
fn future_opening_quote_is_not_selected_and_visible_later_quote_cannot_borrow_fees() {
    let mut r = request(OptionType::C);
    let baseline = terminal::analyze(&r).unwrap();
    let mut next = r.quotes[0].clone();
    next.quote_id = "later".into();
    next.book.source_ns = 11;
    next.book.received_ns = 11;
    next.book.available_ns = 12;
    next.fee_policy = None;
    next.fee_known_ns = None;
    r.quotes.push(next);
    let out = terminal::analyze(&r).unwrap();
    assert_eq!(out.outcomes, baseline.outcomes);
    assert_eq!(out.selected_quote_hash, baseline.selected_quote_hash);
    assert_ne!(out.input_hash, baseline.input_hash);
    r.cutoff_ns = 12;
    let out = terminal::analyze(&r).unwrap();
    assert_eq!(out.net_status.status, Status::NotEvaluable);
    assert_eq!(out.outcomes[3].gross_payoff, Some(d(".1")));
    assert!(out.outcomes[3].conditional_model_pnl.is_none());
}

#[test]
fn tiny_payoff_is_exact_and_excess_input_precision_is_rejected() {
    let mut r = request(OptionType::C);
    let tiny = d("0.0000000000000000000000000001");
    r.spec.min_quantity_native = tiny.clone();
    r.spec.quantity_step_native = tiny.clone();
    r.leg.quantity_native = tiny;
    r.quotes.clear();
    r.scenarios.scenarios[3].delivery_price = d("100.0000000000000000000000000001");
    rebind(&mut r);
    let out = terminal::analyze(&roundtrip(&r).unwrap()).unwrap();
    assert_eq!(
        out.outcomes[3]
            .gross_payoff
            .as_ref()
            .unwrap()
            .canonical_string()
            .unwrap(),
        format!("0.{}1", "0".repeat(55))
    );
    for price in [format!("0.{}1", "0".repeat(28)), "9".repeat(65)] {
        let mut value = serde_json::to_value(&r).unwrap();
        value["scenarios"]["scenarios"][3]["delivery_price"] = price.into();
        assert!(terminal::decode_request(&serde_json::to_vec(&value).unwrap()).is_err());
    }
}
