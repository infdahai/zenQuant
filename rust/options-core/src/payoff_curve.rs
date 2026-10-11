//! Bounded batch adapter over the existing terminal model. No signal-driven trade selection.
use crate::{
    canonical::{canonical_bytes, canonical_hash},
    contracts::*,
    decimal::Dec,
    error::{ErrorCode, Result},
    inputs::{require, text},
    terminal_diagnostics::{
        self as terminal, TerminalDiagnostic, TerminalDiagnosticRequest, TerminalFee,
        TerminalScenario, TerminalScenarios,
    },
};
use serde::{Deserialize, Serialize};

pub const REQUEST_SCHEMA: &str = "options-payoff-curve-request.rust.v1";
pub const REPORT_SCHEMA: &str = "options-payoff-curve-report.rust.v1";
pub const SIGNAL_SCHEMA: &str = "manual-scenario-signal.rust.v1";
pub const MAX_POINTS: usize = 4096;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Candidate {
    pub leg: CandidateLeg,
    pub spec: InstrumentSpec,
    pub metadata_known_ns: Option<u64>,
    pub quotes: Vec<QuoteInput>,
    pub terminal_fee: Option<TerminalFee>,
    pub source_identities: Vec<(String, String)>,
}

/// Exact ascending prices: first + step * i. No interpolation or rounding.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grid {
    pub meta: RecordMeta,
    pub spec_hash: String,
    pub at_ns: u64,
    pub first: Dec,
    pub step: Dec,
    pub count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManualSignal {
    pub schema_version: String,
    pub source_ref: String,
    pub source_sha256: String,
    pub observed_ns: u64,
    pub known_ns: u64,
    pub valid_until_ns: u64,
    pub hypothesis: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub schema_version: String,
    pub request_id: String,
    pub cutoff_ns: u64,
    pub report_currency: String,
    pub candidate: Option<Candidate>,
    pub grid: Option<Grid>,
    pub signal: Option<ManualSignal>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Point {
    pub delivery_price: Dec,
    pub gross_payoff: Option<Dec>,
    pub conditional_model_pnl: Option<Dec>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Report {
    pub schema_version: String,
    pub report_currency: String,
    pub cutoff_ns: String,
    pub expiry_ns: Option<String>,
    pub terminal_fee_assumption: Option<Dec>,
    pub input_hash: String,
    pub grid_hash: Option<String>,
    pub signal_hash: Option<String>,
    pub signal_status: StatusResult,
    pub missing: Vec<String>,
    /// First batch evidence; outcomes live exclusively in points.
    pub diagnostic: Option<TerminalDiagnostic>,
    pub points: Vec<Point>,
    pub limitations: Vec<String>,
}

pub fn decode_request(bytes: &[u8]) -> Result<Request> {
    let r = crate::codec::decode(bytes, MAX_REQUEST_BYTES)?;
    validate(&r)?;
    Ok(r)
}

fn validate(r: &Request) -> Result<()> {
    require(r.schema_version == REQUEST_SCHEMA, ErrorCode::InvalidSchema)?;
    require(
        text(&r.request_id) && text(&r.report_currency),
        ErrorCode::InvalidInput,
    )?;
    require(
        canonical_bytes(r)?.len() <= MAX_REQUEST_BYTES,
        ErrorCode::LimitExceeded,
    )?;
    if let Some(g) = &r.grid {
        require(
            (2..=MAX_POINTS).contains(&g.count),
            ErrorCode::LimitExceeded,
        )?;
        require(
            !g.first.is_negative() && g.step > Dec::zero(),
            ErrorCode::InvalidInput,
        )?;
        g.meta.validate(terminal::SCENARIOS_SCHEMA)?;
        require(g.meta.cutoff_ns <= r.cutoff_ns, ErrorCode::InvalidTimestamp)?;
        require(
            g.meta.revision == 1 && valid_sha256(&g.spec_hash),
            ErrorCode::InvalidMetadata,
        )?;
    }
    if let Some(s) = &r.signal {
        require(s.schema_version == SIGNAL_SCHEMA, ErrorCode::InvalidSchema)?;
        require(
            text(&s.source_ref) && text(&s.hypothesis) && valid_sha256(&s.source_sha256),
            ErrorCode::InvalidInput,
        )?;
        require(
            s.observed_ns <= s.known_ns && s.known_ns < s.valid_until_ns,
            ErrorCode::InvalidTimestamp,
        )?;
    }
    Ok(())
}

pub fn analyze(r: &Request) -> Result<Report> {
    validate(r)?;
    let signal_status = match &r.signal {
        None => StatusResult::new(
            Status::NotEvaluable,
            "NO_SIGNAL_INDEPENDENT_OPTIONS_ANALYSIS",
        ),
        Some(s) if s.known_ns > r.cutoff_ns => {
            StatusResult::new(Status::NotEvaluable, "SIGNAL_NOT_YET_KNOWN")
        }
        Some(s) if r.cutoff_ns >= s.valid_until_ns => {
            StatusResult::new(Status::NotEvaluable, "SIGNAL_EXPIRED")
        }
        Some(_) => StatusResult::new(
            Status::Unverified,
            "MANUAL_HYPOTHESIS_NOT_PREDICTIVE_EVIDENCE",
        ),
    };
    let mut out = Report {
        schema_version: REPORT_SCHEMA.into(),
        report_currency: r.report_currency.clone(),
        cutoff_ns: r.cutoff_ns.to_string(),
        expiry_ns: r.candidate.as_ref().map(|c| c.spec.expiry_ns.to_string()),
        terminal_fee_assumption: r
            .candidate
            .as_ref()
            .and_then(|c| c.terminal_fee.as_ref().map(|f| f.amount_report.clone())),
        input_hash: canonical_hash(r)?,
        grid_hash: r.grid.as_ref().map(canonical_hash).transpose()?,
        signal_hash: r.signal.as_ref().map(canonical_hash).transpose()?,
        signal_status,
        missing: vec![],
        diagnostic: None,
        points: vec![],
        limitations: vec![
            "EXPIRY_SCENARIOS_NOT_BACKTEST_OR_CURRENT_MARK".into(),
            "NO_PROBABILITIES_OR_EXPECTED_RETURN".into(),
            "NO_ACCOUNT_RISK_OR_FILL_AUTHORITY".into(),
            "COSTS_CONDITIONAL_ON_SUPPLIED_QUOTE_NO_SLIPPAGE_FORECAST".into(),
        ],
    };
    if r.candidate.is_none() {
        out.missing.push("CANDIDATE_AND_CONTRACT".into());
    }
    if r.grid.is_none() {
        out.missing.push("EXPLICIT_PRICE_SCENARIOS".into());
    }
    let (Some(c), Some(g)) = (&r.candidate, &r.grid) else {
        return Ok(out);
    };
    require(
        g.spec_hash == canonical_hash(&c.spec)?,
        ErrorCode::InvalidIdentity,
    )?;
    require(c.quotes.len() <= 4, ErrorCode::LimitExceeded)?;
    // V1 is deliberately narrower than the legacy terminal model.
    require(
        c.spec.settlement_rule == SettlementRule::LinearCash,
        ErrorCode::Unsupported,
    )?;
    let mut request = TerminalDiagnosticRequest {
        schema_version: terminal::REQUEST_SCHEMA.into(),
        request_id: r.request_id.clone(),
        cutoff_ns: r.cutoff_ns,
        report_currency: r.report_currency.clone(),
        leg: c.leg.clone(),
        spec: c.spec.clone(),
        metadata_known_ns: c.metadata_known_ns,
        scenarios: TerminalScenarios {
            meta: g.meta.clone(),
            spec_hash: g.spec_hash.clone(),
            scenarios: vec![],
        },
        quotes: c.quotes.clone(),
        terminal_fee: c.terminal_fee.clone(),
        source_identities: c.source_identities.clone(),
    };
    let mut price = g.first.clone();
    while out.points.len() < g.count {
        let count = MAX_SCENARIOS.min(g.count - out.points.len());
        request.scenarios.scenarios.clear();
        for i in 0..count {
            request.scenarios.scenarios.push(TerminalScenario {
                scenario_id: format!("price-{}", out.points.len() + i),
                at_ns: g.at_ns,
                delivery_price: price.clone(),
            });
            if out.points.len() + i + 1 < g.count {
                price = price.checked_add(&g.step)?;
            }
        }
        let mut diagnostic = terminal::analyze(&request)?;
        if let Some(first) = &out.diagnostic {
            // A later arithmetic failure must not leave a seemingly valid first-batch summary.
            require(
                first.support == diagnostic.support && first.net_status == diagnostic.net_status,
                ErrorCode::NumericalLimitation,
            )?;
        }
        for (s, p) in request.scenarios.scenarios.iter().zip(&diagnostic.outcomes) {
            out.points.push(Point {
                delivery_price: s.delivery_price.clone(),
                gross_payoff: p.gross_payoff.clone(),
                conditional_model_pnl: p.conditional_model_pnl.clone(),
            });
        }
        if out.diagnostic.is_none() {
            diagnostic.outcomes.clear();
            out.diagnostic = Some(diagnostic);
        }
    }
    Ok(out)
}
