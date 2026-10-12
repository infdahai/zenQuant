//! Fixed-location allocation/record journal; deliberately never calls cash Authority.
use crate::{
    io,
    reservation_store::wall_clock_ns,
    snapshot_file::{self as disk, Hooks},
};
use options_core::{
    canonical::{canonical_bytes, canonical_hash},
    codec,
    paper::{self, Confirmation, Journal},
};
use serde::{Deserialize, Serialize};
use std::{
    io::Read,
    path::{Path, PathBuf},
};

type Result<T> = std::result::Result<T, String>;
#[derive(Clone)]
pub struct Store {
    path: PathBuf,
    engineering: bool,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    journal: Journal,
    sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Empty {}
#[derive(Serialize)]
pub struct Response {
    pub schema_version: &'static str,
    pub storage_scope: &'static str,
    pub view: paper::View,
    pub preview: Option<paper::Preview>,
    pub blockers: Vec<String>,
    pub replayed: bool,
    pub uncommitted_pending: bool,
}
fn err(e: impl std::fmt::Debug) -> String {
    format!("{e:?}")
}
fn load(dir: &disk::DirectoryLock) -> Result<(Journal, bool)> {
    let mut file = disk::open_file(dir, "snapshot.json")
        .map_err(err)?
        .ok_or("CORRUPT_OR_MISSING_PAPER_JOURNAL")?;
    let mut bytes = Vec::new();
    (&mut file)
        .take(disk::MAX_STORE_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(err)?;
    let e: Envelope = codec::decode(&bytes, disk::MAX_STORE_BYTES).map_err(err)?;
    if canonical_hash(&e.journal).map_err(err)? != e.sha256
        || canonical_bytes(&e).map_err(err)? != bytes
    {
        return Err("CORRUPT_PAPER_JOURNAL".into());
    }
    e.journal.validate().map_err(err)?;
    file.sync_all()
        .map_err(|_| "PAPER_DURABILITY_UNCONFIRMED")?;
    Ok((
        e.journal,
        disk::open_file(dir, "pending.json").map_err(err)?.is_some(),
    ))
}
fn persist(path: &Path, dir: &disk::DirectoryLock, j: &Journal, hooks: Hooks) -> Result<()> {
    let e = Envelope {
        journal: j.clone(),
        sha256: canonical_hash(j).map_err(err)?,
    };
    disk::replace(path, dir, &canonical_bytes(&e).map_err(err)?, hooks).map_err(err)
}
impl Store {
    pub fn repository() -> Result<Self> {
        Ok(Self {
            path: io::repository_root().map_err(err)?.join(".manual-paper"),
            engineering: false,
        })
    }
    /// Only Rust test harnesses choose an isolated path. No HTTP/production CLI path input.
    pub fn engineering(path: PathBuf) -> Self {
        Self {
            path,
            engineering: true,
        }
    }
    fn read(&self) -> Result<(Journal, bool)> {
        let path = disk::safe_path(&self.path).map_err(err)?;
        match std::fs::symlink_metadata(&path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok((Journal::default(), false)),
            Err(e) => Err(err(e)),
            Ok(_) => {
                let dir = disk::lock(&path, false).map_err(err)?;
                let result = load(&dir)?;
                disk::confirm(&path, &dir).map_err(err)?;
                Ok(result)
            }
        }
    }
    fn confirm(
        &self,
        c: &Confirmation,
        clock: &impl Fn() -> Result<u64>,
        hooks: Hooks,
    ) -> Result<(Journal, bool, bool)> {
        let path = disk::safe_path(&self.path).map_err(err)?;
        let (dir, journal, pending) = match std::fs::symlink_metadata(&path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                // Validate before creating anything; preview and rejected confirmation do not initialize.
                Journal::default().confirm(c, clock()?).map_err(err)?;
                (disk::create(&path).map_err(err)?, Journal::default(), false)
            }
            Err(e) => return Err(err(e)),
            Ok(_) => {
                let dir = disk::lock(&path, false).map_err(err)?;
                let (j, p) = load(&dir)?;
                (dir, j, p)
            }
        };
        // Evaluate freshness using the actual clock after lock/load, never caller timestamps.
        let (next, replayed) = journal.confirm(c, clock()?).map_err(err)?;
        if replayed {
            disk::confirm(&path, &dir).map_err(err)?;
        } else {
            persist(&path, &dir, &next, hooks)?;
        }
        Ok((next, replayed, pending && replayed))
    }
    pub fn api(&self, route: &str, bytes: &[u8]) -> Result<Vec<u8>> {
        self.api_with_clock(
            route,
            bytes,
            || wall_clock_ns().map(|ns| ns / 1_000_000).map_err(err),
            Hooks::default(),
        )
    }
    #[cfg(test)]
    fn api_at(&self, route: &str, bytes: &[u8], now: u64, hooks: Hooks) -> Result<Vec<u8>> {
        self.api_with_clock(route, bytes, || Ok(now), hooks)
    }
    fn api_with_clock(
        &self,
        route: &str,
        bytes: &[u8],
        clock: impl Fn() -> Result<u64>,
        hooks: Hooks,
    ) -> Result<Vec<u8>> {
        let (journal, preview, replayed, pending) = match route {
            "/api/paper/state" => {
                let _: Empty = codec::decode(bytes, 32).map_err(err)?;
                let (j, pending) = self.read()?;
                (j, None, false, pending)
            }
            "/api/paper/preview" => {
                let command: paper::Command =
                    codec::decode(bytes, options_core::contracts::MAX_REQUEST_BYTES)
                        .map_err(err)?;
                let (j, pending) = self.read()?;
                let preview = j.preview(command, clock()?).map_err(err)?;
                (j, Some(preview), false, pending)
            }
            "/api/paper/confirm" => {
                let confirmation: Confirmation =
                    codec::decode(bytes, options_core::contracts::MAX_REQUEST_BYTES)
                        .map_err(err)?;
                let (j, replayed, pending) = self.confirm(&confirmation, &clock, hooks)?;
                (j, None, replayed, pending)
            }
            _ => return Err("UNKNOWN_PAPER_ROUTE".into()),
        };
        let blockers = match preview.as_ref().map(|p| &p.command.action) {
            Some(paper::Action::Record(i)) => i.blockers(clock()?),
            _ => vec!["NO_EXECUTION_AUTHORITY".into()],
        };
        canonical_bytes(&Response {
            schema_version: "manual-paper-response.rust.v1",
            storage_scope: if self.engineering {
                "ENGINEERING_TEST_ONLY"
            } else {
                "REPOSITORY_SCOPED_SIMULATION_DECLARATION"
            },
            view: journal.view().map_err(err)?,
            preview,
            blockers,
            replayed,
            uncommitted_pending: pending,
        })
        .map_err(err)
    }
}
