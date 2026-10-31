//! Concrete shared snapshot IO: directory lock, bounded regular files and fsync/rename.
use crate::{io, publication};
use std::{
    ffi::CString,
    fs::File,
    io::Write,
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

pub(crate) fn publish_initial<F>(path: &Path, hooks: Hooks, write: F) -> Result<bool>
where
    F: FnOnce(&Path, &DirectoryLock) -> Result<()>,
{
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let parent_path = path.parent().ok_or(StoreError::InvalidStore)?;
    trusted_parent(parent_path)?;
    let parent = publication::open_dir(parent_path, false).map_err(|_| StoreError::InvalidStore)?;
    use std::os::unix::fs::DirBuilderExt;
    let stage_path = loop {
        let candidate = parent_path.join(format!(
            ".paper-init-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        match std::fs::DirBuilder::new().mode(0o700).create(&candidate) {
            Ok(()) => break candidate,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(_) => return Err(StoreError::WriteFailed),
        }
    };
    struct Cleanup(PathBuf);
    impl Drop for Cleanup {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    let _cleanup = Cleanup(stage_path.clone());
    // create_dir uses the process umask; remove all group/other permissions.
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&stage_path, std::fs::Permissions::from_mode(0o700))
        .map_err(|_| StoreError::WriteFailed)?;
    let stage_parent =
        publication::open_dir(&stage_path, false).map_err(|_| StoreError::InvalidStore)?;
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
            return Ok(false);
        }
        return Err(StoreError::WriteFailed);
    }
    checkpoint(hooks, "published").map_err(|_| StoreError::DurabilityUnconfirmed)?;
    confirm(path, &dir)?;
    checkpoint(hooks, "parent_synced").map_err(|_| StoreError::DurabilityUnconfirmed)?;
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
