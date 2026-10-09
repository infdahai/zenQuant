//! Deterministic diagnostics. All authority is fixed here, never supplied by input.
use options_core::{
    canonical::{bytes_sha256, canonical_bytes, canonical_hash},
    contracts::*,
    inputs::ValidatedAnalysis,
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
pub fn render(
    input: &AnalysisInput,
    request_bytes: &[u8],
    verified: Option<crate::io::VerifiedSources>,
) -> options_core::error::Result<Vec<(&'static str, Vec<u8>)>> {
    let validated = ValidatedAnalysis::new(input)?;
    let panel = verified.is_some();
    let pins = verified.map(|v| v.pins().to_vec()).unwrap_or_default();
    let code: Vec<_> = CODE_FILES
        .iter()
        .map(|(name, bytes)| (*name, bytes_sha256(bytes)))
        .collect();
    let identity = Identity {
        request_bytes_sha256: bytes_sha256(request_bytes),
        canonical_input_sha256: canonical_hash(input)?,
        code_sha256: canonical_hash(&code)?,
        method_version: ARITHMETIC_METHOD,
        cargo_lock_sha256: bytes_sha256(LOCK_BYTES),
        source_identities: input.source_identities.clone(),
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
    };
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
