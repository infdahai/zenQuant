//! Single paid-long terminal scenarios, independently admitted without View or funding.
//! Gross payoff depends on spec/scenario evidence. Quotes only support conditional net PnL.
use crate::{
    canonical::{canonical_bytes, canonical_hash},
    contracts::*,
    costs::{Diagnostic, legal_quantity, math, need, quote_cost},
    decimal::Dec,
    error::{ErrorCode, Result},
    expressions::{intrinsic, terminal_spec},
    inputs::{identities, require, select_quote, text, validate_quotes, validate_spec},
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub const REQUEST_SCHEMA: &str = "options-terminal-diagnostic-request.rust.v1";
pub const REPORT_SCHEMA: &str = "options-terminal-diagnostic-report.rust.v1";
pub const SCENARIOS_SCHEMA: &str = "options-terminal-scenarios.rust.v1";
pub const MODEL_VERSION: &str = "single_paid_long_terminal.rust.v1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TerminalScenario {
    pub scenario_id: String,
    pub at_ns: u64,
    pub delivery_price: Dec,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TerminalScenarios {
    pub meta: RecordMeta,
    pub spec_hash: String,
    pub scenarios: Vec<TerminalScenario>,
}

/// Explicit total fee assumption for this leg in report currency, not venue attestation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TerminalFee {
    pub amount_report: Dec,
    pub known_ns: u64,
    pub source_ref: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TerminalDiagnosticRequest {
    pub schema_version: String,
    pub request_id: String,
    pub cutoff_ns: u64,
    pub report_currency: String,
    pub leg: CandidateLeg,
    pub spec: InstrumentSpec,
    pub metadata_known_ns: Option<u64>,
    pub scenarios: TerminalScenarios,
    pub quotes: Vec<QuoteInput>,
    pub terminal_fee: Option<TerminalFee>,
    pub source_identities: Vec<(String, String)>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TerminalDiagnostic {
    pub request_id: String,
    pub input_hash: String,
    pub spec_hash: String,
    pub scenario_hash: String,
    pub model_version: String,
    pub support: SupportColumns,
    pub selected_quote_hash: Option<String>,
    pub cost: Option<CostEvidence>,
    pub net_status: StatusResult,
    pub outcomes: Vec<ScenarioOutcome>,
    pub reasons: Vec<String>,
}

pub fn decode_request(bytes: &[u8]) -> Result<TerminalDiagnosticRequest> {
    let r = crate::codec::decode(bytes, MAX_REQUEST_BYTES)?;
    validate_request(&r)?;
    Ok(r)
}

fn validate_request(r: &TerminalDiagnosticRequest) -> Result<()> {
    require(r.schema_version == REQUEST_SCHEMA, ErrorCode::InvalidSchema)?;
    require(
        !r.scenarios.scenarios.is_empty()
            && r.scenarios.scenarios.len() <= MAX_SCENARIOS
            && r.quotes.len() <= MAX_QUOTES,
        ErrorCode::LimitExceeded,
    )?;
    require(
        canonical_bytes(r)?.len() <= MAX_REQUEST_BYTES,
        ErrorCode::LimitExceeded,
    )?;
    require(
        text(&r.request_id) && text(&r.report_currency),
        ErrorCode::InvalidInput,
    )?;
    identities(&r.source_identities)?;
    validate_spec(&r.spec)?;
    require(
        r.leg.instrument == InstrumentDescriptor::from_spec(&r.spec)?
            && r.leg.quantity_native > Dec::zero(),
        ErrorCode::InvalidIdentity,
    )?;
    r.scenarios.meta.validate(SCENARIOS_SCHEMA)?;
    require(
        r.scenarios.meta.cutoff_ns <= r.cutoff_ns,
        ErrorCode::InvalidTimestamp,
    )?;
    // This boundary accepts a self-contained snapshot, not an unverified revision chain.
    require(r.scenarios.meta.revision == 1, ErrorCode::InvalidMetadata)?;
    require(
        r.scenarios.spec_hash == canonical_hash(&r.spec)?,
        ErrorCode::InvalidIdentity,
    )?;
    let mut ids = BTreeSet::new();
    for s in &r.scenarios.scenarios {
        require(
            text(&s.scenario_id) && ids.insert(&s.scenario_id) && !s.delivery_price.is_negative(),
            ErrorCode::InvalidInput,
        )?;
    }
    validate_quotes(&r.quotes, &r.report_currency)?;
    for q in &r.quotes {
        require(q.book.spec == r.spec, ErrorCode::InvalidIdentity)?;
    }
    if let Some(fee) = &r.terminal_fee {
        require(
            !fee.amount_report.is_negative() && text(&fee.source_ref),
            ErrorCode::InvalidInput,
        )?;
    }
    Ok(())
}

/// No wallet/budget admission exists here. Unsupported models retain independent costs
/// but never return payoff values. Structurally invalid evidence returns a typed error.
pub fn analyze(r: &TerminalDiagnosticRequest) -> Result<TerminalDiagnostic> {
    validate_request(r)?;
    let selected = select_quote(&r.quotes, &r.leg.instrument.spec_hash, r.cutoff_ns)?;
    let mut out = TerminalDiagnostic {
        request_id: r.request_id.clone(),
        input_hash: canonical_hash(r)?,
        spec_hash: canonical_hash(&r.spec)?,
        scenario_hash: canonical_hash(&r.scenarios)?,
        model_version: MODEL_VERSION.into(),
        support: SupportColumns {
            input_validity: StatusResult::new(Status::Valid, "ADMITTED_TERMINAL_DIAGNOSTIC"),
            model_support: StatusResult::new(Status::NotEvaluable, "NOT_EVALUATED"),
            modeled_feasibility: StatusResult::new(Status::NotEvaluable, "FUNDING_NOT_ASSESSED"),
            account_eligibility: StatusResult::new(Status::Unverified, "NO_ACCOUNT_EVIDENCE"),
            execution_evidence: StatusResult::new(Status::Unverified, "NO_EXECUTION_EVIDENCE"),
        },
        selected_quote_hash: selected.map(canonical_hash).transpose()?,
        cost: selected.map(|q| quote_cost(q, &r.leg, r.cutoff_ns, &r.report_currency)),
        net_status: StatusResult::new(Status::NotEvaluable, "NOT_EVALUATED"),
        outcomes: r
            .scenarios
            .scenarios
            .iter()
            .map(|s| ScenarioOutcome {
                scenario_id: s.scenario_id.clone(),
                currency: r.report_currency.clone(),
                gross_payoff: None,
                conditional_model_pnl: None,
                reason: "NOT_EVALUATED".into(),
            })
            .collect(),
        reasons: vec![
            "CONDITIONAL_MATHEMATICS_NO_FUNDING_OR_FILL".into(),
            "NO_PROBABILITIES_OR_EV_MODEL".into(),
        ],
    };
    let gross = (|| -> Diagnostic<Vec<Dec>> {
        need(
            r.leg.side == Side::Buy,
            Status::Unsupported,
            "SINGLE_LONG_LEG_REQUIRED",
        )?;
        terminal_spec(
            &r.spec,
            &r.report_currency,
            r.cutoff_ns,
            r.metadata_known_ns,
        )?;
        need(
            r.scenarios
                .scenarios
                .iter()
                .all(|s| s.at_ns == r.spec.expiry_ns),
            Status::Unsupported,
            "EVERY_SCENARIO_MUST_EQUAL_EXPIRY",
        )?;
        let base = legal_quantity(&r.spec, &r.leg.quantity_native)?;
        r.scenarios
            .scenarios
            .iter()
            .map(|s| intrinsic(&r.spec, &s.delivery_price, &base))
            .collect()
    })();
    let gross = match gross {
        Ok(gross) => gross,
        Err(status) => {
            out.support.model_support = status.clone();
            out.net_status = status.clone();
            for o in &mut out.outcomes {
                o.reason = status.reason.clone();
            }
            return Ok(out);
        }
    };
    out.support.model_support =
        StatusResult::new(Status::Valid, "SINGLE_LONG_TERMINAL_CONDITIONAL_MODEL");
    if r.spec.settlement_rule == SettlementRule::LinearViaFuture {
        out.reasons
            .push("CASH_EQUIVALENT_TERMINAL_ONLY_NO_FUTURE_EVENTS_OR_ACCOUNT_PERMISSION".into());
    }
    for (o, value) in out.outcomes.iter_mut().zip(&gross) {
        o.gross_payoff = Some(value.clone());
        o.reason = "GROSS_TERMINAL_CONDITIONAL_MODEL".into();
    }
    let net = (|| -> Diagnostic<Vec<Dec>> {
        let cost = out.cost.as_ref().ok_or_else(|| {
            StatusResult::new(Status::NotEvaluable, "MISSING_VISIBLE_EXACT_SPEC_QUOTE")
        })?;
        if cost.status.status != Status::Valid {
            return Err(cost.status.clone());
        }
        let opening = cost
            .total_report_currency
            .as_ref()
            .ok_or_else(|| StatusResult::new(Status::NotEvaluable, "INCOMPLETE_OPENING_COST"))?;
        let fee = r.terminal_fee.as_ref().ok_or_else(|| {
            StatusResult::new(Status::NotEvaluable, "MISSING_TERMINAL_FEE_ASSUMPTION")
        })?;
        need(
            fee.known_ns <= r.cutoff_ns,
            Status::NotEvaluable,
            "TERMINAL_FEE_NOT_YET_KNOWN",
        )?;
        let paid = math(opening.checked_add(&fee.amount_report))?;
        gross
            .iter()
            .map(|value| math(value.checked_sub(&paid)))
            .collect()
    })();
    out.net_status = match net {
        Ok(net) => {
            for (o, value) in out.outcomes.iter_mut().zip(net) {
                o.conditional_model_pnl = Some(value);
                o.reason = "CONDITIONAL_TERMINAL_PNL_NOT_ACTUAL".into();
            }
            StatusResult::new(Status::Valid, "CONDITIONAL_NET_ONLY_NO_FUNDING_OR_FILL")
        }
        Err(status) => status,
    };
    Ok(out)
}
