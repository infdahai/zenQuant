//! One repository-scoped SIMULATED cash authority. Funding and active reservations
//! share one atomic snapshot. Available cash is derived, never separately persisted.
use crate::{
    io, publication,
    snapshot_file::{self as disk, DirectoryLock, Hooks, StoreError},
};
use options_core::{
    canonical::{bytes_sha256, canonical_bytes, canonical_hash},
    codec,
    contracts::valid_sha256,
    decimal::Dec,
    intents::Mode,
    reservations::{self as core, Bounds, EvaluationFailure, Funding, Owner, Request},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    io::Read,
    path::{Path, PathBuf},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub const STORE_SCHEMA: &str = "simulated-reservation-authority.rust.v1";
pub const RECEIPT_SCHEMA: &str = "simulated-reservation-receipt.rust.v1";
pub const MAX_DOMAINS: usize = 16;
pub const MAX_RESERVATIONS: usize = 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Failure {
    Storage(StoreError),
    InvalidInput,
    Corrupt,
    FundingConflict,
    RequestConflict,
    MissingFunding,
    InsufficientFunds,
    ClockRegression,
    ClockUnavailable,
    NotEvaluable(EvaluationFailure),
    Limit,
}
impl From<StoreError> for Failure {
    fn from(value: StoreError) -> Self {
        Self::Storage(value)
    }
}
impl Failure {
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::Storage(e) => e.exit_code(),
            _ => 2,
        }
    }
    pub fn message(&self) -> String {
        match self {
            Self::Storage(e) => e.message().into(),
            Self::InvalidInput => "INVALID_RESERVATION_INPUT".into(),
            Self::Corrupt => "CORRUPT_RESERVATION_AUTHORITY".into(),
            Self::FundingConflict => "FUNDING_OWNER_CONFLICT".into(),
            Self::RequestConflict => "RESERVATION_ID_CONFLICT".into(),
            Self::MissingFunding => "MISSING_SIMULATED_FUNDING".into(),
            Self::InsufficientFunds => "INSUFFICIENT_SIMULATED_FUNDS".into(),
            Self::ClockRegression => "EVALUATION_CLOCK_REGRESSED".into(),
            Self::ClockUnavailable => "EVALUATION_CLOCK_UNAVAILABLE".into(),
            Self::NotEvaluable(e) => format!("NOT_EVALUABLE:{e:?}"),
            Self::Limit => "RESERVATION_CAPACITY_LIMIT".into(),
        }
    }
}
type Result<T> = std::result::Result<T, Failure>;
#[derive(Debug, Clone, Serialize, Deserialize)]
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
#[derive(Debug, Clone, Serialize, Deserialize)]
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
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    schema_version: String,
    mode: Mode,
    created_at_ns: u64,
    last_admission_ns: u64,
    fundings: Vec<FundedDomain>,
    reservations: Vec<Reservation>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    snapshot: Snapshot,
    sha256: String,
}
#[derive(Debug, Clone, Serialize)]
pub struct Balance {
    pub owner: Owner,
    pub funding_sha256: String,
    pub initial: Dec,
    pub available: Dec,
    pub active_reserved: Dec,
}
#[derive(Debug, Serialize)]
pub struct Receipt {
    pub schema_version: &'static str,
    pub mode: Mode,
    pub result: &'static str,
    pub observed_at_ns: u64,
    pub snapshot_sha256: String,
    pub reservation_count: usize,
    pub balances: Vec<Balance>,
    pub reservations: Vec<Reservation>,
    pub uncommitted_pending: bool,
    pub scope: &'static str,
    pub execution_authority: &'static str,
    pub account_evidence: &'static str,
    pub fee_evidence: &'static str,
}
pub fn authority_path() -> Result<PathBuf> {
    // All binaries/worktrees resolving this repository share this path; no CLI store override.
    let root = io::repository_root().map_err(|_| Failure::InvalidInput)?;
    disk::safe_path(&root.join(".simulated-reservations")).map_err(Into::into)
}
pub fn wall_clock_ns() -> Result<u64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|d| u64::try_from(d.as_nanos()).ok())
        .ok_or(Failure::ClockUnavailable)
}
fn balance(s: &Snapshot, f: &FundedDomain) -> Result<Balance> {
    let (available, active_reserved) = core::checked_remaining(
        &f.funding.initial_balance,
        s.reservations
            .iter()
            .filter(|r| r.request.owner == f.funding.owner)
            .map(|r| r.bounds.total.clone()),
    )
    .map_err(|_| Failure::Corrupt)?;
    Ok(Balance {
        owner: f.funding.owner.clone(),
        funding_sha256: f.funding_sha256.clone(),
        initial: f.funding.initial_balance.clone(),
        available,
        active_reserved,
    })
}
fn validate(s: &Snapshot) -> Result<()> {
    if s.schema_version != STORE_SCHEMA
        || s.fundings.len() > MAX_DOMAINS
        || s.reservations.len() > MAX_RESERVATIONS
    {
        return Err(Failure::Corrupt);
    }
    let mut owners = BTreeSet::new();
    let mut ids = BTreeSet::new();
    let mut latest = s.created_at_ns;
    for f in &s.fundings {
        core::funding_current(&f.funding, f.admitted_at_ns).map_err(|_| Failure::Corrupt)?;
        if f.admitted_at_ns < s.created_at_ns
            || canonical_hash(&f.funding).map_err(|_| Failure::Corrupt)? != f.funding_sha256
            || !owners.insert(canonical_hash(&f.funding.owner).map_err(|_| Failure::Corrupt)?)
        {
            return Err(Failure::Corrupt);
        }
        latest = latest.max(f.admitted_at_ns);
    }
    let mut previous = s.created_at_ns;
    for (index, r) in s.reservations.iter().enumerate() {
        let f = s
            .fundings
            .iter()
            .find(|f| f.funding.owner == r.request.owner)
            .ok_or(Failure::Corrupt)?;
        let calculated = core::evaluate(&f.funding, &r.request, r.admitted_at_ns)
            .map_err(|_| Failure::Corrupt)?;
        if r.sequence != index as u64 + 1
            || !ids.insert(&r.request.intent.request_id)
            || r.admitted_at_ns < f.admitted_at_ns
            || r.admitted_at_ns < previous
            || !valid_sha256(&r.first_request_bytes_sha256)
            || calculated != r.bounds
            || canonical_hash(&r.request).map_err(|_| Failure::Corrupt)? != r.request_sha256
        {
            return Err(Failure::Corrupt);
        }
        previous = r.admitted_at_ns;
        latest = latest.max(r.admitted_at_ns);
    }
    if latest != s.last_admission_ns {
        return Err(Failure::Corrupt);
    }
    for f in &s.fundings {
        balance(s, f)?;
    }
    Ok(())
}
fn load(dir: &DirectoryLock) -> Result<(Snapshot, bool)> {
    let mut file = disk::open_file(dir, "snapshot.json")?.ok_or(Failure::Corrupt)?;
    let mut bytes = Vec::new();
    (&mut file)
        .take(disk::MAX_STORE_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| Failure::Corrupt)?;
    let envelope: Envelope =
        codec::decode(&bytes, disk::MAX_STORE_BYTES).map_err(|_| Failure::Corrupt)?;
    validate(&envelope.snapshot)?;
    if canonical_hash(&envelope.snapshot).map_err(|_| Failure::Corrupt)? != envelope.sha256
        || canonical_bytes(&envelope).map_err(|_| Failure::Corrupt)? != bytes
    {
        return Err(Failure::Corrupt);
    }
    let pending = disk::open_file(dir, "pending.json")?.is_some();
    file.sync_all()
        .map_err(|_| StoreError::DurabilityUnconfirmed)?;
    Ok((envelope.snapshot, pending))
}
fn persist(path: &Path, dir: &DirectoryLock, s: Snapshot, h: Hooks) -> Result<Snapshot> {
    let envelope = Envelope {
        sha256: canonical_hash(&s).map_err(|_| Failure::Limit)?,
        snapshot: s,
    };
    let bytes = canonical_bytes(&envelope).map_err(|_| Failure::Limit)?;
    disk::replace(path, dir, &bytes, h)?;
    Ok(envelope.snapshot)
}
fn receipt(
    s: &Snapshot,
    result: &'static str,
    now: u64,
    records: Vec<Reservation>,
    pending: bool,
) -> Result<Receipt> {
    Ok(Receipt {
        schema_version: RECEIPT_SCHEMA,
        mode: Mode::Simulated,
        result,
        observed_at_ns: now,
        snapshot_sha256: canonical_hash(s).map_err(|_| Failure::Corrupt)?,
        reservation_count: s.reservations.len(),
        balances: s
            .fundings
            .iter()
            .map(|f| balance(s, f))
            .collect::<Result<_>>()?,
        reservations: records,
        uncommitted_pending: pending,
        scope: "SIMULATED_OPENING_PREMIUM_AND_FEE_ONLY",
        execution_authority: "NONE",
        account_evidence: "UNVERIFIED_NO_REAL_BALANCE_SYNC",
        fee_evidence: "DECLARED_SIMULATION_BOUND_NOT_VENUE_VERIFIED",
    })
}
fn check_clock(s: &Snapshot, now: u64) -> Result<()> {
    if now < s.last_admission_ns {
        Err(Failure::ClockRegression)
    } else {
        Ok(())
    }
}
pub fn initialize() -> Result<Receipt> {
    initialize_at(&authority_path()?, wall_clock_ns)
}
fn initialize_at(path: &Path, clock: impl FnOnce() -> Result<u64>) -> Result<Receipt> {
    let path = disk::safe_path(path)?;
    let dir = disk::create(&path)?;
    let now = clock()?;
    let s = Snapshot {
        schema_version: STORE_SCHEMA.into(),
        mode: Mode::Simulated,
        created_at_ns: now,
        last_admission_ns: now,
        fundings: vec![],
        reservations: vec![],
    };
    let s = persist(&path, &dir, s, Hooks::default())?;
    receipt(&s, "INITIALIZED", now, vec![], false)
}
fn fund_at(
    path: &Path,
    bytes: &[u8],
    clock: impl FnOnce() -> Result<u64>,
    h: Hooks,
) -> Result<Receipt> {
    let funding = core::decode_funding(bytes).map_err(|_| Failure::InvalidInput)?;
    let path = disk::safe_path(path)?;
    let dir = disk::lock(&path, true)?;
    let (mut s, pending) = load(&dir)?;
    let now = clock()?;
    check_clock(&s, now)?;
    if let Some(existing) = s.fundings.iter().find(|f| f.funding.owner == funding.owner) {
        if existing.funding != funding {
            return Err(Failure::FundingConflict);
        }
        disk::confirm(&path, &dir)?;
        return receipt(&s, "ALREADY_FUNDED", now, vec![], pending);
    }
    if s.fundings.len() >= MAX_DOMAINS {
        return Err(Failure::Limit);
    }
    core::funding_current(&funding, now).map_err(Failure::NotEvaluable)?;
    s.fundings.push(FundedDomain {
        funding_sha256: canonical_hash(&funding).map_err(|_| Failure::InvalidInput)?,
        funding,
        admitted_at_ns: now,
    });
    s.last_admission_ns = now;
    let s = persist(&path, &dir, s, h)?;
    receipt(&s, "FUNDED", now, vec![], false)
}
/// Clock is read after exclusive lock acquisition AND full snapshot validation.
fn reserve_at(
    path: &Path,
    bytes: &[u8],
    clock: impl FnOnce() -> Result<u64>,
    h: Hooks,
) -> Result<(Receipt, Duration)> {
    let request = core::decode_request(bytes).map_err(|_| Failure::InvalidInput)?;
    let path = disk::safe_path(path)?;
    let waiting = Instant::now();
    let dir = disk::lock(&path, true)?;
    let lock_wait = waiting.elapsed();
    let (mut s, pending) = load(&dir)?;
    let now = clock()?;
    check_clock(&s, now)?;
    if let Some(existing) = s
        .reservations
        .iter()
        .find(|r| r.request.intent.request_id == request.intent.request_id)
    {
        if existing.request != request {
            return Err(Failure::RequestConflict);
        }
        disk::confirm(&path, &dir)?;
        return Ok((
            receipt(&s, "ALREADY_RESERVED", now, vec![existing.clone()], pending)?,
            lock_wait,
        ));
    }
    if s.reservations.len() >= MAX_RESERVATIONS {
        return Err(Failure::Limit);
    }
    let funded = s
        .fundings
        .iter()
        .find(|f| f.funding.owner == request.owner)
        .ok_or(Failure::MissingFunding)?;
    let bounds = core::evaluate(&funded.funding, &request, now).map_err(Failure::NotEvaluable)?;
    if balance(&s, funded)?.available < bounds.total {
        return Err(Failure::InsufficientFunds);
    }
    let record = Reservation {
        sequence: s.reservations.len() as u64 + 1,
        state: State::Active,
        request_sha256: canonical_hash(&request).map_err(|_| Failure::InvalidInput)?,
        request,
        first_request_bytes_sha256: bytes_sha256(bytes),
        admitted_at_ns: now,
        bounds,
    };
    s.reservations.push(record.clone());
    s.last_admission_ns = now;
    let s = persist(&path, &dir, s, h)?;
    Ok((
        receipt(&s, "RESERVED", now, vec![record], false)?,
        lock_wait,
    ))
}
fn replay_at(path: &Path, clock: impl FnOnce() -> Result<u64>) -> Result<Receipt> {
    let path = disk::safe_path(path)?;
    let dir = disk::lock(&path, true)?;
    let (s, pending) = load(&dir)?;
    let now = clock()?;
    disk::confirm(&path, &dir)?;
    receipt(&s, "REPLAYED", now, s.reservations.clone(), pending)
}
fn request_bytes(request: &Path, authority: &Path) -> Result<Vec<u8>> {
    let request = io::absolute(request).map_err(|_| Failure::InvalidInput)?;
    if request.starts_with(authority) || authority.starts_with(&request) {
        return Err(Failure::InvalidInput);
    }
    publication::read_bounded(&request, core::MAX_INPUT_BYTES).map_err(|_| Failure::InvalidInput)
}
pub fn fund_file(request: &Path) -> Result<Receipt> {
    let path = authority_path()?;
    let bytes = request_bytes(request, &path)?;
    fund_at(&path, &bytes, wall_clock_ns, Hooks::default())
}
pub fn reserve_file(request: &Path) -> Result<Receipt> {
    let path = authority_path()?;
    let bytes = request_bytes(request, &path)?;
    reserve_at(&path, &bytes, wall_clock_ns, Hooks::default()).map(|r| r.0)
}
pub fn replay() -> Result<Receipt> {
    replay_at(&authority_path()?, wall_clock_ns)
}

