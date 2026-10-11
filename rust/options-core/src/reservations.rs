//! Explicit simulated cash authority and exact opening-premium/fee bounds only.
use crate::{
    canonical::{canonical_bytes, canonical_hash},
    contracts::*,
    decimal::Dec,
    error::{ErrorCode, Result},
    intents::{self, Intent, Mode},
    measurement::{legal_quantity, require, text},
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
    // This v1 state uses the same exact decimal decoder as its inputs. Refuse an
    // exact arithmetic result that would not survive replay; never round it.
    for amount in [&premium, &opening_fee, &total] {
        let restored = amount
            .canonical_string()
            .and_then(|s| Dec::parse(&s))
            .map_err(|_| E::NumericalLimitation)?;
        if restored != *amount {
            return Err(E::NumericalLimitation);
        }
    }
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

// Funding and reservations form one logical state. IO adapters cannot mutate it.
use crate::contracts::valid_sha256;
use std::collections::BTreeSet;
pub const STORE_SCHEMA: &str = "simulated-reservation-authority.rust.v1";
pub const MAX_DOMAINS: usize = 16;
pub const MAX_RESERVATIONS: usize = 1024;
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CashFailure {
    InvalidInput,
    Corrupt,
    FundingConflict,
    RequestConflict,
    MissingFunding,
    InsufficientFunds,
    ClockRegression,
    NotEvaluable(EvaluationFailure),
    Limit,
}
type CashResult<T> = std::result::Result<T, CashFailure>;
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct FundedDomain {
    funding: Funding,
    funding_sha256: String,
    admitted_at_ns: u64,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum State {
    Active,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Reservation {
    pub sequence: u64,
    pub state: State,
    pub request: Request,
    pub request_sha256: String,
    pub first_request_bytes_sha256: String,
    pub admitted_at_ns: u64,
    pub bounds: Bounds,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StateData {
    schema_version: String,
    mode: Mode,
    created_at_ns: u64,
    last_admission_ns: u64,
    fundings: Vec<FundedDomain>,
    reservations: Vec<Reservation>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Balance {
    pub owner: Owner,
    pub funding_sha256: String,
    pub initial: Dec,
    pub available: Dec,
    pub active_reserved: Dec,
}
fn balance(s: &StateData, f: &FundedDomain) -> CashResult<Balance> {
    let (available, active_reserved) = checked_remaining(
        &f.funding.initial_balance,
        s.reservations
            .iter()
            .filter(|r| r.request.owner == f.funding.owner)
            .map(|r| r.bounds.total.clone()),
    )
    .map_err(|_| CashFailure::Corrupt)?;
    Ok(Balance {
        owner: f.funding.owner.clone(),
        funding_sha256: f.funding_sha256.clone(),
        initial: f.funding.initial_balance.clone(),
        available,
        active_reserved,
    })
}
fn validate(s: &StateData) -> CashResult<()> {
    if s.schema_version != STORE_SCHEMA
        || s.fundings.len() > MAX_DOMAINS
        || s.reservations.len() > MAX_RESERVATIONS
    {
        return Err(CashFailure::Corrupt);
    }
    let mut owners = BTreeSet::new();
    let mut ids = BTreeSet::new();
    let mut latest = s.created_at_ns;
    for f in &s.fundings {
        funding_current(&f.funding, f.admitted_at_ns).map_err(|_| CashFailure::Corrupt)?;
        if f.admitted_at_ns < s.created_at_ns
            || canonical_hash(&f.funding).map_err(|_| CashFailure::Corrupt)? != f.funding_sha256
            || !owners.insert(canonical_hash(&f.funding.owner).map_err(|_| CashFailure::Corrupt)?)
        {
            return Err(CashFailure::Corrupt);
        }
        latest = latest.max(f.admitted_at_ns);
    }
    let mut previous = s.created_at_ns;
    for (index, r) in s.reservations.iter().enumerate() {
        let f = s
            .fundings
            .iter()
            .find(|f| f.funding.owner == r.request.owner)
            .ok_or(CashFailure::Corrupt)?;
        let calculated =
            evaluate(&f.funding, &r.request, r.admitted_at_ns).map_err(|_| CashFailure::Corrupt)?;
        if r.sequence != index as u64 + 1
            || !ids.insert(&r.request.intent.request_id)
            || r.admitted_at_ns < f.admitted_at_ns
            || r.admitted_at_ns < previous
            || !valid_sha256(&r.first_request_bytes_sha256)
            || calculated != r.bounds
            || canonical_hash(&r.request).map_err(|_| CashFailure::Corrupt)? != r.request_sha256
        {
            return Err(CashFailure::Corrupt);
        }
        previous = r.admitted_at_ns;
        latest = latest.max(r.admitted_at_ns);
    }
    if latest != s.last_admission_ns {
        return Err(CashFailure::Corrupt);
    }
    for f in &s.fundings {
        balance(s, f)?;
    }
    Ok(())
}

/// Validated owned state. Private data and checked deserialization preserve the
/// invariant between pure transitions; no filesystem, clock or authority-path APIs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct Authority(StateData);
impl<'de> Deserialize<'de> for Authority {
    fn deserialize<D: serde::Deserializer<'de>>(
        deserializer: D,
    ) -> std::result::Result<Self, D::Error> {
        let state = StateData::deserialize(deserializer)?;
        validate(&state).map_err(|_| serde::de::Error::custom("INVALID_CASH_AUTHORITY"))?;
        Ok(Self(state))
    }
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    Funded,
    AlreadyFunded,
    Reserved(Reservation),
    AlreadyReserved(Reservation),
}
impl Outcome {
    pub fn changed(&self) -> bool {
        matches!(self, Self::Funded | Self::Reserved(_))
    }
    pub fn label(&self) -> &'static str {
        match self {
            Self::Funded => "FUNDED",
            Self::AlreadyFunded => "ALREADY_FUNDED",
            Self::Reserved(_) => "RESERVED",
            Self::AlreadyReserved(_) => "ALREADY_RESERVED",
        }
    }
    pub fn record(&self) -> Option<&Reservation> {
        match self {
            Self::Reserved(r) | Self::AlreadyReserved(r) => Some(r),
            _ => None,
        }
    }
}
/// A proposed state, never a durability acknowledgement or execution authorization.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Transition {
    pub next: Authority,
    pub outcome: Outcome,
}
impl Authority {
    pub fn new(now: u64) -> Self {
        Self(StateData {
            schema_version: STORE_SCHEMA.into(),
            mode: Mode::Simulated,
            created_at_ns: now,
            last_admission_ns: now,
            fundings: vec![],
            reservations: vec![],
        })
    }
    pub fn records(&self) -> &[Reservation] {
        &self.0.reservations
    }
    pub fn balances(&self) -> CashResult<Vec<Balance>> {
        self.0
            .fundings
            .iter()
            .map(|f| balance(&self.0, f))
            .collect()
    }
    fn check_clock(&self, now: u64) -> CashResult<()> {
        if now < self.0.last_admission_ns {
            Err(CashFailure::ClockRegression)
        } else {
            Ok(())
        }
    }
    pub fn fund(&self, funding: &Funding, now: u64) -> CashResult<Transition> {
        validate_funding(funding).map_err(|_| CashFailure::InvalidInput)?;
        self.check_clock(now)?;
        if let Some(existing) = self
            .0
            .fundings
            .iter()
            .find(|f| f.funding.owner == funding.owner)
        {
            if existing.funding != *funding {
                return Err(CashFailure::FundingConflict);
            }
            return Ok(Transition {
                next: self.clone(),
                outcome: Outcome::AlreadyFunded,
            });
        }
        if self.0.fundings.len() >= MAX_DOMAINS {
            return Err(CashFailure::Limit);
        }
        funding_current(funding, now).map_err(CashFailure::NotEvaluable)?;
        let mut next = self.clone();
        next.0.fundings.push(FundedDomain {
            funding_sha256: canonical_hash(funding).map_err(|_| CashFailure::InvalidInput)?,
            funding: funding.clone(),
            admitted_at_ns: now,
        });
        next.0.last_admission_ns = now;
        Ok(Transition {
            next,
            outcome: Outcome::Funded,
        })
    }
    pub fn reserve(
        &self,
        request: &Request,
        first_bytes_sha256: &str,
        now: u64,
    ) -> CashResult<Transition> {
        validate_request(request).map_err(|_| CashFailure::InvalidInput)?;
        if !valid_sha256(first_bytes_sha256) {
            return Err(CashFailure::InvalidInput);
        }
        self.check_clock(now)?;
        if let Some(existing) = self
            .0
            .reservations
            .iter()
            .find(|r| r.request.intent.request_id == request.intent.request_id)
        {
            if existing.request != *request {
                return Err(CashFailure::RequestConflict);
            }
            return Ok(Transition {
                next: self.clone(),
                outcome: Outcome::AlreadyReserved(existing.clone()),
            });
        }
        if self.0.reservations.len() >= MAX_RESERVATIONS {
            return Err(CashFailure::Limit);
        }
        let funded = self
            .0
            .fundings
            .iter()
            .find(|f| f.funding.owner == request.owner)
            .ok_or(CashFailure::MissingFunding)?;
        let bounds = evaluate(&funded.funding, request, now).map_err(CashFailure::NotEvaluable)?;
        if balance(&self.0, funded)?.available < bounds.total {
            return Err(CashFailure::InsufficientFunds);
        }
        let record = Reservation {
            sequence: self.0.reservations.len() as u64 + 1,
            state: State::Active,
            request_sha256: canonical_hash(request).map_err(|_| CashFailure::InvalidInput)?,
            first_request_bytes_sha256: first_bytes_sha256.into(),
            request: request.clone(),
            admitted_at_ns: now,
            bounds,
        };
        let mut next = self.clone();
        next.0.reservations.push(record.clone());
        next.0.last_admission_ns = now;
        Ok(Transition {
            next,
            outcome: Outcome::Reserved(record),
        })
    }
}
