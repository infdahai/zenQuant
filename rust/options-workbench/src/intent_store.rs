//! Bounded simulation snapshots on a local Linux filesystem, with a cooperating-process
//! exclusive directory flock. No orders, balances, risk decision, WAL or automatic repair.
use crate::snapshot_file::{DirectoryLock, Hooks, confirm, open_file, safe_path};
use crate::{io, publication};
use options_core::{
    canonical::{bytes_sha256, canonical_bytes, canonical_hash},
    codec,
    contracts::valid_sha256,
    intents::{self, Intent, Mode},
};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, fs::File, io::Read, path::Path};

pub const STORE_SCHEMA: &str = "simulated-intent-store.rust.v1";
pub const REPORT_SCHEMA: &str = "simulated-intent-receipt.rust.v1";
pub const MAX_RECORDS: usize = 1024;
pub const MAX_STORE_BYTES: usize = 16 * 1024 * 1024;
const SNAPSHOT: &str = "snapshot.json";
const PENDING: &str = "pending.json";

pub use crate::snapshot_file::StoreError;
type Result<T> = std::result::Result<T, StoreError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RecordState {
    RecordedUnassessed,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub sequence: u64,
    pub state: RecordState,
    pub intent_sha256: String,
    pub first_request_bytes_sha256: String,
    pub intent: Intent,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    schema_version: String,
    mode: Mode,
    store_id: String,
    records: Vec<Record>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    snapshot: Snapshot,
    sha256: String,
}
#[derive(Debug, Serialize)]
pub struct Receipt {
    pub schema_version: &'static str,
    pub mode: Mode,
    pub store_id: String,
    pub result: &'static str,
    pub snapshot_sha256: String,
    pub record_count: usize,
    /// Record returns one immutable record; replay returns every record in sequence order.
    pub records: Vec<Record>,
    pub uncommitted_pending: bool,
    pub risk_assessment: &'static str,
    pub account_evidence: &'static str,
    pub execution_authority: &'static str,
    pub execution_evidence: &'static str,
    pub source_integrity: &'static str,
    pub funds_reserved: bool,
}
fn receipt(
    s: &Snapshot,
    result: &'static str,
    records: Vec<Record>,
    pending: bool,
) -> Result<Receipt> {
    Ok(Receipt {
        schema_version: REPORT_SCHEMA,
        mode: Mode::Simulated,
        store_id: s.store_id.clone(),
        result,
        snapshot_sha256: canonical_hash(s).map_err(|_| StoreError::CorruptStore)?,
        record_count: s.records.len(),
        records,
        uncommitted_pending: pending,
        risk_assessment: "NOT_ASSESSED",
        account_evidence: "UNVERIFIED",
        execution_authority: "NONE",
        execution_evidence: "UNVERIFIED",
        source_integrity: "NOT_INDEPENDENTLY_VERIFIED",
        funds_reserved: false,
    })
}
fn lock(path: &Path) -> Result<DirectoryLock> {
    crate::snapshot_file::lock(path, false)
}
fn validate_snapshot(s: &Snapshot) -> Result<()> {
    if s.schema_version != STORE_SCHEMA
        || !intents::valid_id(&s.store_id)
        || s.records.len() > MAX_RECORDS
    {
        return Err(StoreError::CorruptStore);
    }
    let mut ids = BTreeSet::new();
    for (index, record) in s.records.iter().enumerate() {
        intents::validate(&record.intent).map_err(|_| StoreError::CorruptStore)?;
        if record.sequence != index as u64 + 1
            || !ids.insert(&record.intent.request_id)
            || !valid_sha256(&record.first_request_bytes_sha256)
            || canonical_hash(&record.intent).map_err(|_| StoreError::CorruptStore)?
                != record.intent_sha256
        {
            return Err(StoreError::CorruptStore);
        }
    }
    Ok(())
}
fn load(dir: &File) -> Result<(Snapshot, bool)> {
    let mut file = open_file(dir, SNAPSHOT)?.ok_or(StoreError::CorruptStore)?;
    let mut bytes = Vec::new();
    (&mut file)
        .take(MAX_STORE_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| StoreError::CorruptStore)?;
    let e: Envelope =
        codec::decode(&bytes, MAX_STORE_BYTES).map_err(|_| StoreError::CorruptStore)?;
    validate_snapshot(&e.snapshot)?;
    if canonical_hash(&e.snapshot).map_err(|_| StoreError::CorruptStore)? != e.sha256
        || canonical_bytes(&e).map_err(|_| StoreError::CorruptStore)? != bytes
    {
        return Err(StoreError::CorruptStore);
    }
    let pending = open_file(dir, PENDING)?.is_some();
    // A retry after an uncertain acknowledgement must confirm the committed bytes too.
    file.sync_all()
        .map_err(|_| StoreError::DurabilityUnconfirmed)?;
    Ok((e.snapshot, pending))
}
fn persist(path: &Path, dir: &File, s: Snapshot, h: Hooks) -> Result<Snapshot> {
    let e = Envelope {
        sha256: canonical_hash(&s).map_err(|_| StoreError::Limit)?,
        snapshot: s,
    };
    let bytes = canonical_bytes(&e).map_err(|_| StoreError::Limit)?;
    crate::snapshot_file::replace(path, dir, &bytes, h)?;
    Ok(e.snapshot)
}

/// Explicit initialization only; an existing or crash-interrupted directory is never reset.
pub fn initialize(path: &Path, store_id: &str) -> Result<Receipt> {
    initialize_with(path, store_id, Hooks::default())
}
fn initialize_with(path: &Path, store_id: &str, h: Hooks) -> Result<Receipt> {
    if !intents::valid_id(store_id) {
        return Err(StoreError::InvalidInput);
    }
    let path = safe_path(path)?;
    let dir = crate::snapshot_file::create(&path)?;
    let s = Snapshot {
        schema_version: STORE_SCHEMA.into(),
        mode: Mode::Simulated,
        store_id: store_id.into(),
        records: vec![],
    };
    let s = persist(&path, &dir, s, h)?;
    receipt(&s, "INITIALIZED", vec![], false)
}
/// Byte API validates before opening the store. Request IDs are unique across the store,
/// including accounts and source kinds. Retries preserve the first raw-byte identity.
pub fn record(path: &Path, bytes: &[u8]) -> Result<Receipt> {
    record_with(path, bytes, Hooks::default())
}
fn record_with(path: &Path, bytes: &[u8], h: Hooks) -> Result<Receipt> {
    let intent = intents::decode_request(bytes).map_err(|_| StoreError::InvalidInput)?;
    let hash = canonical_hash(&intent).map_err(|_| StoreError::InvalidInput)?;
    let path = safe_path(path)?;
    let dir = lock(&path)?;
    let (mut s, pending) = load(&dir)?;
    if let Some(existing) = s
        .records
        .iter()
        .find(|r| r.intent.request_id == intent.request_id)
    {
        if existing.intent != intent {
            return Err(StoreError::Conflict);
        }
        confirm(&path, &dir)?;
        return receipt(&s, "ALREADY_RECORDED", vec![existing.clone()], pending);
    }
    if s.records.len() >= MAX_RECORDS {
        return Err(StoreError::Limit);
    }
    let record = Record {
        sequence: s.records.len() as u64 + 1,
        state: RecordState::RecordedUnassessed,
        intent_sha256: hash,
        first_request_bytes_sha256: bytes_sha256(bytes),
        intent,
    };
    s.records.push(record.clone());
    let s = persist(&path, &dir, s, h)?;
    receipt(&s, "RECORDED", vec![record], false)
}
pub fn replay(path: &Path) -> Result<Receipt> {
    let path = safe_path(path)?;
    let dir = lock(&path)?;
    let (s, pending) = load(&dir)?;
    confirm(&path, &dir)?;
    receipt(&s, "REPLAYED", s.records.clone(), pending)
}
pub fn record_file(path: &Path, request: &Path) -> Result<Receipt> {
    let store = safe_path(path)?;
    let request = io::absolute(request).map_err(|_| StoreError::InvalidInput)?;
    if request.starts_with(&store) || store.starts_with(&request) {
        return Err(StoreError::InvalidInput);
    }
    let bytes = publication::read_bounded(&request, intents::MAX_INTENT_BYTES)
        .map_err(|_| StoreError::InvalidInput)?;
    record(&store, &bytes)
}

#[cfg(all(test, target_os = "linux"))]
#[path = "intent_store_tests.rs"]
mod tests;
