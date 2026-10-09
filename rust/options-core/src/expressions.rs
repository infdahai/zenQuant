//! Versioned terminal-only paid-long model. Scenario outcomes are conditional mathematics.
use crate::{
    canonical::canonical_hash,
    contracts::*,
    costs::{Diagnostic, legal_quantity, math, need, quote_cost},
    decimal::Dec,
    inputs::select_quote,
};
pub const LONG_MODEL: &str = "terminal_paid_long.rust.v1";
pub const CASH_MODEL: &str = "cash_no_interest.rust.v1";
pub(crate) fn selected<'a>(
    a: &'a AnalysisInput,
    r: &CandidateRequest,
) -> Diagnostic<Vec<&'a QuoteInput>> {
    r.legs
        .iter()
        .map(|l| {
            math(select_quote(
                &a.quotes,
                &l.instrument.spec_hash,
                a.cutoff_ns,
            ))?
            .ok_or_else(|| {
                StatusResult::new(Status::NotEvaluable, "MISSING_VISIBLE_EXACT_SPEC_QUOTE")
            })
        })
        .collect()
}
pub(crate) fn same_fixing(a: &InstrumentSpec, b: &InstrumentSpec) -> bool {
    a.venue == b.venue
        && a.underlying == b.underlying
        && a.expiry_ns == b.expiry_ns
        && a.settlement_index == b.settlement_index
        && a.settlement_window_ns == b.settlement_window_ns
        && a.settlement_rule == b.settlement_rule
        && a.rule_version == b.rule_version
        && a.settlement_currency == b.settlement_currency
}
pub(crate) fn terminal_spec(
    s: &InstrumentSpec,
    report: &str,
    cutoff: u64,
    known: Option<u64>,
) -> Diagnostic<()> {
    need(
        known.is_some_and(|n| n <= cutoff)
            && s.valid_from_ns <= cutoff
            && s.valid_until_ns.is_none_or(|n| s.expiry_ns < n),
        Status::NotEvaluable,
        "TERMINAL_SPEC_KNOWLEDGE_OR_VALID_DOMAIN_MISSING",
    )?;
    need(
        s.expiry_ns > cutoff,
        Status::Unsupported,
        "EXPIRED_MODEL_DOMAIN",
    )?;
    need(
        s.product == Product::LinearOption
            && s.exercise_style == ExerciseStyle::European
            && s.settlement_rule == SettlementRule::LinearCash,
        Status::Unsupported,
        "LINEAR_EUROPEAN_CASH_RULE_REQUIRED",
    )?;
    need(
        [
            &s.quote_currency,
            &s.strike_currency,
            &s.settlement_currency,
            &s.margin_currency,
        ]
        .iter()
        .all(|c| c.as_str() == report),
        Status::Unsupported,
        "TERMINAL_SAME_CURRENCY_REQUIRED",
    )?;
    need(
        s.strike > Dec::zero() && !s.settlement_index.is_empty() && s.settlement_window_ns > 0,
        Status::Invalid,
        "INVALID_TERMINAL_SPEC",
    )
}
pub(crate) fn intrinsic(s: &InstrumentSpec, price: &Dec, q: &Dec) -> Diagnostic<Dec> {
    let x = match s.option_type {
        OptionType::C => math(price.checked_sub(&s.strike))?,
        OptionType::P => math(s.strike.checked_sub(price))?,
    };
    math(x.max(Dec::zero()).checked_mul(q))
}
pub(crate) fn signed_payoff(
    specs: &[&InstrumentSpec],
    legs: &[CandidateLeg],
    base: &[Dec],
    price: &Dec,
) -> Diagnostic<Dec> {
    let mut value = Dec::zero();
    for ((s, l), q) in specs.iter().zip(legs).zip(base) {
        let p = intrinsic(s, price, q)?;
        value = if l.side == Side::Buy {
            math(value.checked_add(&p))?
        } else {
            math(value.checked_sub(&p))?
        };
    }
    Ok(value)
}
/// Slopes after each ordered knot, including the analytic infinite right tail.
pub(crate) fn piecewise(
    specs: &[&InstrumentSpec],
    legs: &[CandidateLeg],
    base: &[Dec],
) -> Diagnostic<(Vec<Dec>, Vec<Dec>, Vec<Dec>)> {
    let mut knots = vec![Dec::zero()];
    knots.extend(specs.iter().map(|s| s.strike.clone()));
    knots.sort();
    knots.dedup();
    let mut values = vec![];
    let mut slopes = vec![];
    for k in &knots {
        values.push(signed_payoff(specs, legs, base, k)?);
        let mut slope = Dec::zero();
        for ((s, l), q) in specs.iter().zip(legs).zip(base) {
            let delta = match s.option_type {
                OptionType::C if k >= &s.strike => q.clone(),
                OptionType::P if k < &s.strike => math(q.checked_neg())?,
                _ => Dec::zero(),
            };
            slope = if l.side == Side::Buy {
                math(slope.checked_add(&delta))?
            } else {
                math(slope.checked_sub(&delta))?
            };
        }
        slopes.push(slope);
    }
    Ok((knots, values, slopes))
}
fn roots(knots: &[Dec], values: &[Dec], slopes: &[Dec], paid: &Dec) -> Diagnostic<Vec<Dec>> {
    let mut roots = vec![];
    for (i, k) in knots.iter().enumerate() {
        let pnl = math(values[i].checked_sub(paid))?;
        if pnl.is_zero() {
            roots.push(k.clone());
        }
        if !slopes[i].is_zero() {
            let root = math(k.checked_sub(&math(pnl.checked_div(&slopes[i]))?))?;
            if root >= *k
                && knots.get(i + 1).is_none_or(|next| root <= *next)
                && !root.is_negative()
            {
                roots.push(root);
            }
        }
    }
    roots.sort();
    roots.dedup();
    Ok(roots)
}
fn support(
    a: &AnalysisInput,
    r: &CandidateRequest,
    quotes: &[&QuoteInput],
) -> Diagnostic<Vec<Dec>> {
    need(
        r.template_version == "v1",
        Status::Unsupported,
        "UNSUPPORTED_TEMPLATE_VERSION",
    )?;
    need(
        r.exit_policy == "expiry_only",
        Status::Unsupported,
        "PATH_OR_EARLY_EXIT_UNSUPPORTED",
    )?;
    need(
        r.legs.iter().all(|l| l.side == Side::Buy),
        Status::Unsupported,
        "SHORT_EXPRESSION_UNSUPPORTED",
    )?;
    let expected = match r.template_id.as_str() {
        "long_call" | "long_put" => 1,
        "long_straddle" | "long_strangle" => 2,
        _ => {
            return Err(StatusResult::new(
                Status::Unsupported,
                "UNSUPPORTED_TEMPLATE",
            ));
        }
    };
    need(
        r.legs.len() == expected,
        Status::Unsupported,
        "TEMPLATE_SHAPE_MISMATCH",
    )?;
    let first = &quotes[0].book.spec;
    let mut base = vec![];
    for (q, l) in quotes.iter().zip(&r.legs) {
        let s = &q.book.spec;
        need(
            math(InstrumentDescriptor::from_spec(s))? == l.instrument,
            Status::Invalid,
            "EXACT_SPEC_BINDING_REQUIRED",
        )?;
        terminal_spec(s, &a.view.report_currency, a.cutoff_ns, q.metadata_known_ns)?;
        need(
            s.underlying == a.view.underlying && same_fixing(first, s),
            Status::Unsupported,
            "COMMON_FIXING_REQUIRED",
        )?;
        base.push(legal_quantity(s, &l.quantity_native)?);
    }
    need(
        base.iter().all(|q| q == &base[0]),
        Status::Unsupported,
        "EQUAL_BASE_QUANTITY_REQUIRED",
    )?;
    let call = quotes
        .iter()
        .find(|q| q.book.spec.option_type == OptionType::C);
    let put = quotes
        .iter()
        .find(|q| q.book.spec.option_type == OptionType::P);
    let shape = match r.template_id.as_str() {
        "long_call" => call.is_some(),
        "long_put" => put.is_some(),
        "long_straddle" => call
            .zip(put)
            .is_some_and(|(c, p)| c.book.spec.strike == p.book.spec.strike),
        "long_strangle" => call
            .zip(put)
            .is_some_and(|(c, p)| p.book.spec.strike < c.book.spec.strike),
        _ => false,
    };
    need(shape, Status::Unsupported, "TEMPLATE_OPTION_SHAPE_MISMATCH")?;
    need(
        a.view.horizon_ns == first.expiry_ns,
        Status::Unsupported,
        "HORIZON_MUST_EQUAL_EXPIRY",
    )?;
    need(
        a.scenarios.scenarios.iter().all(|s| {
            s.points.len() == 1
                && s.points[0].at_ns == first.expiry_ns
                && s.points[0].iv_shift.is_none()
                && s.points[0].liquidity_haircut.is_none()
        }),
        Status::Unsupported,
        "SINGLE_TERMINAL_POINT_REQUIRED_NO_IV_OR_LIQUIDITY_MODEL",
    )?;
    Ok(base)
}
fn funding(
    a: &AnalysisInput,
    r: &CandidateRequest,
    quotes: &[&QuoteInput],
    paid: &Dec,
) -> Diagnostic<ModeledFunding> {
    need(
        a.risk_policy.report_currency == a.view.report_currency,
        Status::NotEvaluable,
        "RISK_POLICY_CURRENCY_MISMATCH",
    )?;
    let mut required: Vec<(WalletKey, Dec)> = vec![];
    if !quotes.is_empty() {
        let venue = &quotes[0].book.spec.venue;
        let currency = &a.view.report_currency;
        let wallets: Vec<_> = a
            .hypothetical_balances
            .iter()
            .filter(|w| &w.wallet.venue == venue && &w.wallet.currency == currency)
            .collect();
        need(
            wallets.len() == 1,
            Status::NotEvaluable,
            "UNIQUE_HYPOTHETICAL_WALLET_REQUIRED_NO_ACCOUNT_POOLING",
        )?;
        let w = wallets[0];
        need(
            w.wallet.account_mode == "standard",
            Status::NotEvaluable,
            "HYPOTHETICAL_ACCOUNT_MODE_UNSUPPORTED",
        )?;
        need(
            w.cash >= *paid,
            Status::Rejected,
            "WALLET_ENTRY_PLUS_TERMINAL_RESERVE_EXCEEDED",
        )?;
        required.push((w.wallet.clone(), paid.clone()));
    }
    let mut total = Dec::zero();
    for w in &a.hypothetical_balances {
        need(
            w.wallet.currency == a.view.report_currency,
            Status::NotEvaluable,
            "CAPITAL_PROFILE_CURRENCY_CONVERSION_UNSUPPORTED",
        )?;
        need(
            !w.cash.is_negative(),
            Status::Invalid,
            "NEGATIVE_HYPOTHETICAL_BALANCE",
        )?;
        total = math(total.checked_add(&w.cash))?;
    }
    need(
        total <= a.view.initial_capital,
        Status::Invalid,
        "HYPOTHETICAL_BALANCES_EXCEED_SINGLE_CAPITAL_PROFILE",
    )?;
    need(
        total == a.view.initial_capital,
        Status::NotEvaluable,
        "HYPOTHETICAL_CAPITAL_ALLOCATION_INCOMPLETE",
    )?;
    let limits = [
        (&a.view.initial_capital, "INITIAL_CAPITAL"),
        (&a.view.max_loss_budget, "VIEW_LOSS_BUDGET"),
        (&a.risk_policy.per_intent_report, "PER_INTENT"),
        (
            &a.risk_policy.active_and_pending_report,
            "GLOBAL_ACTIVE_AND_PENDING",
        ),
    ];
    for (cap, label) in limits {
        need(!cap.is_negative(), Status::Invalid, "NEGATIVE_MODEL_LIMIT")?;
        need(
            paid <= cap,
            Status::Rejected,
            &format!("{label}_ENTRY_PLUS_TERMINAL_RESERVE_EXCEEDED"),
        )?;
    }
    if r.sleeve == Sleeve::Tail {
        need(
            paid <= &a.risk_policy.rolling_tail_paid_report,
            Status::Rejected,
            "TAIL_ENTRY_PLUS_TERMINAL_RESERVE_EXCEEDED",
        )?;
    }
    Ok(ModeledFunding {
        status: StatusResult::new(Status::Valid, "HYPOTHETICAL_REFERENCE_ACCOUNT_ONLY"),
        required_cash_by_wallet: required,
        policy_hash: math(canonical_hash(&a.risk_policy))?,
        reasons: vec!["ACCOUNT_BALANCES_UNVERIFIED_NO_RESERVATION".into()],
    })
}
fn subjective_ev(a: &AnalysisInput, outcomes: &[ScenarioOutcome]) -> Diagnostic<Option<Dec>> {
    if a.scenarios.purpose != ScenarioPurpose::Forecast
        || a.scenarios.probability_source != Some(ProbabilitySource::Subjective)
    {
        return Ok(None);
    }
    if a.scenarios.scenarios.is_empty()
        || outcomes.iter().any(|x| x.conditional_model_pnl.is_none())
        || a.scenarios
            .scenarios
            .iter()
            .any(|s| s.probability.is_none())
    {
        return Ok(None);
    }
    let mut sum = Dec::zero();
    let mut ev = Dec::zero();
    for (s, o) in a.scenarios.scenarios.iter().zip(outcomes) {
        let p = s
            .probability
            .as_ref()
            .ok_or_else(|| StatusResult::new(Status::NotEvaluable, "MISSING_PROBABILITY"))?;
        need(!p.is_negative(), Status::Invalid, "INVALID_PROBABILITY")?;
        sum = math(sum.checked_add(p))?;
        let pnl = o
            .conditional_model_pnl
            .as_ref()
            .ok_or_else(|| StatusResult::new(Status::NotEvaluable, "MISSING_MODEL_PNL"))?;
        ev = math(ev.checked_add(&math(p.checked_mul(pnl))?))?;
    }
    need(
        sum == Dec::one(),
        Status::Invalid,
        "PROBABILITIES_MUST_SUM_EXACTLY_ONE",
    )?;
    Ok(Some(ev))
}
/// Preserves unsupported candidates and independent displayed costs. Never grants account authority.
pub fn analyze_expression(a: &AnalysisInput, r: &CandidateRequest) -> ExpressionAnalysis {
    let unverified = StatusResult::new(Status::Unverified, "NO_ACCOUNT_OR_EXECUTION_EVIDENCE");
    let mut out = ExpressionAnalysis {
        request_id: r.request_id.clone(),
        request_hash: String::new(),
        input_hash: String::new(),
        model_version: LONG_MODEL.into(),
        support: SupportColumns {
            input_validity: StatusResult::new(Status::Valid, "TYPED_DIAGNOSTIC_INPUT"),
            model_support: StatusResult::new(Status::NotEvaluable, "NOT_EVALUATED"),
            modeled_feasibility: StatusResult::new(Status::NotEvaluable, "NOT_EVALUATED"),
            account_eligibility: unverified.clone(),
            execution_evidence: unverified,
        },
        modeled_candidate: None,
        costs: vec![],
        model_funding: None,
        outcomes: a
            .scenarios
            .scenarios
            .iter()
            .map(|s| ScenarioOutcome {
                scenario_id: s.scenario_id.clone(),
                currency: a.view.report_currency.clone(),
                gross_payoff: None,
                conditional_model_pnl: None,
                reason: "NOT_EVALUATED".into(),
            })
            .collect(),
        conditional_ev: None,
        break_even_levels: None,
        conditional_model_loss_bound: None,
        reasons: vec![],
    };
    let run = (|| -> Diagnostic<()> {
        out.request_hash = math(canonical_hash(r))?;
        out.input_hash = math(canonical_hash(a))?;
        for l in &r.legs {
            if let Some(q) = math(select_quote(
                &a.quotes,
                &l.instrument.spec_hash,
                a.cutoff_ns,
            ))? {
                out.costs
                    .push(quote_cost(q, l, a.cutoff_ns, &a.view.report_currency));
            }
        }
        need(
            r.template_version == "v1"
                && matches!(
                    r.template_id.as_str(),
                    "cash" | "long_call" | "long_put" | "long_straddle" | "long_strangle"
                ),
            Status::Unsupported,
            "UNSUPPORTED_TEMPLATE_OR_VERSION",
        )?;
        let quotes = selected(a, r)?;
        if r.template_id == "cash" {
            out.model_version = CASH_MODEL.into();
            need(
                r.template_version == "v1" && r.legs.is_empty() && r.exit_policy == "expiry_only",
                Status::Unsupported,
                "CASH_MODEL_SHAPE_UNSUPPORTED",
            )?;
            for o in &mut out.outcomes {
                o.gross_payoff = Some(Dec::zero());
                o.conditional_model_pnl = Some(Dec::zero());
                o.reason = "EXPLICIT_SAME_CURRENCY_NO_INTEREST_NO_TRADE_MODEL".into();
            }
            out.conditional_model_loss_bound = Some(Dec::zero());
            out.support.model_support = StatusResult::new(Status::Valid, "CASH_NO_INTEREST_MODEL");
            out.conditional_ev = subjective_ev(a, &out.outcomes)?;
            out.model_funding = Some(funding(a, r, &[], &Dec::zero())?);
            out.support.modeled_feasibility = StatusResult::new(Status::Valid, "NO_TRADE_MODEL");
            return Ok(());
        }
        let base = support(a, r, &quotes)?;
        out.support.model_support =
            StatusResult::new(Status::Valid, "TERMINAL_ONLY_CONDITIONAL_MODEL");
        // Validate every same-fixing price before calculating any outcome. A conflict invalidates all.
        for s in &a.scenarios.scenarios {
            let point = &s.points[0];
            let mut price: Option<&Dec> = None;
            for q in &a.quotes {
                if !same_fixing(&quotes[0].book.spec, &q.book.spec) {
                    continue;
                }
                let hash = math(canonical_hash(&q.book.spec))?;
                let entries: Vec<_> = point
                    .delivery_by_spec_hash
                    .iter()
                    .filter(|(h, _)| h == &hash)
                    .collect();
                need(
                    entries.len() <= 1,
                    Status::Invalid,
                    "DUPLICATE_SCENARIO_SPEC_PRICE",
                )?;
                if let Some((_, p)) = entries.first() {
                    need(!p.is_negative(), Status::Invalid, "NEGATIVE_TERMINAL_PRICE")?;
                    need(
                        price.is_none_or(|old| old == p),
                        Status::Invalid,
                        "SCENARIO_FIXING_CONFLICT",
                    )?;
                    price = Some(p);
                }
            }
        }
        let specs: Vec<_> = quotes.iter().map(|q| &q.book.spec).collect();
        for (s, o) in a.scenarios.scenarios.iter().zip(&mut out.outcomes) {
            let point = &s.points[0];
            if r.legs.iter().all(|l| {
                point
                    .delivery_by_spec_hash
                    .iter()
                    .any(|(h, _)| h == &l.instrument.spec_hash)
            }) {
                let p = &point
                    .delivery_by_spec_hash
                    .iter()
                    .find(|(h, _)| h == &r.legs[0].instrument.spec_hash)
                    .ok_or_else(|| {
                        StatusResult::new(Status::NotEvaluable, "MISSING_TERMINAL_PRICE")
                    })?
                    .1;
                o.gross_payoff = Some(signed_payoff(&specs, &r.legs, &base, p)?);
                o.reason = "GROSS_TERMINAL_CONDITIONAL_MODEL".into();
            } else {
                o.reason = "MISSING_TERMINAL_PRICE".into();
            }
        }
        let mut opening = Dec::zero();
        for c in &out.costs {
            if c.status.status != Status::Valid {
                return Err(c.status.clone());
            }
            let spend = c.total_report_currency.as_ref().ok_or_else(|| {
                StatusResult::new(Status::NotEvaluable, "INCOMPLETE_OPENING_COST")
            })?;
            opening = math(opening.checked_add(spend))?;
        }
        let fee = r.terminal_fee_native.as_ref().ok_or_else(|| {
            StatusResult::new(Status::NotEvaluable, "MISSING_TERMINAL_FEE_RESERVE")
        })?;
        need(!fee.is_negative(), Status::Invalid, "NEGATIVE_TERMINAL_FEE")?;
        let paid = math(opening.checked_add(fee))?;
        let (knots, values, slopes) = piecewise(&specs, &r.legs, &base)?;
        out.break_even_levels = Some(roots(&knots, &values, &slopes, &paid)?);
        out.conditional_model_loss_bound = Some(paid.clone());
        for o in &mut out.outcomes {
            if let Some(g) = &o.gross_payoff {
                o.conditional_model_pnl = Some(math(g.checked_sub(&paid))?);
                o.reason = "CONDITIONAL_TERMINAL_PNL_NOT_ACTUAL".into();
            }
        }
        out.conditional_ev = subjective_ev(a, &out.outcomes)?;
        out.modeled_candidate = Some(ModeledCandidate {
            request: r.clone(),
            view_hash: math(canonical_hash(&a.view))?,
            scenario_hash: math(canonical_hash(&a.scenarios))?,
            report_currency: a.view.report_currency.clone(),
        });
        need(
            out.outcomes
                .iter()
                .all(|o| o.conditional_model_pnl.is_some()),
            Status::NotEvaluable,
            "INCOMPLETE_TERMINAL_SCENARIO_SET",
        )?;
        out.model_funding = Some(funding(a, r, &quotes, &paid)?);
        out.support.modeled_feasibility = StatusResult::new(
            Status::Valid,
            "ENTRY_PLUS_TERMINAL_RESERVE_WITHIN_HYPOTHETICAL_LIMITS",
        );
        Ok(())
    })();
    if let Err(s) = run {
        out.reasons.push(s.reason.clone());
        if s.status == Status::Invalid {
            out.support.input_validity = s.clone();
            for o in &mut out.outcomes {
                o.gross_payoff = None;
                o.conditional_model_pnl = None;
                o.reason = s.reason.clone();
            }
            out.conditional_ev = None;
            out.break_even_levels = None;
            out.conditional_model_loss_bound = None;
        }
        if out.support.model_support.status != Status::Valid {
            out.support.model_support = s.clone();
        }
        out.support.modeled_feasibility = s;
    }
    if out.conditional_ev.is_none() {
        out.reasons
            .push("EV_REQUIRES_COMPLETE_FORECAST_SUBJECTIVE_WEIGHTS_AND_NET_OUTCOMES".into());
    }
    out
}
