//! Static signed vertical proof over all nonnegative fixing prices, including the right tail.
//! A certificate is never a financed short position or a lifecycle arbitrage claim.
use crate::{
    canonical::canonical_hash,
    contracts::*,
    costs::{Diagnostic, legal_quantity, math, need, quote_cost},
    decimal::Dec,
    expressions::{piecewise, same_fixing, selected, terminal_spec},
};
pub const VERTICAL_MODEL: &str = "linear_vertical_payoff.rust.v1";
pub fn certify_vertical(a: &AnalysisInput, r: &CandidateRequest) -> ConstraintCertificate {
    let mut out = ConstraintCertificate {
        request_id: r.request_id.clone(),
        input_hash: String::new(),
        method_version: VERTICAL_MODEL.into(),
        status: StatusResult::new(Status::NotEvaluable, "NOT_EVALUATED"),
        spec_hashes: r
            .legs
            .iter()
            .map(|l| l.instrument.spec_hash.clone())
            .collect(),
        currency: None,
        knots: vec![],
        values_at_knots: vec![],
        segment_slopes: vec![],
        gross_payoff_lower: None,
        gross_payoff_upper: None,
        opening_net_spend: None,
        conditional_model_lower: None,
        conditional_model_upper: None,
        reasons: vec![
            "SHORT_EXECUTION_UNSUPPORTED".into(),
            "FINANCING_MARGIN_ACCOUNT_EXECUTION_UNVERIFIED".into(),
            "STATIC_MATHEMATICS_NO_LIFECYCLE_EDGE".into(),
        ],
    };
    let run =
        (|| -> Diagnostic<()> {
            out.input_hash = math(canonical_hash(&(a, r)))?;
            need(
                r.template_version == "v1",
                Status::Unsupported,
                "UNSUPPORTED_TEMPLATE_VERSION",
            )?;
            let kind = match r.template_id.as_str() {
                "call_vertical" => OptionType::C,
                "put_vertical" => OptionType::P,
                _ => {
                    return Err(StatusResult::new(
                        Status::Unsupported,
                        "UNSUPPORTED_VERTICAL_TEMPLATE",
                    ));
                }
            };
            need(
                r.legs.len() == 2 && r.exit_policy == "expiry_only",
                Status::Unsupported,
                "STATIC_TWO_LEG_VERTICAL_REQUIRED",
            )?;
            need(
                r.legs.iter().filter(|l| l.side == Side::Buy).count() == 1
                    && r.legs.iter().filter(|l| l.side == Side::Sell).count() == 1,
                Status::Unsupported,
                "ONE_BUY_ONE_SELL_REQUIRED",
            )?;
            let quotes = selected(a, r)?;
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
                    s.option_type == kind && s.underlying == a.view.underlying,
                    Status::Unsupported,
                    "VERTICAL_OPTION_TYPE_OR_UNDERLYING_MISMATCH",
                )?;
                base.push(legal_quantity(s, &l.quantity_native)?);
            }
            need(
                same_fixing(&quotes[0].book.spec, &quotes[1].book.spec),
                Status::Unsupported,
                "COMMON_VENUE_FIXING_AND_RULE_REQUIRED",
            )?;
            need(
                base[0] == base[1],
                Status::Unsupported,
                "VERTICAL_EQUAL_BASE_QUANTITY_REQUIRED",
            )?;
            need(
                quotes[0].book.spec.strike != quotes[1].book.spec.strike,
                Status::Unsupported,
                "DISTINCT_VERTICAL_STRIKES_REQUIRED",
            )?;
            let specs: Vec<_> = quotes.iter().map(|q| &q.book.spec).collect();
            let (knots, values, slopes) = piecewise(&specs, &r.legs, &base)?;
            need(
                slopes.last() == Some(&Dec::zero()),
                Status::Unsupported,
                "NONZERO_RIGHT_TAIL_NO_FINITE_BOUND",
            )?;
            let lower =
                values.iter().min().cloned().ok_or_else(|| {
                    StatusResult::new(Status::NotEvaluable, "MISSING_ANALYTIC_NODES")
                })?;
            let upper =
                values.iter().max().cloned().ok_or_else(|| {
                    StatusResult::new(Status::NotEvaluable, "MISSING_ANALYTIC_NODES")
                })?;
            out.currency = Some(a.view.report_currency.clone());
            out.knots = knots;
            out.values_at_knots = values;
            out.segment_slopes = slopes;
            out.gross_payoff_lower = Some(lower.clone());
            out.gross_payoff_upper = Some(upper.clone());
            out.status = StatusResult::new(Status::Valid, "FULL_DOMAIN_STATIC_PAYOFF_PROOF_ONLY");
            let mut spend = Dec::zero();
            let mut complete = true;
            for (q, l) in quotes.iter().zip(&r.legs) {
                let c = quote_cost(q, l, a.cutoff_ns, &a.view.report_currency);
                if let Some(cost) = c
                    .total_report_currency
                    .filter(|_| c.status.status == Status::Valid)
                {
                    spend = if l.side == Side::Buy {
                        math(spend.checked_add(&cost))?
                    } else {
                        math(spend.checked_sub(&cost))?
                    };
                } else {
                    complete = false;
                    out.reasons.push(c.status.reason);
                }
            }
            if complete {
                out.opening_net_spend = Some(spend.clone());
                if let Some(fee) = &r.terminal_fee_native {
                    need(!fee.is_negative(), Status::Invalid, "NEGATIVE_TERMINAL_FEE")?;
                    let paid = math(spend.checked_add(fee))?;
                    out.conditional_model_lower = Some(math(lower.checked_sub(&paid))?);
                    out.conditional_model_upper = Some(math(upper.checked_sub(&paid))?);
                } else {
                    out.reasons.push("MISSING_TERMINAL_FEE_RESERVE".into());
                }
            }
            Ok(())
        })();
    if let Err(s) = run {
        out.reasons.push(s.reason.clone());
        out.status = s;
        out.conditional_model_lower = None;
        out.conditional_model_upper = None;
    }
    out
}
