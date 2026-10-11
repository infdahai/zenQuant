//! One repository-scoped SIMULATED cash authority. Funding and active reservations
//! share one atomic snapshot. Available cash is derived, never separately persisted.
use crate::{
    io, publication,
    snapshot_file::{self as disk, DirectoryLock, Hooks, StoreError},
};
use options_core::{
    canonical::{bytes_sha256, canonical_bytes, canonical_hash},
    codec,
    intents::Mode,
    reservations::{self as core, Authority, CashFailure, EvaluationFailure, Transition},
};
use serde::{Deserialize, Serialize};
use std::{
    io::Read,
    path::{Path, PathBuf},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub use core::{Balance, MAX_DOMAINS, MAX_RESERVATIONS, Reservation, STORE_SCHEMA, State};
pub const RECEIPT_SCHEMA: &str = "simulated-reservation-receipt.rust.v1";

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
impl From<CashFailure> for Failure {
    fn from(value: CashFailure) -> Self {
        match value {
            CashFailure::InvalidInput => Self::InvalidInput,
            CashFailure::Corrupt => Self::Corrupt,
            CashFailure::FundingConflict => Self::FundingConflict,
            CashFailure::RequestConflict => Self::RequestConflict,
            CashFailure::MissingFunding => Self::MissingFunding,
            CashFailure::InsufficientFunds => Self::InsufficientFunds,
            CashFailure::ClockRegression => Self::ClockRegression,
            CashFailure::NotEvaluable(e) => Self::NotEvaluable(e),
            CashFailure::Limit => Self::Limit,
        }
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
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    snapshot: Authority,
    sha256: String,
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
fn load(dir: &DirectoryLock) -> Result<(Authority, bool)> {
    let mut file = disk::open_file(dir, "snapshot.json")?.ok_or(Failure::Corrupt)?;
    let mut bytes = Vec::new();
    (&mut file)
        .take(disk::MAX_STORE_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| Failure::Corrupt)?;
    let envelope: Envelope =
        codec::decode(&bytes, disk::MAX_STORE_BYTES).map_err(|_| Failure::Corrupt)?;
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
fn persist(path: &Path, dir: &DirectoryLock, s: Authority, h: Hooks) -> Result<Authority> {
    let envelope = Envelope {
        sha256: canonical_hash(&s).map_err(|_| Failure::Limit)?,
        snapshot: s,
    };
    let bytes = canonical_bytes(&envelope).map_err(|_| Failure::Limit)?;
    disk::replace(path, dir, &bytes, h)?;
    Ok(envelope.snapshot)
}
fn receipt(
    s: &Authority,
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
        reservation_count: s.records().len(),
        balances: s.balances()?,
        reservations: records,
        uncommitted_pending: pending,
        scope: "SIMULATED_OPENING_PREMIUM_AND_FEE_ONLY",
        execution_authority: "NONE",
        account_evidence: "UNVERIFIED_NO_REAL_BALANCE_SYNC",
        fee_evidence: "DECLARED_SIMULATION_BOUND_NOT_VENUE_VERIFIED",
    })
}
pub fn initialize() -> Result<Receipt> {
    initialize_at(&authority_path()?, wall_clock_ns)
}
fn initialize_at(path: &Path, clock: impl FnOnce() -> Result<u64>) -> Result<Receipt> {
    let path = disk::safe_path(path)?;
    let dir = disk::create(&path)?;
    let now = clock()?;
    let s = Authority::new(now);
    let s = persist(&path, &dir, s, Hooks::default())?;
    receipt(&s, "INITIALIZED", now, vec![], false)
}
fn commit_transition(
    path: &Path,
    dir: &DirectoryLock,
    transition: Transition,
    now: u64,
    pending: bool,
    h: Hooks,
) -> Result<Receipt> {
    let result = transition.outcome.label();
    let records = transition.outcome.record().cloned().into_iter().collect();
    let changed = transition.outcome.changed();
    let s = if changed {
        persist(path, dir, transition.next, h)?
    } else {
        disk::confirm(path, dir)?;
        transition.next
    };
    receipt(&s, result, now, records, !changed && pending)
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
    let (s, pending) = load(&dir)?;
    let now = clock()?;
    commit_transition(&path, &dir, s.fund(&funding, now)?, now, pending, h)
}
/// Clock is read after exclusive lock acquisition AND checked state deserialization.
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
    let (s, pending) = load(&dir)?;
    let now = clock()?;
    let transition = s.reserve(&request, &bytes_sha256(bytes), now)?;
    Ok((
        commit_transition(&path, &dir, transition, now, pending, h)?,
        lock_wait,
    ))
}
fn replay_at(path: &Path, clock: impl FnOnce() -> Result<u64>) -> Result<Receipt> {
    let path = disk::safe_path(path)?;
    let dir = disk::lock(&path, true)?;
    let (s, pending) = load(&dir)?;
    let now = clock()?;
    disk::confirm(&path, &dir)?;
    receipt(&s, "REPLAYED", now, s.records().to_vec(), pending)
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

#[cfg(all(test, target_os = "linux"))]
#[path = "reservation_store_tests.rs"]
mod tests;

#[cfg(feature = "reservation-bench")]
#[path = "reservation_benchmark.rs"]
pub mod benchmark;
