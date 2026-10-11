//! Bounded simulation snapshots on a local Linux filesystem, with a cooperating-process
//! exclusive directory flock. No orders, balances, risk decision, WAL or automatic repair.
use crate::{io, publication};
use options_core::{
    canonical::{bytes_sha256, canonical_bytes, canonical_hash},
    codec,
    contracts::valid_sha256,
    intents::{self, Intent, Mode},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    ffi::CString,
    fs::File,
    io::{Read, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::{ffi::OsStrExt, fs::MetadataExt},
    },
    path::{Path, PathBuf},
};

pub const STORE_SCHEMA: &str = "simulated-intent-store.rust.v1";
pub const REPORT_SCHEMA: &str = "simulated-intent-receipt.rust.v1";
pub const MAX_RECORDS: usize = 1024;
pub const MAX_STORE_BYTES: usize = 16 * 1024 * 1024;
const SNAPSHOT: &str = "snapshot.json";
const PENDING: &str = "pending.json";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StoreError {
    InvalidInput,
    InvalidStore,
    CorruptStore,
    Conflict,
    Busy,
    Limit,
    WriteFailed,
    DurabilityUnconfirmed,
    UnsupportedPlatform,
}
impl StoreError {
    pub fn message(self) -> &'static str {
        match self {
            Self::InvalidInput => "INVALID_INTENT_INPUT",
            Self::InvalidStore => "INVALID_STORE_PATH",
            Self::CorruptStore => "CORRUPT_OR_MISSING_STORE",
            Self::Conflict => "INTENT_ID_CONFLICT",
            Self::Busy => "STORE_BUSY",
            Self::Limit => "STORE_LIMIT_REACHED",
            Self::WriteFailed => "STORE_WRITE_FAILED",
            Self::DurabilityUnconfirmed => "STORE_DURABILITY_UNCONFIRMED",
            Self::UnsupportedPlatform => "STORE_PLATFORM_UNSUPPORTED",
        }
    }
    pub fn exit_code(self) -> i32 {
        match self {
            Self::Busy => 3,
            Self::WriteFailed | Self::DurabilityUnconfirmed => 1,
            _ => 2,
        }
    }
}
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
fn safe_path(path: &Path) -> Result<PathBuf> {
    if !cfg!(target_os = "linux") {
        return Err(StoreError::UnsupportedPlatform);
    }
    let path = io::absolute(path).map_err(|_| StoreError::InvalidStore)?;
    io::reject_symlinks(&path).map_err(|_| StoreError::InvalidStore)?;
    for protected in io::trusted_roots().map_err(|_| StoreError::InvalidStore)? {
        if path.starts_with(&protected) || protected.starts_with(&path) {
            return Err(StoreError::InvalidStore);
        }
    }
    Ok(path)
}
struct DirectoryLock(File);
impl std::ops::Deref for DirectoryLock {
    type Target = File;
    fn deref(&self) -> &File {
        &self.0
    }
}
impl Drop for DirectoryLock {
    fn drop(&mut self) {
        // A concurrent fork can retain the open file description until exec even
        // with CLOEXEC. End this operation's lock explicitly before closing it.
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}
fn lock(path: &Path) -> Result<DirectoryLock> {
    let dir = publication::open_dir(path, false).map_err(|_| StoreError::InvalidStore)?;
    lock_directory(path, dir)
}
fn lock_directory(path: &Path, dir: File) -> Result<DirectoryLock> {
    if unsafe { libc::flock(dir.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err(
            if std::io::Error::last_os_error().kind() == std::io::ErrorKind::WouldBlock {
                StoreError::Busy
            } else {
                StoreError::InvalidStore
            },
        );
    }
    publication::same_parent(path, &dir).map_err(|_| StoreError::InvalidStore)?;
    Ok(DirectoryLock(dir))
}
fn name(value: &str) -> CString {
    CString::new(value).expect("fixed internal filename")
}
fn open_file(dir: &File, filename: &str) -> Result<Option<File>> {
    let fd = unsafe {
        libc::openat(
            dir.as_raw_fd(),
            name(filename).as_ptr(),
            libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC,
        )
    };
    if fd < 0 {
        return if std::io::Error::last_os_error().kind() == std::io::ErrorKind::NotFound {
            Ok(None)
        } else {
            Err(StoreError::CorruptStore)
        };
    }
    let file = unsafe { File::from_raw_fd(fd) };
    let meta = file.metadata().map_err(|_| StoreError::CorruptStore)?;
    if !meta.is_file() || meta.nlink() != 1 {
        return Err(StoreError::CorruptStore);
    }
    Ok(Some(file))
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
fn confirm(path: &Path, dir: &File) -> Result<()> {
    publication::same_parent(path, dir).map_err(|_| StoreError::DurabilityUnconfirmed)?;
    dir.sync_all()
        .map_err(|_| StoreError::DurabilityUnconfirmed)?;
    publication::same_parent(path, dir).map_err(|_| StoreError::DurabilityUnconfirmed)
}

#[derive(Clone, Copy, Default)]
struct Hooks {
    fail_at: Option<&'static str>,
    observe: Option<fn(&str)>,
}
fn checkpoint(h: Hooks, point: &str) -> Result<()> {
    if let Some(f) = h.observe {
        f(point);
    }
    if h.fail_at == Some(point) {
        Err(StoreError::WriteFailed)
    } else {
        Ok(())
    }
}
fn persist(path: &Path, dir: &File, s: Snapshot, h: Hooks) -> Result<Snapshot> {
    let e = Envelope {
        sha256: canonical_hash(&s).map_err(|_| StoreError::Limit)?,
        snapshot: s,
    };
    let bytes = canonical_bytes(&e).map_err(|_| StoreError::Limit)?;
    if bytes.len() > MAX_STORE_BYTES {
        return Err(StoreError::Limit);
    }
    publication::same_parent(path, dir).map_err(|_| StoreError::InvalidStore)?;
    // A leftover pending file is never authoritative, even if complete. Discard only
    // after validating the existing snapshot (or during exclusive first initialization).
    if open_file(dir, PENDING)?.is_some()
        && unsafe { libc::unlinkat(dir.as_raw_fd(), name(PENDING).as_ptr(), 0) } != 0
    {
        return Err(StoreError::WriteFailed);
    }
    checkpoint(h, "before_create")?;
    let fd = unsafe {
        libc::openat(
            dir.as_raw_fd(),
            name(PENDING).as_ptr(),
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            0o600,
        )
    };
    if fd < 0 {
        return Err(StoreError::WriteFailed);
    }
    let mut file = unsafe { File::from_raw_fd(fd) };
    checkpoint(h, "temp_created")?;
    file.write_all(&bytes)
        .map_err(|_| StoreError::WriteFailed)?;
    checkpoint(h, "before_file_sync")?;
    file.sync_all().map_err(|_| StoreError::WriteFailed)?;
    checkpoint(h, "file_synced")?;
    publication::same_parent(path, dir).map_err(|_| StoreError::WriteFailed)?;
    if unsafe {
        libc::renameat(
            dir.as_raw_fd(),
            name(PENDING).as_ptr(),
            dir.as_raw_fd(),
            name(SNAPSHOT).as_ptr(),
        )
    } != 0
    {
        return Err(StoreError::WriteFailed);
    }
    checkpoint(h, "renamed").map_err(|_| StoreError::DurabilityUnconfirmed)?;
    confirm(path, dir)?;
    checkpoint(h, "directory_synced").map_err(|_| StoreError::DurabilityUnconfirmed)?;
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
    let parent_path = path.parent().ok_or(StoreError::InvalidStore)?;
    let parent = publication::open_dir(parent_path, false).map_err(|_| StoreError::InvalidStore)?;
    let name = CString::new(path.file_name().ok_or(StoreError::InvalidStore)?.as_bytes())
        .map_err(|_| StoreError::InvalidStore)?;
    if unsafe { libc::mkdirat(parent.as_raw_fd(), name.as_ptr(), 0o700) } != 0 {
        return Err(StoreError::InvalidStore);
    }
    parent
        .sync_all()
        .map_err(|_| StoreError::DurabilityUnconfirmed)?;
    let created =
        publication::directory(&parent, path.file_name().ok_or(StoreError::InvalidStore)?)
            .map_err(|_| StoreError::InvalidStore)?;
    publication::same_parent(parent_path, &parent).map_err(|_| StoreError::InvalidStore)?;
    let dir = lock_directory(&path, created)?;
    if open_file(&dir, SNAPSHOT)?.is_some() || open_file(&dir, PENDING)?.is_some() {
        return Err(StoreError::InvalidStore);
    }
    let s = Snapshot {
        schema_version: STORE_SCHEMA.into(),
        mode: Mode::Simulated,
        store_id: store_id.into(),
        records: vec![],
    };
    let s = persist(&path, &dir, s, h)?;
    publication::same_parent(parent_path, &parent)
        .map_err(|_| StoreError::DurabilityUnconfirmed)?;
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
