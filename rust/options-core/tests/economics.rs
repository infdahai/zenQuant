use options_core::{
    canonical::canonical_hash, constraints::certify_vertical, contracts::*, costs::quote_cost,
    decimal::Dec, expressions::analyze_expression, fixtures::*, inputs::load_analysis,
};
fn d(s: &str) -> Dec {
    Dec::parse(s).unwrap()
}
fn case(name: &str) -> AnalysisInput {
    let wallet = WalletKey {
        venue: "MATHEMATICAL_FIXTURE".into(),
        account: "hypothetical".into(),
        account_mode: "standard".into(),
        currency: "USDC".into(),
    };
    load_analysis(
        mathematical_request(
            name,
            d("1000"),
            d("10"),
            vec![WalletBalance {
                wallet,
                cash: d("1000"),
            }],
            RiskPolicy {
                per_intent_report: d("10"),
                active_and_pending_report: d("50"),
                rolling_tail_paid_report: d("20"),
                rolling_window_ns: 1000,
                report_currency: "USDC".into(),
                fx_timing: None,
            },
        )
        .unwrap(),
    )
    .unwrap()
}
fn leg(q: &QuoteInput, side: Side, qty: &str) -> CandidateLeg {
    CandidateLeg {
        instrument: InstrumentDescriptor::from_spec(&q.book.spec).unwrap(),
        side,
        quantity_native: d(qty),
    }
}
#[test]
fn cost_hand_oracles_depth_sell_cap_multiplier() {
    let a = case("terminal-eth-call");
    let q = &a.quotes[0];
    let c = quote_cost(q, &a.requests[0].legs[0], 10, "USDC");
    assert_eq!(c.total_report_currency, Some(d(".0203")));
    let sell = quote_cost(q, &leg(q, Side::Sell, ".01"), 10, "USDC");
    assert_eq!(sell.total_report_currency, Some(d(".0187")));
    let mut q = q.clone();
    q.book.asks = vec![
        BookLevel {
            price: d("2"),
            quantity_native: d(".04"),
        },
        BookLevel {
            price: d("3"),
            quantity_native: d(".06"),
        },
    ];
    let c = quote_cost(&q, &leg(&q, Side::Buy, ".1"), 10, "USDC");
    assert_eq!(c.premium_native, Some(d(".26")));
    assert_eq!(c.fee_native, Some(d(".003")));
    assert_eq!(c.total_report_currency, Some(d(".263")));
    q.book.spec.quantity_unit = QuantityUnit::Contracts;
    q.book.spec.multiplier = d("10");
    let c = quote_cost(&q, &leg(&q, Side::Buy, ".1"), 10, "USDC");
    assert_eq!(c.total_report_currency, Some(d("2.63")));
    q.book.asks = vec![BookLevel {
        price: d(".1"),
        quantity_native: d("1"),
    }];
    q.book.bids[0].price = d(".09");
    let c = quote_cost(&q, &leg(&q, Side::Buy, ".01"), 10, "USDC");
    assert_eq!(c.fee_native, Some(d(".00125")));
}
#[test]
fn costs_refuse_missing_late_stale_units_quantity_and_books() {
    let a = case("terminal-eth-call");
    let base = a.quotes[0].clone();
    let mut variants = vec![];
    let mut q = base.clone();
    q.fee_policy = None;
    variants.push(q);
    let mut q = base.clone();
    q.fee_known_ns = Some(11);
    variants.push(q);
    let mut q = base.clone();
    q.metadata_known_ns = Some(11);
    variants.push(q);
    let mut q = base.clone();
    q.index_currency = Some("USD".into());
    variants.push(q);
    let mut q = base.clone();
    q.fx_pair = Some(("USD".into(), "USDC".into()));
    variants.push(q);
    let mut q = base.clone();
    q.quote_to_report.as_mut().unwrap().available_ns = 11;
    variants.push(q);
    let mut q = base.clone();
    q.book.asks[0].quantity_native = d(".009");
    variants.push(q);
    let mut q = base.clone();
    q.book.bids.clear();
    variants.push(q);
    let mut q = base.clone();
    q.book.bids[0].price = d("3");
    variants.push(q);
    let mut q = base.clone();
    q.book.asks[0].price = d("2.001");
    variants.push(q);
    let mut q = base.clone();
    q.book.source_ns = 20;
    q.timing.minimum_source_age_ns = -2;
    variants.push(q);
    for q in variants {
        let c = quote_cost(&q, &leg(&q, Side::Buy, ".01"), 10, "USDC");
        assert_ne!(c.status.status, Status::Valid, "{}", c.status.reason);
        assert!(c.total_report_currency.is_none());
    }
    for qty in [".009", ".015", "0", "-.01"] {
        assert_ne!(
            quote_cost(&base, &leg(&base, Side::Buy, qty), 10, "USDC")
                .status
                .status,
            Status::Valid
        );
    }
    assert_ne!(
        quote_cost(&base, &leg(&base, Side::Buy, ".01"), 111, "USDC")
            .status
            .status,
        Status::Valid
    );
    let mut lead = base.clone();
    lead.book.source_ns = 11;
    lead.timing.minimum_source_age_ns = -2;
    let c = quote_cost(&lead, &leg(&lead, Side::Buy, ".01"), 10, "USDC");
    assert_eq!(c.status.status, Status::Valid);
    assert!(
        c.assumptions
            .iter()
            .any(|x| x.contains("SOURCE_CLOCK_LEAD"))
    );
}
#[test]
fn long_payoffs_ev_roots_and_missing_terminal_fee() {
    for (name, cost, ev, roots) in [
        ("terminal-eth-call", ".0203", ".0047", vec![d("102.03")]),
        (
            "terminal-eth-straddle",
            ".0356",
            ".0144",
            vec![d("96.44"), d("103.56")],
        ),
    ] {
        let mut a = case(name);
        a.scenarios.probability_source = Some(ProbabilitySource::Subjective);
        for (s, p) in a.scenarios.scenarios.iter_mut().zip([".25", ".5", ".25"]) {
            s.probability = Some(d(p));
        }
        let r = analyze_expression(&a, &a.requests[0]);
        assert_eq!(r.support.model_support.status, Status::Valid);
        assert_eq!(r.conditional_model_loss_bound, Some(d(cost)));
        assert_eq!(r.conditional_ev, Some(d(ev)));
        assert_eq!(r.break_even_levels, Some(roots));
        assert_eq!(r.support.account_eligibility.status, Status::Unverified);
        assert_eq!(r.support.execution_evidence.status, Status::Unverified);
        let mut req = a.requests[0].clone();
        req.terminal_fee_native = None;
        let r = analyze_expression(&a, &req);
        assert_eq!(r.outcomes[2].gross_payoff, Some(d(".1")));
        assert!(r.outcomes.iter().all(|x| x.conditional_model_pnl.is_none()));
        assert!(r.conditional_ev.is_none());
        assert!(r.conditional_model_loss_bound.is_none());
        assert_eq!(r.support.modeled_feasibility.status, Status::NotEvaluable);
    }
    let mut a = case("terminal-eth-straddle");
    a.requests[0].template_id = "long_put".into();
    a.requests[0].legs.remove(0);
    let r = analyze_expression(&a, &a.requests[0]);
    assert_eq!(r.break_even_levels, Some(vec![d("98.47")]));
    assert_eq!(r.outcomes[0].conditional_model_pnl, Some(d(".0847")));
    a.requests[0].template_id = "cash".into();
    a.requests[0].legs.clear();
    let r = analyze_expression(&a, &a.requests[0]);
    assert!(
        r.outcomes
            .iter()
            .all(|x| x.conditional_model_pnl == Some(Dec::zero()))
    );
    let mut a = case("terminal-eth-straddle");
    a.quotes[0].book.spec.strike = d("110");
    a.quotes[1].book.spec.strike = d("90");
    a.requests[0].template_id = "long_strangle".into();
    for (q, l) in a.quotes.iter().zip(&mut a.requests[0].legs) {
        l.instrument = InstrumentDescriptor::from_spec(&q.book.spec).unwrap();
    }
    for (s, price) in a.scenarios.scenarios.iter_mut().zip(["80", "100", "120"]) {
        s.points[0].delivery_by_spec_hash = a
            .quotes
            .iter()
            .map(|q| (canonical_hash(&q.book.spec).unwrap(), d(price)))
            .collect();
    }
    let r = analyze_expression(&a, &a.requests[0]);
    assert_eq!(r.break_even_levels, Some(vec![d("86.44"), d("113.56")]));
    assert_eq!(r.outcomes[0].conditional_model_pnl, Some(d(".0644")));
}
#[test]
fn model_refuses_conflicting_fixing_unsupported_and_interim() {
    let base = case("terminal-eth-straddle");
    let mut a = base.clone();
    a.scenarios.scenarios[0].points[0].delivery_by_spec_hash[0].1 = d("110");
    let r = analyze_expression(&a, &a.requests[0]);
    assert_eq!(r.support.input_validity.status, Status::Invalid);
    assert!(r.outcomes.iter().all(|x| x.conditional_model_pnl.is_none()));
    assert!(r.conditional_ev.is_none());
    for mode in 0..7 {
        let mut a = base.clone();
        match mode {
            0 => a.requests[0].template_id = "disabled".into(),
            1 => a.requests[0].legs[0].side = Side::Sell,
            2 => a.requests[0].exit_policy = "early".into(),
            3 => a.scenarios.scenarios[0].points[0].at_ns = 999,
            4 => a.scenarios.scenarios[0].points[0].iv_shift = Some(d("0")),
            5 => a.view.horizon_ns = 999,
            _ => {
                a.quotes[0].book.spec.exercise_style = ExerciseStyle::Unknown;
                a.requests[0].legs[0].instrument =
                    InstrumentDescriptor::from_spec(&a.quotes[0].book.spec).unwrap();
            }
        }
        let r = analyze_expression(&a, &a.requests[0]);
        assert_ne!(r.support.model_support.status, Status::Valid);
        assert!(r.conditional_ev.is_none());
        assert!(r.outcomes.iter().all(|x| x.conditional_model_pnl.is_none()));
    }
}
#[test]
fn reserve_budget_wallet_and_ambiguity() {
    let base = case("terminal-eth-call");
    let mut a = base.clone();
    a.view.max_loss_budget = d(".0203");
    assert_eq!(
        analyze_expression(&a, &a.requests[0])
            .support
            .modeled_feasibility
            .status,
        Status::Valid
    );
    a.view.max_loss_budget = d(".020299");
    assert_eq!(
        analyze_expression(&a, &a.requests[0])
            .support
            .modeled_feasibility
            .status,
        Status::Rejected
    );
    for mode in 0..7 {
        let mut a = base.clone();
        a.requests[0].terminal_fee_native = Some(d(".001"));
        match mode {
            0 => a.view.max_loss_budget = d(".0203"),
            1 => a.risk_policy.active_and_pending_report = d(".0203"),
            2 => {
                a.requests[0].sleeve = Sleeve::Tail;
                a.risk_policy.rolling_tail_paid_report = d(".0203")
            }
            3 => a.hypothetical_balances[0].cash = d(".0203"),
            4 => a.risk_policy.per_intent_report = d(".0203"),
            5 => a.view.initial_capital = d(".0203"),
            _ => {
                let mut w = a.hypothetical_balances[0].clone();
                w.wallet.account = "second".into();
                a.hypothetical_balances.push(w);
            }
        }
        assert_ne!(
            analyze_expression(&a, &a.requests[0])
                .support
                .modeled_feasibility
                .status,
            Status::Valid
        );
    }
}
#[test]
fn vertical_analytic_tails_and_cost_oracles() {
    let a = case("terminal-vertical");
    let c = certify_vertical(&a, &a.requests[0]);
    assert_eq!(c.status.status, Status::Valid);
    assert_eq!(c.knots, vec![d("0"), d("100"), d("110")]);
    assert_eq!(c.values_at_knots, vec![d("0"), d("0"), d(".1")]);
    assert_eq!(c.segment_slopes, vec![d("0"), d(".01"), d("0")]);
    assert_eq!(c.gross_payoff_lower, Some(d("0")));
    assert_eq!(c.gross_payoff_upper, Some(d(".1")));
    assert_eq!(c.opening_net_spend, Some(d("-.0094")));
    assert_eq!(c.conditional_model_lower, Some(d(".0094")));
    assert_eq!(c.conditional_model_upper, Some(d(".1094")));
    let mut req = a.requests[0].clone();
    req.terminal_fee_native = None;
    let c = certify_vertical(&a, &req);
    assert_eq!(c.gross_payoff_upper, Some(d(".1")));
    assert!(c.conditional_model_upper.is_none());
}
#[test]
fn vertical_rejects_fixing_mismatch_unequal_size_and_preserves_math_without_cost() {
    let base = case("terminal-vertical");
    for mode in 0..4 {
        let mut a = base.clone();
        match mode {
            0 => {
                a.quotes[1].book.spec.settlement_index = "other".into();
                a.requests[0].legs[1].instrument =
                    InstrumentDescriptor::from_spec(&a.quotes[1].book.spec).unwrap()
            }
            1 => a.requests[0].legs[1].quantity_native = d(".02"),
            2 => a.requests[0].legs[1].side = Side::Buy,
            _ => a.requests[0].legs[1].quantity_native = d(".015"),
        };
        assert_ne!(
            certify_vertical(&a, &a.requests[0]).status.status,
            Status::Valid
        );
    }
    let mut a = base.clone();
    a.quotes[0].fee_policy = None;
    let c = certify_vertical(&a, &a.requests[0]);
    assert_eq!(c.gross_payoff_upper, Some(d(".1")));
    assert!(c.opening_net_spend.is_none());
}

#[test]
fn fixing_conflict_in_other_same_domain_spec_invalidates_single_call() {
    let mut a = case("terminal-eth-straddle");
    a.requests[0].template_id = "long_call".into();
    a.requests[0].legs.pop();
    a.scenarios.scenarios[0].points[0].delivery_by_spec_hash[1].1 = d("110");
    let r = analyze_expression(&a, &a.requests[0]);
    assert_eq!(r.support.input_validity.status, Status::Invalid);
    assert!(r.outcomes.iter().all(|o| o.conditional_model_pnl.is_none()));
}
#[test]
fn missing_other_leg_keeps_independent_call_cost() {
    let mut a = case("terminal-eth-straddle");
    a.quotes.pop();
    let r = analyze_expression(&a, &a.requests[0]);
    assert_eq!(r.costs.len(), 1);
    assert_eq!(r.costs[0].total_report_currency, Some(d(".0203")));
    assert_eq!(r.support.modeled_feasibility.status, Status::NotEvaluable);
}
#[test]
fn fully_allocated_standard_wallet_and_terminal_numerical_limitation() {
    let mut a = case("terminal-eth-call");
    a.hypothetical_balances[0].wallet.account_mode = "standard".into();
    assert_eq!(
        analyze_expression(&a, &a.requests[0])
            .support
            .modeled_feasibility
            .status,
        Status::Valid
    );
    a.hypothetical_balances[0].cash = d("999");
    assert_eq!(
        analyze_expression(&a, &a.requests[0])
            .support
            .modeled_feasibility
            .status,
        Status::NotEvaluable
    );
    a.hypothetical_balances[0].cash = d("1001");
    assert_eq!(
        analyze_expression(&a, &a.requests[0])
            .support
            .input_validity
            .status,
        Status::Invalid
    );
    let mut a = case("terminal-eth-call");
    a.requests[0].legs[0].quantity_native = d(".03");
    a.quotes[0].book.asks = vec![
        BookLevel {
            price: d("2"),
            quantity_native: d(".01"),
        },
        BookLevel {
            price: d("3"),
            quantity_native: d(".02"),
        },
    ];
    let r = analyze_expression(&a, &a.requests[0]);
    assert_eq!(r.support.modeled_feasibility.status, Status::NotEvaluable);
    assert!(r.break_even_levels.is_none());
    assert!(r.conditional_ev.is_none());
    assert!(r.reasons.iter().any(|x| x.contains("NUMERICAL_LIMITATION")));
}
#[test]
fn capital_profile_does_not_add_different_currencies() {
    let mut a = case("terminal-eth-call");
    a.hypothetical_balances[0].cash = d("999");
    let mut w = a.hypothetical_balances[0].clone();
    w.wallet.currency = "BTC".into();
    w.cash = d("1");
    a.hypothetical_balances.push(w);
    let r = analyze_expression(&a, &a.requests[0]);
    assert_eq!(r.support.modeled_feasibility.status, Status::NotEvaluable);
}
#[test]
fn probabilities_incomplete_or_non_subjective_never_claim_ev() {
    for mode in 0..5 {
        let mut a = case("terminal-eth-call");
        a.scenarios.probability_source = Some(ProbabilitySource::Subjective);
        for (s, p) in a.scenarios.scenarios.iter_mut().zip([".25", ".5", ".25"]) {
            s.probability = Some(d(p));
        }
        match mode {
            0 => a.scenarios.purpose = ScenarioPurpose::Stress,
            1 => a.scenarios.probability_source = Some(ProbabilitySource::RiskNeutral),
            2 => a.scenarios.probability_source = Some(ProbabilitySource::Empirical),
            3 => a.scenarios.scenarios[0].probability = None,
            _ => {
                a.scenarios.scenarios[0].points[0]
                    .delivery_by_spec_hash
                    .clear();
            }
        }
        let r = analyze_expression(&a, &a.requests[0]);
        assert!(r.conditional_ev.is_none());
    }
    let mut a = case("terminal-eth-call");
    a.requests[0].template_version = "1".into();
    let r = analyze_expression(&a, &a.requests[0]);
    assert_eq!(r.support.model_support.status, Status::Unsupported);
    assert_eq!(r.costs[0].total_report_currency, Some(d(".0203")));
}
#[test]
fn canonical_put_vertical_and_contract_price_units() {
    let mut a = case("terminal-vertical");
    for (q, l) in a.quotes.iter_mut().zip(&mut a.requests[0].legs) {
        q.book.spec.option_type = OptionType::P;
        l.instrument = InstrumentDescriptor::from_spec(&q.book.spec).unwrap();
    }
    a.requests[0].template_id = "put_vertical".into();
    a.requests[0].legs[0].side = Side::Sell;
    a.requests[0].legs[1].side = Side::Buy;
    let c = certify_vertical(&a, &a.requests[0]);
    assert_eq!(c.gross_payoff_lower, Some(d("0")));
    assert_eq!(c.gross_payoff_upper, Some(d(".1")));
    assert_eq!(c.values_at_knots, vec![d(".1"), d(".1"), d("0")]);
    assert_eq!(c.segment_slopes, vec![d("0"), d("-.01"), d("0")]);
    let mut q = a.quotes[0].clone();
    q.book.spec.quantity_unit = QuantityUnit::Contracts;
    q.book.spec.multiplier = d("10");
    q.book.spec.price_unit = PriceUnit::QuotePerContract;
    let c = quote_cost(&q, &leg(&q, Side::Buy, ".01"), 10, "USDC");
    assert_eq!(c.premium_native, Some(d(".04")));
    assert_eq!(c.fee_native, Some(d(".003")));
    assert_eq!(c.total_report_currency, Some(d(".043")));
}
fn rebind_and_readmit(mut a: AnalysisInput) -> AnalysisInput {
    for (q, l) in a.quotes.iter().zip(&mut a.requests[0].legs) {
        l.instrument = InstrumentDescriptor::from_spec(&q.book.spec).unwrap();
    }
    for scenario in &mut a.scenarios.scenarios {
        for point in &mut scenario.points {
            let price = point.delivery_by_spec_hash[0].1.clone();
            point.delivery_by_spec_hash = a
                .quotes
                .iter()
                .map(|q| (canonical_hash(&q.book.spec).unwrap(), price.clone()))
                .collect();
        }
    }
    load_analysis(Request {
        schema_version: REQUEST_SCHEMA.into(),
        cutoff_ns: a.cutoff_ns,
        view_id: a.view.meta.object_id.clone(),
        views: a.view_history,
        scenarios: a.scenarios,
        candidates: a.requests,
        quotes: a.quotes,
        hypothetical_balances: a.hypothetical_balances,
        risk_policy: a.risk_policy,
        source_identities: a.source_identities,
        fixture_name: None,
    })
    .unwrap()
}
#[test]
fn registered_vertical_orientation_rejects_reverse_call_and_put() {
    for kind in [OptionType::C, OptionType::P] {
        let mut a = case("terminal-vertical");
        for q in &mut a.quotes {
            q.book.spec.option_type = kind;
        }
        a.requests[0].template_id = if kind == OptionType::C {
            "call_vertical"
        } else {
            "put_vertical"
        }
        .into();
        if kind == OptionType::C {
            a.requests[0].legs[0].side = Side::Sell;
            a.requests[0].legs[1].side = Side::Buy;
        }
        let a = rebind_and_readmit(a);
        let c = certify_vertical(&a, &a.requests[0]);
        assert_eq!(c.status.status, Status::Unsupported);
        assert!(c.gross_payoff_upper.is_none());
        assert!(c.conditional_model_upper.is_none());
        assert!(c.opening_net_spend.is_some());
        assert_eq!(c.request_id, a.requests[0].request_id);
        for (q, l) in a.quotes.iter().zip(&a.requests[0].legs) {
            assert_eq!(
                quote_cost(q, l, a.cutoff_ns, "USDC").status.status,
                Status::Valid
            );
        }
    }
}
#[test]
fn linear_via_future_has_conditional_terminal_cash_equivalence_only() {
    for name in ["terminal-eth-call", "terminal-vertical"] {
        let mut a = case(name);
        for q in &mut a.quotes {
            q.book.spec.settlement_rule = SettlementRule::LinearViaFuture;
        }
        let a = rebind_and_readmit(a);
        if name == "terminal-eth-call" {
            let r = analyze_expression(&a, &a.requests[0]);
            assert_eq!(r.support.model_support.status, Status::Valid);
            assert_eq!(r.outcomes[2].conditional_model_pnl, Some(d(".0797")));
            assert_eq!(r.support.account_eligibility.status, Status::Unverified);
            assert!(
                r.reasons
                    .iter()
                    .any(|x| x.contains("CASH_EQUIVALENT_TERMINAL_ONLY"))
            );
        } else {
            let c = certify_vertical(&a, &a.requests[0]);
            assert_eq!(c.status.status, Status::Valid);
            assert_eq!(c.conditional_model_upper, Some(d(".1094")));
            assert!(
                c.reasons
                    .iter()
                    .any(|x| x.contains("CASH_EQUIVALENT_TERMINAL_ONLY"))
            );
            assert!(
                c.reasons
                    .iter()
                    .any(|x| x.contains("SHORT_EXECUTION_UNSUPPORTED"))
            );
        }
    }
}
#[test]
fn sentinel_fixing_never_upgrades_to_supported_model() {
    for sentinel in ["UNKNOWN", " unknown ", "UnVeRiFiEd"] {
        for name in ["terminal-eth-call", "terminal-vertical"] {
            let mut a = case(name);
            for q in &mut a.quotes {
                q.book.spec.settlement_index = sentinel.into();
            }
            let a = rebind_and_readmit(a);
            if name == "terminal-eth-call" {
                let r = analyze_expression(&a, &a.requests[0]);
                assert_ne!(r.support.model_support.status, Status::Valid);
                assert!(r.conditional_model_loss_bound.is_none());
                assert!(r.conditional_ev.is_none());
                assert_eq!(r.costs[0].status.status, Status::Valid);
            } else {
                let c = certify_vertical(&a, &a.requests[0]);
                assert_ne!(c.status.status, Status::Valid);
                assert!(c.gross_payoff_upper.is_none());
                assert!(c.conditional_model_upper.is_none());
                assert_eq!(c.opening_net_spend, Some(d("-.0094")));
                assert_eq!(
                    quote_cost(&a.quotes[0], &a.requests[0].legs[0], a.cutoff_ns, "USDC")
                        .status
                        .status,
                    Status::Valid
                );
            }
        }
    }
}
