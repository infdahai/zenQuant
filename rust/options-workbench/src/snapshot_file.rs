//! Concrete shared snapshot IO: directory lock, bounded regular files and fsync/rename.
use crate::{io, publication};
use std::{
    ffi::CString,
    fs::File,
    io::{Read, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::{ffi::OsStrExt, fs::MetadataExt},
    },
    path::{Path, PathBuf},
};
pub(crate) const MAX_STORE_BYTES: usize = 16 * 1024 * 1024;
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
pub(crate) fn safe_path(path: &Path) -> Result<PathBuf> {
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
pub(crate) struct DirectoryLock(File);
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
pub(crate) fn lock(path: &Path, wait: bool) -> Result<DirectoryLock> {
    let dir = publication::open_dir(path, false).map_err(|_| StoreError::InvalidStore)?;
    lock_directory(path, dir, wait)
}
fn lock_directory(path: &Path, dir: File, wait: bool) -> Result<DirectoryLock> {
    if unsafe {
        libc::flock(
            dir.as_raw_fd(),
            libc::LOCK_EX | if wait { 0 } else { libc::LOCK_NB },
        )
    } != 0
    {
        return Err(
            if std::io::Error::last_os_error().kind() == std::io::ErrorKind::WouldBlock {
                StoreError::Busy
            } else {
                StoreError::InvalidStore
            },
        );
    }
    let held = DirectoryLock(dir);
    publication::same_parent(path, &held).map_err(|_| StoreError::InvalidStore)?;
    Ok(held)
}
fn name(value: &str) -> CString {
    CString::new(value).expect("fixed internal filename")
}
pub(crate) fn open_file(dir: &File, filename: &str) -> Result<Option<File>> {
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
pub(crate) fn confirm(path: &Path, dir: &File) -> Result<()> {
    publication::same_parent(path, dir).map_err(|_| StoreError::DurabilityUnconfirmed)?;
    dir.sync_all()
        .map_err(|_| StoreError::DurabilityUnconfirmed)?;
    let parent_path = path.parent().ok_or(StoreError::DurabilityUnconfirmed)?;
    let parent =
        publication::open_dir(parent_path, false).map_err(|_| StoreError::DurabilityUnconfirmed)?;
    parent
        .sync_all()
        .map_err(|_| StoreError::DurabilityUnconfirmed)?;
    publication::same_parent(parent_path, &parent)
        .map_err(|_| StoreError::DurabilityUnconfirmed)?;
    publication::same_parent(path, dir).map_err(|_| StoreError::DurabilityUnconfirmed)
}

#[derive(Clone, Copy, Default)]
pub(crate) struct Hooks {
    pub(crate) fail_at: Option<&'static str>,
    pub(crate) observe: Option<fn(&str)>,
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
pub(crate) fn replace(path: &Path, dir: &File, bytes: &[u8], h: Hooks) -> Result<()> {
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
    file.write_all(bytes).map_err(|_| StoreError::WriteFailed)?;
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
    Ok(())
}

// Other users cannot replace the staging parent; malicious same-UID processes
// are outside this contract because they can already alter the journal itself.
pub(crate) fn trusted_parent(path: &Path) -> Result<()> {
    let uid = unsafe { libc::geteuid() };
    for (i, p) in path.ancestors().enumerate() {
        let m = std::fs::symlink_metadata(p).map_err(|_| StoreError::InvalidStore)?;
        if !m.is_dir()
            || (m.uid() != uid && m.uid() != 0)
            || (m.mode() & 0o022 != 0 && !(m.uid() == 0 && m.mode() & 0o1000 != 0))
            || (i == 0 && (m.uid() != uid || m.mode() & 0o022 != 0))
        {
            return Err(StoreError::InvalidStore);
        }
    }
    Ok(())
}

fn stage_marker(path: &Path) -> Result<Vec<u8>> {
    let base = path.file_name().ok_or(StoreError::InvalidStore)?;
    Ok(format!(
        "zenquant-paper-stage-v1:{}",
        options_core::canonical::bytes_sha256(base.as_bytes())
    )
    .into_bytes())
}

fn private(meta: &std::fs::Metadata) -> bool {
    meta.uid() == unsafe { libc::geteuid() } && meta.mode() & 0o077 == 0
}

// Only a fully validated protocol stage may be reclaimed. Unknown or legacy
// entries are left untouched, including stages from a different destination.
fn reclaim_stage(
    parent: &File,
    parent_path: &Path,
    stage_path: &Path,
    stage: &File,
    marker: &[u8],
) -> Result<()> {
    if publication::same_parent(stage_path, stage).is_err() {
        return Ok(());
    }
    let meta = stage.metadata().map_err(|_| StoreError::InvalidStore)?;
    if !meta.is_dir() || !private(&meta) {
        return Ok(());
    }
    let mut owner = match open_file(stage, "owner") {
        Ok(Some(f)) => f,
        _ => return Ok(()),
    };
    if !private(&owner.metadata().map_err(|_| StoreError::InvalidStore)?) {
        return Ok(());
    }
    let mut bytes = Vec::new();
    if Read::by_ref(&mut owner)
        .take(256)
        .read_to_end(&mut bytes)
        .is_err()
        || bytes != marker
    {
        return Ok(());
    }
    let entries = std::fs::read_dir(stage_path).map_err(|_| StoreError::InvalidStore)?;
    let mut has_journal = false;
    for entry in entries {
        let entry = entry.map_err(|_| StoreError::InvalidStore)?;
        match entry.file_name().to_str() {
            Some("owner") => (),
            Some("journal") => has_journal = true,
            _ => return Ok(()),
        }
    }
    let mut journal = None;
    let mut children = Vec::new();
    if has_journal {
        let j = match publication::directory(stage, std::ffi::OsStr::new("journal")) {
            Ok(j) => j,
            Err(_) => return Ok(()),
        };
        let jm = j.metadata().map_err(|_| StoreError::InvalidStore)?;
        let jp = stage_path.join("journal");
        if !private(&jm) || publication::same_parent(&jp, &j).is_err() {
            return Ok(());
        }
        for child in std::fs::read_dir(&jp).map_err(|_| StoreError::InvalidStore)? {
            let child = child.map_err(|_| StoreError::InvalidStore)?;
            let filename = child.file_name();
            let Some(s) = filename.to_str() else {
                return Ok(());
            };
            if s != SNAPSHOT && s != PENDING {
                return Ok(());
            }
            let file = match open_file(&j, s) {
                Ok(Some(f)) => f,
                _ => return Ok(()),
            };
            if !private(&file.metadata().map_err(|_| StoreError::InvalidStore)?) {
                return Ok(());
            }
            children.push(s.to_owned());
        }
        journal = Some(j);
    }
    if publication::same_parent(parent_path, parent).is_err()
        || publication::same_parent(stage_path, stage).is_err()
    {
        return Ok(());
    }
    if let Some(j) = journal {
        if publication::same_parent(&stage_path.join("journal"), &j).is_err() {
            return Ok(());
        }
        for child in children {
            if unsafe { libc::unlinkat(j.as_raw_fd(), name(&child).as_ptr(), 0) } != 0 {
                return Err(StoreError::WriteFailed);
            }
        }
        if unsafe {
            libc::unlinkat(
                stage.as_raw_fd(),
                name("journal").as_ptr(),
                libc::AT_REMOVEDIR,
            )
        } != 0
        {
            return Err(StoreError::WriteFailed);
        }
    }
    if unsafe { libc::unlinkat(stage.as_raw_fd(), name("owner").as_ptr(), 0) } != 0 {
        return Err(StoreError::WriteFailed);
    }
    let stage_name = CString::new(
        stage_path
            .file_name()
            .ok_or(StoreError::InvalidStore)?
            .as_bytes(),
    )
    .map_err(|_| StoreError::InvalidStore)?;
    if unsafe { libc::unlinkat(parent.as_raw_fd(), stage_name.as_ptr(), libc::AT_REMOVEDIR) } != 0 {
        return Err(StoreError::WriteFailed);
    }
    parent
        .sync_all()
        .map_err(|_| StoreError::DurabilityUnconfirmed)
}

pub(crate) fn recover_stages(path: &Path) -> Result<()> {
    let parent_path = path.parent().ok_or(StoreError::InvalidStore)?;
    let parent = publication::open_dir(parent_path, false).map_err(|_| StoreError::InvalidStore)?;
    let marker = stage_marker(path)?;
    for entry in std::fs::read_dir(parent_path).map_err(|_| StoreError::InvalidStore)? {
        let entry = entry.map_err(|_| StoreError::InvalidStore)?;
        if !entry.file_name().as_bytes().starts_with(b".paper-init-v1-") {
            continue;
        }
        let stage = match publication::directory(&parent, &entry.file_name()) {
            Ok(d) => d,
            Err(_) => continue,
        };
        if unsafe { libc::flock(stage.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            continue;
        }
        let held = DirectoryLock(stage);
        reclaim_stage(&parent, parent_path, &entry.path(), &held, &marker)?;
    }
    Ok(())
}

pub(crate) fn publish_initial<F>(path: &Path, hooks: Hooks, write: F) -> Result<bool>
where
    F: FnOnce(&Path, &DirectoryLock) -> Result<()>,
{
    let parent_path = path.parent().ok_or(StoreError::InvalidStore)?;
    trusted_parent(parent_path)?;
    let parent = publication::open_dir(parent_path, false).map_err(|_| StoreError::InvalidStore)?;
    use std::os::unix::fs::DirBuilderExt;
    let mut template = CString::new(
        parent_path
            .join(".paper-init-v1-XXXXXX")
            .as_os_str()
            .as_bytes(),
    )
    .map_err(|_| StoreError::InvalidStore)?
    .into_bytes_with_nul();
    if unsafe { libc::mkdtemp(template.as_mut_ptr().cast()) }.is_null() {
        return Err(StoreError::WriteFailed);
    }
    let stage_path = PathBuf::from(std::ffi::OsStr::from_bytes(&template[..template.len() - 1]));
    let stage_parent = lock(&stage_path, false)?;
    let fd = unsafe {
        libc::openat(
            stage_parent.as_raw_fd(),
            name("owner").as_ptr(),
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            0o600,
        )
    };
    if fd < 0 {
        return Err(StoreError::WriteFailed);
    }
    let mut owner = unsafe { File::from_raw_fd(fd) };
    let marker = stage_marker(path)?;
    owner
        .write_all(&marker)
        .and_then(|_| owner.sync_all())
        .and_then(|_| stage_parent.sync_all())
        .map_err(|_| StoreError::WriteFailed)?;
    struct Cleanup<'a> {
        parent: &'a File,
        parent_path: &'a Path,
        stage_path: &'a Path,
        stage: &'a File,
        marker: &'a [u8],
    }
    impl Drop for Cleanup<'_> {
        fn drop(&mut self) {
            let _ = reclaim_stage(
                self.parent,
                self.parent_path,
                self.stage_path,
                self.stage,
                self.marker,
            );
        }
    }
    let cleanup = Cleanup {
        parent: &parent,
        parent_path,
        stage_path: &stage_path,
        stage: &stage_parent,
        marker: &marker,
    };
    let stage = stage_path.join("journal");
    std::fs::DirBuilder::new()
        .mode(0o700)
        .create(&stage)
        .map_err(|_| StoreError::WriteFailed)?;
    let dir = lock(&stage, false)?;
    write(&stage, &dir)?;
    checkpoint(hooks, "before_publish")?;
    trusted_parent(parent_path)?;
    publication::same_parent(parent_path, &parent).map_err(|_| StoreError::InvalidStore)?;
    publication::same_parent(&stage_path, &stage_parent).map_err(|_| StoreError::InvalidStore)?;
    publication::same_parent(&stage, &dir).map_err(|_| StoreError::InvalidStore)?;
    #[cfg(target_os = "linux")]
    let dest = CString::new(path.file_name().ok_or(StoreError::InvalidStore)?.as_bytes())
        .map_err(|_| StoreError::InvalidStore)?;
    #[cfg(target_os = "linux")]
    let rc = unsafe {
        libc::renameat2(
            stage_parent.as_raw_fd(),
            name("journal").as_ptr(),
            parent.as_raw_fd(),
            dest.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    #[cfg(not(target_os = "linux"))]
    let rc = -1;
    if rc != 0 {
        if std::io::Error::last_os_error().kind() == std::io::ErrorKind::AlreadyExists {
            reclaim_stage(&parent, parent_path, &stage_path, &stage_parent, &marker)
                .map_err(|_| StoreError::DurabilityUnconfirmed)?;
            std::mem::forget(cleanup);
            return Ok(false);
        }
        return Err(StoreError::WriteFailed);
    }
    checkpoint(hooks, "published").map_err(|_| StoreError::DurabilityUnconfirmed)?;
    confirm(path, &dir)?;
    checkpoint(hooks, "parent_synced").map_err(|_| StoreError::DurabilityUnconfirmed)?;
    reclaim_stage(&parent, parent_path, &stage_path, &stage_parent, &marker)
        .map_err(|_| StoreError::DurabilityUnconfirmed)?;
    std::mem::forget(cleanup);
    Ok(true)
}

pub(crate) fn create(path: &Path) -> Result<DirectoryLock> {
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
    let dir = lock_directory(&path, created, false)?;
    if open_file(&dir, SNAPSHOT)?.is_some() || open_file(&dir, PENDING)?.is_some() {
        return Err(StoreError::InvalidStore);
    }
    Ok(dir)
}
