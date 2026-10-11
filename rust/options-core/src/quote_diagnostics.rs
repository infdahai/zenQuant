//! Independent displayed-cost diagnostics. No View, scenario, wallet or decision source.
//! The strict v1 workbench request remains a separate contract.
use crate::{
    canonical::{canonical_bytes, canonical_hash},
    contracts::*,
    costs::quote_cost,
    error::{ErrorCode, Result},
    inputs::{identities, require, select_quote, text, validate_leg, validate_quotes},
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub const REQUEST_SCHEMA: &str = "options-quote-diagnostic-request.rust.v1";
pub const REPORT_SCHEMA: &str = "options-quote-diagnostic-report.rust.v1";
pub const MAX_DIAGNOSTICS: usize = 64;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QuoteCostRequest {
    pub request_id: String,
    pub leg: CandidateLeg,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QuoteDiagnosticRequest {
    pub schema_version: String,
    pub cutoff_ns: u64,
    pub report_currency: String,
    pub requests: Vec<QuoteCostRequest>,
    pub quotes: Vec<QuoteInput>,
    pub source_identities: Vec<(String, String)>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QuoteDiagnostic {
    pub request_id: String,
    pub request_hash: String,
    pub input_hash: String,
    pub selected_quote_id: Option<String>,
    pub selected_quote_hash: Option<String>,
    pub status: StatusResult,
    pub cost: Option<CostEvidence>,
    pub account_eligibility: StatusResult,
    pub execution_evidence: StatusResult,
}

pub fn decode_request(bytes: &[u8]) -> Result<QuoteDiagnosticRequest> {
    let r = crate::codec::decode(bytes, MAX_REQUEST_BYTES)?;
    validate_request(&r)?;
    Ok(r)
}

fn validate_request(r: &QuoteDiagnosticRequest) -> Result<()> {
    require(r.schema_version == REQUEST_SCHEMA, ErrorCode::InvalidSchema)?;
    require(
        !r.requests.is_empty()
            && r.requests.len() <= MAX_DIAGNOSTICS
            && r.quotes.len() <= MAX_QUOTES,
        ErrorCode::LimitExceeded,
    )?;
    // Typed callers receive the same scalar/depth/string checks as JSON callers.
    require(
        canonical_bytes(r)?.len() <= MAX_REQUEST_BYTES,
        ErrorCode::LimitExceeded,
    )?;
    require(text(&r.report_currency), ErrorCode::InvalidInput)?;
    identities(&r.source_identities)?;
    let specs = validate_quotes(&r.quotes, &r.report_currency)?;
    let mut ids = BTreeSet::new();
    for request in &r.requests {
        require(
            text(&request.request_id) && ids.insert(&request.request_id),
            ErrorCode::InvalidIdentity,
        )?;
        let descriptor = &request.leg.instrument;
        require(
            [
                &descriptor.venue,
                &descriptor.instrument_id,
                &descriptor.underlying,
                &descriptor.rule_version,
            ]
            .iter()
            .all(|s| text(s)),
            ErrorCode::InvalidIdentity,
        )?;
        validate_leg(&request.leg, &specs)?;
    }
    Ok(())
}

/// Revalidate public typed input before evaluation. Missing evidence affects only its
/// own leg; structurally conflicting evidence rejects the entire request.
pub fn analyze(r: &QuoteDiagnosticRequest) -> Result<Vec<QuoteDiagnostic>> {
    validate_request(r)?;
    let input_hash = canonical_hash(r)?;
    r.requests
        .iter()
        .map(|request| {
            let quote = select_quote(&r.quotes, &request.leg.instrument.spec_hash, r.cutoff_ns)?;
            let cost = quote.map(|q| quote_cost(q, &request.leg, r.cutoff_ns, &r.report_currency));
            Ok(QuoteDiagnostic {
                request_id: request.request_id.clone(),
                request_hash: canonical_hash(request)?,
                input_hash: input_hash.clone(),
                selected_quote_id: quote.map(|q| q.quote_id.clone()),
                selected_quote_hash: quote.map(canonical_hash).transpose()?,
                status: cost.as_ref().map(|c| c.status.clone()).unwrap_or_else(|| {
                    StatusResult::new(Status::NotEvaluable, "MISSING_VISIBLE_EXACT_SPEC_QUOTE")
                }),
                cost,
                account_eligibility: StatusResult::new(Status::Unverified, "NO_ACCOUNT_EVIDENCE"),
                execution_evidence: StatusResult::new(
                    Status::Unverified,
                    "DISPLAYED_COST_ONLY_NO_FILL",
                ),
            })
        })
        .collect()
}
