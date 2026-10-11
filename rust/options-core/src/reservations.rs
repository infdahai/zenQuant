//! Explicit simulated cash authority and exact opening-premium/fee bounds only.
use crate::{
    canonical::{canonical_bytes, canonical_hash},
    contracts::*,
    costs::legal_quantity,
    decimal::Dec,
    error::{ErrorCode, Result},
    inputs::{require, text},
    intents::{self, Intent, Mode},
};
use serde::{Deserialize, Serialize};

pub const FUNDING_SCHEMA: &str = "simulated-funding-snapshot.rust.v1";
pub const REQUEST_SCHEMA: &str = "simulated-reservation-request.rust.v1";
pub const FEE_SCHEMA: &str = "simulated-premium-fee-cap.rust.v1";
pub const MAX_INPUT_BYTES: usize = 128 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Subaccount {
    Main,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum CollateralDomain {
    FullyPaidSingleCurrencyCash,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Owner {
    pub venue: String,
    pub simulated_account_id: String,
    pub subaccount: Subaccount,
    pub collateral_domain: CollateralDomain,
    pub currency: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FeeCap {
    pub schema_version: String,
    /// Explicit simulation evidence that total opening fees <= premium * fraction.
    /// Includes all opening fees; never inferred from a quote, zero, or an index forecast.
    pub premium_fraction: Dec,
    pub known_ns: u64,
    pub valid_from_ns: u64,
    pub valid_until_ns: u64,
    pub source_ref: String,
    pub source_sha256: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Funding {
    pub schema_version: String,
    pub mode: Mode,
    pub snapshot_id: String,
    pub owner: Owner,
    pub initial_balance: Dec,
    pub available_ns: u64,
    pub source_ref: String,
    pub source_sha256: String,
    pub fee_cap: FeeCap,
    pub max_spec_age_ns: u64,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub schema_version: String,
    pub mode: Mode,
    pub owner: Owner,
    pub funding_sha256: String,
    pub intent_sha256: String,
    pub intent: Intent,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bounds {
    pub currency: String,
    pub premium: Dec,
    pub opening_fee: Dec,
    pub total: Dec,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EvaluationFailure {
    InvalidInput,
    IdentityMismatch,
    UnsupportedCashDomain,
    FundingNotAvailable,
    FeeBoundNotCurrent,
    IntentNotCurrent,
    StaleSpec,
    UnknownSpecValidity,
    NumericalLimitation,
}
pub fn validate_owner(owner: &Owner) -> Result<()> {
    require(
        intents::valid_id(&owner.venue)
            && owner.venue == owner.venue.to_ascii_uppercase()
            && intents::valid_id(&owner.simulated_account_id)
            && intents::valid_id(&owner.currency)
            && owner.currency == owner.currency.to_ascii_uppercase(),
        ErrorCode::InvalidIdentity,
    )
}
pub fn validate_funding(f: &Funding) -> Result<()> {
    require(
        f.schema_version == FUNDING_SCHEMA && f.fee_cap.schema_version == FEE_SCHEMA,
        ErrorCode::InvalidSchema,
    )?;
    validate_owner(&f.owner)?;
    require(
        canonical_bytes(f)?.len() <= MAX_INPUT_BYTES,
        ErrorCode::LimitExceeded,
    )?;
    require(
        intents::valid_id(&f.snapshot_id)
            && !f.initial_balance.is_negative()
            && f.fee_cap.premium_fraction > Dec::zero()
            && f.max_spec_age_ns > 0
            && text(&f.source_ref)
            && valid_sha256(&f.source_sha256)
            && f.source_sha256 != "0".repeat(64)
            && text(&f.fee_cap.source_ref)
            && valid_sha256(&f.fee_cap.source_sha256)
            && f.fee_cap.source_sha256 != "0".repeat(64),
        ErrorCode::MissingEvidence,
    )?;
    require(
        f.fee_cap.valid_from_ns < f.fee_cap.valid_until_ns
            && f.fee_cap.known_ns < f.fee_cap.valid_until_ns,
        ErrorCode::InvalidTimestamp,
    )
}
pub fn validate_request(r: &Request) -> Result<()> {
    require(r.schema_version == REQUEST_SCHEMA, ErrorCode::InvalidSchema)?;
    require(
        canonical_bytes(r)?.len() <= MAX_INPUT_BYTES,
        ErrorCode::LimitExceeded,
    )?;
    validate_owner(&r.owner)?;
    intents::validate(&r.intent)?;
    require(
        valid_sha256(&r.funding_sha256)
            && canonical_hash(&r.intent)? == r.intent_sha256
            && r.owner.venue == r.intent.spec.venue
            && r.owner.simulated_account_id == r.intent.simulated_account_id,
        ErrorCode::InvalidIdentity,
    )
}
pub fn decode_funding(bytes: &[u8]) -> Result<Funding> {
    let f = crate::codec::decode(bytes, MAX_INPUT_BYTES)?;
    validate_funding(&f)?;
    Ok(f)
}
pub fn decode_request(bytes: &[u8]) -> Result<Request> {
    let r = crate::codec::decode(bytes, MAX_INPUT_BYTES)?;
    validate_request(&r)?;
    Ok(r)
}
pub fn funding_current(f: &Funding, now: u64) -> std::result::Result<(), EvaluationFailure> {
    validate_funding(f).map_err(|_| EvaluationFailure::InvalidInput)?;
    if f.available_ns > now {
        return Err(EvaluationFailure::FundingNotAvailable);
    }
    if f.fee_cap.known_ns > now || f.fee_cap.valid_from_ns > now || now >= f.fee_cap.valid_until_ns
    {
        return Err(EvaluationFailure::FeeBoundNotCurrent);
    }
    Ok(())
}
pub fn evaluate(
    f: &Funding,
    r: &Request,
    now: u64,
) -> std::result::Result<Bounds, EvaluationFailure> {
    use EvaluationFailure as E;
    validate_request(r).map_err(|_| E::InvalidInput)?;
    funding_current(f, now)?;
    if r.owner != f.owner || canonical_hash(f).map_err(|_| E::InvalidInput)? != r.funding_sha256 {
        return Err(E::IdentityMismatch);
    }
    let i = &r.intent;
    let s = &i.spec;
    if i.expires_ns > f.fee_cap.valid_until_ns {
        return Err(E::FeeBoundNotCurrent);
    }
    if s.settlement_rule != SettlementRule::LinearCash
        || [
            &s.quote_currency,
            &s.strike_currency,
            &s.settlement_currency,
            &s.margin_currency,
        ]
        .iter()
        .any(|c| **c != f.owner.currency)
    {
        return Err(E::UnsupportedCashDomain);
    }
    if now < i.created_ns || now >= i.expires_ns || now >= s.expiry_ns {
        return Err(E::IntentNotCurrent);
    }
    let until = s.valid_until_ns.ok_or(E::UnknownSpecValidity)?;
    if now < s.valid_from_ns
        || now >= until
        || now < i.metadata_known_ns
        || now - i.metadata_known_ns > f.max_spec_age_ns
    {
        return Err(E::StaleSpec);
    }
    let base = legal_quantity(s, &i.leg.quantity_native).map_err(|_| E::InvalidInput)?;
    let priced_quantity = match s.price_unit {
        PriceUnit::QuotePerBase => base,
        PriceUnit::QuotePerContract => i.leg.quantity_native.clone(),
    };
    let premium = i
        .limit_price_native
        .checked_mul(&priced_quantity)
        .map_err(|_| E::NumericalLimitation)?;
    let opening_fee = premium
        .checked_mul(&f.fee_cap.premium_fraction)
        .map_err(|_| E::NumericalLimitation)?;
    let total = premium
        .checked_add(&opening_fee)
        .map_err(|_| E::NumericalLimitation)?;
    Ok(Bounds {
        currency: f.owner.currency.clone(),
        premium,
        opening_fee,
        total,
    })
}
pub fn checked_remaining(
    initial: &Dec,
    reservations: impl IntoIterator<Item = Dec>,
) -> Result<(Dec, Dec)> {
    let mut held = Dec::zero();
    for amount in reservations {
        require(amount > Dec::zero(), ErrorCode::InvalidInput)?;
        held = held.checked_add(&amount)?;
    }
    require(
        !initial.is_negative() && held <= *initial,
        ErrorCode::InvalidInput,
    )?;
    let available = initial.checked_sub(&held)?;
    require(
        available.checked_add(&held)? == *initial,
        ErrorCode::Internal,
    )?;
    Ok((available, held))
}
