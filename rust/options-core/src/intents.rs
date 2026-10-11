//! Simulation requests only: structural admission never grants risk or order authority.
use crate::{
    canonical::canonical_bytes,
    contracts::*,
    decimal::Dec,
    error::{Error, ErrorCode, Result},
    measurement::{legal_quantity, require, text, tick, validate_spec},
};
use serde::{Deserialize, Serialize};

pub const REQUEST_SCHEMA: &str = "simulated-trade-intent.rust.v1";
pub const MAX_INTENT_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Mode {
    Simulated,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DecisionKind {
    Manual,
    Algorithm,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionSource {
    pub kind: DecisionKind,
    pub source_id: String,
    pub source_version: String,
    /// Claimed source bytes; never fetched or independently attested by this module.
    pub evidence_sha256: String,
    pub evidence_available_ns: u64,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Intent {
    pub schema_version: String,
    pub mode: Mode,
    pub request_id: String,
    pub simulated_account_id: String,
    pub source: DecisionSource,
    pub evidence_cutoff_ns: u64,
    pub created_ns: u64,
    /// Exclusive declared deadline; admission does not consult the current clock.
    pub expires_ns: u64,
    pub metadata_known_ns: u64,
    pub spec: InstrumentSpec,
    pub leg: CandidateLeg,
    /// Positive limit in spec.price_unit and spec.quote_currency; not a fill price.
    pub limit_price_native: Dec,
}

pub fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b))
}

pub fn decode_request(bytes: &[u8]) -> Result<Intent> {
    let intent = crate::codec::decode(bytes, MAX_INTENT_BYTES)?;
    validate(&intent)?;
    Ok(intent)
}

pub fn validate(r: &Intent) -> Result<()> {
    require(r.schema_version == REQUEST_SCHEMA, ErrorCode::InvalidSchema)?;
    require(
        canonical_bytes(r)?.len() <= MAX_INTENT_BYTES,
        ErrorCode::LimitExceeded,
    )?;
    require(
        valid_id(&r.request_id)
            && valid_id(&r.simulated_account_id)
            && text(&r.source.source_id)
            && text(&r.source.source_version)
            && valid_sha256(&r.source.evidence_sha256),
        ErrorCode::InvalidIdentity,
    )?;
    validate_spec(&r.spec)?;
    require(
        r.source.evidence_available_ns <= r.evidence_cutoff_ns
            && r.metadata_known_ns <= r.evidence_cutoff_ns
            && r.spec.valid_from_ns <= r.evidence_cutoff_ns
            && r.evidence_cutoff_ns <= r.created_ns
            && r.created_ns < r.expires_ns
            && r.expires_ns <= r.spec.expiry_ns
            && r.spec.valid_until_ns.is_none_or(|end| r.expires_ns <= end),
        ErrorCode::InvalidTimestamp,
    )?;
    require(
        r.leg.instrument == InstrumentDescriptor::from_spec(&r.spec)?,
        ErrorCode::InvalidIdentity,
    )?;
    // This first recording contract deliberately supports just one limit BUY option leg.
    require(
        r.leg.side == Side::Buy
            && r.spec.product == Product::LinearOption
            && r.spec.exercise_style == ExerciseStyle::European
            && matches!(
                r.spec.settlement_rule,
                SettlementRule::LinearCash | SettlementRule::LinearViaFuture
            )
            && (r.spec.quantity_unit == QuantityUnit::Contracts
                || r.spec.price_unit == PriceUnit::QuotePerBase),
        ErrorCode::Unsupported,
    )?;
    require(r.limit_price_native > Dec::zero(), ErrorCode::InvalidInput)?;
    legal_quantity(&r.spec, &r.leg.quantity_native)
        .map_err(|_| Error::new(ErrorCode::InvalidInput))?;
    tick(&r.spec, &r.limit_price_native).map_err(|_| Error::new(ErrorCode::InvalidInput))?;
    Ok(())
}
