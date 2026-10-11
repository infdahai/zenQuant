//! Deterministic diagnostics. All authority is fixed here, never supplied by input.
use options_core::{
    canonical::{bytes_sha256, canonical_bytes, canonical_hash},
    contracts::*,
    inputs::ValidatedAnalysis,
    quote_diagnostics::{self, QuoteDiagnostic, QuoteDiagnosticRequest},
    terminal_diagnostics::{self, TerminalDiagnostic, TerminalDiagnosticRequest},
};
use serde::Serialize;
include!(concat!(env!("OUT_DIR"), "/identities.rs"));
#[derive(Serialize)]
struct Identity {
    pub request_bytes_sha256: String,
    pub canonical_input_sha256: String,
    pub code_sha256: String,
    pub method_version: &'static str,
    pub cargo_lock_sha256: String,
    pub source_identities: Vec<(String, String)>,
    pub verified_raw_sources: Vec<PinnedSource>,
    pub raw_source_integrity: &'static str,
    pub projection_trust: &'static str,
}
#[derive(Serialize)]
struct Report<'a> {
    pub schema_version: &'static str,
    pub diagnostic_only: bool,
    pub rankable: bool,
    pub live_eligible: bool,
    pub execution_authority: &'static str,
    pub actual_pnl: Option<String>,
    pub paper_pnl: Option<String>,
    pub sharpe: Option<String>,
    pub capacity: Option<String>,
    pub lifecycle_arbitrage_edge: Option<String>,
    pub identity: Identity,
    pub input: &'a AnalysisInput,
    pub expressions: Vec<ExpressionAnalysis>,
    pub constraints: Vec<ConstraintCertificate>,
}
fn identity<T: Serialize>(
    input: &T,
    request_bytes: &[u8],
    source_identities: &[(String, String)],
    verified: Option<crate::io::VerifiedSources>,
) -> options_core::error::Result<Identity> {
    let panel = verified.is_some();
    let pins = verified.map(|v| v.pins().to_vec()).unwrap_or_default();
    let code: Vec<_> = CODE_FILES
        .iter()
        .map(|(name, bytes)| (*name, bytes_sha256(bytes)))
        .collect();
    Ok(Identity {
        request_bytes_sha256: bytes_sha256(request_bytes),
        canonical_input_sha256: canonical_hash(input)?,
        code_sha256: canonical_hash(&code)?,
        method_version: ARITHMETIC_METHOD,
        cargo_lock_sha256: bytes_sha256(LOCK_BYTES),
        source_identities: source_identities.to_vec(),
        verified_raw_sources: pins,
        raw_source_integrity: if panel {
            "RAW_PINNED_BYTES_VERIFIED"
        } else {
            "NOT_INDEPENDENTLY_VERIFIED"
        },
        projection_trust: if panel {
            "EXPORTED_PROJECTION_UNATTESTED"
        } else {
            "MANUAL_UNATTESTED"
        },
    })
}
pub fn render(
    input: &AnalysisInput,
    request_bytes: &[u8],
    verified: Option<crate::io::VerifiedSources>,
) -> options_core::error::Result<Vec<(&'static str, Vec<u8>)>> {
    let validated = ValidatedAnalysis::new(input)?;
    let identity = identity(input, request_bytes, &input.source_identities, verified)?;
    let expressions = input
        .requests
        .iter()
        .map(|r| validated.analyze_expression(r))
        .collect();
    let constraints = input
        .requests
        .iter()
        .filter(|r| matches!(r.template_id.as_str(), "call_vertical" | "put_vertical"))
        .map(|r| validated.certify_vertical(r))
        .collect();
    let report = Report {
        schema_version: REPORT_SCHEMA,
        diagnostic_only: true,
        rankable: false,
        live_eligible: false,
        execution_authority: "NONE",
        actual_pnl: None,
        paper_pnl: None,
        sharpe: None,
        capacity: None,
        lifecycle_arbitrage_edge: None,
        identity,
        input,
        expressions,
        constraints,
    };
    let json = canonical_bytes(&report)?;
    let identity = canonical_bytes(&report.identity)?;
    // The escaped full structured diagnostic preserves every view revision, hypothesis,
    // gap, cost, reason and constraint without introducing input-driven markup.
    let pretty = serde_json::to_string_pretty(&report)
        .map_err(|_| options_core::error::Error::new(options_core::error::ErrorCode::Internal))?;
    let mut summaries = String::new();
    for expression in &report.expressions {
        summaries.push_str(&format!(
            "<h2>Candidate: {}</h2>\n\n",
            escape_markup(&expression.request_id).replace('\n', "&#10;")
        ));
        let details = serde_json::to_string_pretty(expression).map_err(|_| {
            options_core::error::Error::new(options_core::error::ErrorCode::Internal)
        })?;
        summaries.push_str(&format!("<p>Five separate support columns, independent costs, conditional scenario outcomes and reasons:</p>\n<pre>{}</pre>\n\n",escape_markup(&details)));
    }
    let md = format!(
        "# Offline options diagnostics\n\nDiagnostic only. Execution authority: NONE. Rankable: false. Live eligible: false.\n\nConditional model PnL is mathematical scenario output. Actual/paper PnL, Sharpe, capacity and lifecycle arbitrage edge are unknown.\n\n{}Complete timeline, source identities, constraints, hypotheses and gaps:\n\n<pre>{}</pre>\n",
        summaries,
        escape_markup(&pretty)
    );
    Ok(vec![
        ("report.json", json),
        ("REPORT.md", md.into_bytes()),
        ("identity.json", identity),
    ])
}

#[derive(Serialize)]
struct QuoteReport<'a> {
    schema_version: &'static str,
    diagnostic_only: bool,
    rankable: bool,
    live_eligible: bool,
    execution_authority: &'static str,
    actual_pnl: Option<String>,
    paper_pnl: Option<String>,
    sharpe: Option<String>,
    capacity: Option<String>,
    lifecycle_arbitrage_edge: Option<String>,
    identity: Identity,
    input: &'a QuoteDiagnosticRequest,
    quote_diagnostics: Vec<QuoteDiagnostic>,
}

/// Separate schema and input boundary. Never creates a View or claims funding support.
pub fn render_quote_diagnostics(
    input: &QuoteDiagnosticRequest,
    request_bytes: &[u8],
) -> options_core::error::Result<Vec<(&'static str, Vec<u8>)>> {
    let diagnostics = quote_diagnostics::analyze(input)?;
    let report = QuoteReport {
        schema_version: quote_diagnostics::REPORT_SCHEMA,
        diagnostic_only: true,
        rankable: false,
        live_eligible: false,
        execution_authority: "NONE",
        actual_pnl: None,
        paper_pnl: None,
        sharpe: None,
        capacity: None,
        lifecycle_arbitrage_edge: None,
        identity: identity(input, request_bytes, &input.source_identities, None)?,
        input,
        quote_diagnostics: diagnostics,
    };
    let pretty = serde_json::to_string_pretty(&report)
        .map_err(|_| options_core::error::Error::new(options_core::error::ErrorCode::Internal))?;
    let md = format!(
        "# Offline option quote diagnostics\n\nDiagnostic only. Execution authority: NONE. Rankable: false. Live eligible: false.\n\nBUY total is displayed spend including fees; SELL total is displayed net proceeds after fees. Each request is independent. No fill, account risk, funding, terminal payoff or arbitrage conclusion is established.\n\nRaw sources are not independently verified; input provenance labels grant no authority. Actual/paper PnL, Sharpe, capacity and lifecycle arbitrage edge are unknown.\n\nComplete input, selected evidence and diagnostics:\n\n<pre>{}</pre>\n",
        escape_markup(&pretty)
    );
    Ok(vec![
        ("report.json", canonical_bytes(&report)?),
        ("REPORT.md", md.into_bytes()),
        ("identity.json", canonical_bytes(&report.identity)?),
    ])
}
#[derive(Serialize)]
struct TerminalReport<'a> {
    schema_version: &'static str,
    diagnostic_only: bool,
    rankable: bool,
    live_eligible: bool,
    execution_authority: &'static str,
    actual_pnl: Option<String>,
    paper_pnl: Option<String>,
    sharpe: Option<String>,
    capacity: Option<String>,
    lifecycle_arbitrage_edge: Option<String>,
    identity: Identity,
    input: &'a TerminalDiagnosticRequest,
    terminal_diagnostic: TerminalDiagnostic,
}

pub fn render_terminal_diagnostics(
    input: &TerminalDiagnosticRequest,
    request_bytes: &[u8],
) -> options_core::error::Result<Vec<(&'static str, Vec<u8>)>> {
    let diagnostic = terminal_diagnostics::analyze(input)?;
    let report = TerminalReport {
        schema_version: terminal_diagnostics::REPORT_SCHEMA,
        diagnostic_only: true,
        rankable: false,
        live_eligible: false,
        execution_authority: "NONE",
        actual_pnl: None,
        paper_pnl: None,
        sharpe: None,
        capacity: None,
        lifecycle_arbitrage_edge: None,
        identity: identity(input, request_bytes, &input.source_identities, None)?,
        input,
        terminal_diagnostic: diagnostic,
    };
    let pretty = serde_json::to_string_pretty(&report)
        .map_err(|_| options_core::error::Error::new(options_core::error::ErrorCode::Internal))?;
    let md = format!(
        "# Offline single-leg terminal scenarios\n\nDiagnostic only. Execution authority: NONE. Rankable: false. Live eligible: false.\n\nGross payoff is conditional expiry mathematics. Conditional net PnL subtracts displayed opening cost and the explicit total terminal fee assumption; neither is an actual fill or realized PnL. Funding feasibility, account eligibility, exercise lifecycle and execution are not established. No probabilities, EV, early-exit valuation or arbitrage conclusion.\n\nRaw sources are not independently verified. Complete input and diagnostic:\n\n<pre>{}</pre>\n",
        escape_markup(&pretty)
    );
    Ok(vec![
        ("report.json", canonical_bytes(&report)?),
        ("REPORT.md", md.into_bytes()),
        ("identity.json", canonical_bytes(&report.identity)?),
    ])
}

pub fn escape_markup(value: &str) -> String {
    let mut out = String::new();
    for c in value.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            '\n' => out.push('\n'),
            c if c.is_control() || matches!(c, '\u{202a}'..='\u{202e}'|'\u{2066}'..='\u{2069}') => {
                out.push_str(&format!("&#{};", c as u32))
            }
            c => out.push(c),
        }
    }
    out
}
