//! Native descriptor-relative, exclusive report publication.
use std::{
    ffi::CString,
    fs::File,
    io::{Read, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::{ffi::OsStrExt, fs::MetadataExt},
    },
    path::{Component, Path},
    sync::atomic::{AtomicU64, Ordering},
};
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Failure {
    Invalid,
    Incomplete,
    PublishedDurabilityUnconfirmed,
}
impl Failure {
    pub fn message(self) -> &'static str {
        match self {
            Self::Invalid => "INVALID_INPUT",
            Self::Incomplete => "INCOMPLETE_PUBLICATION",
            Self::PublishedDurabilityUnconfirmed => "PUBLISHED_DURABILITY_UNCONFIRMED",
        }
    }
}
fn c(s: &std::ffi::OsStr) -> Result<CString, Failure> {
    CString::new(s.as_bytes()).map_err(|_| Failure::Invalid)
}
pub(crate) fn directory(parent: &File, name: &std::ffi::OsStr) -> Result<File, Failure> {
    let name = c(name)?;
    let fd = unsafe {
        libc::openat(
            parent.as_raw_fd(),
            name.as_ptr(),
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
        )
    };
    if fd < 0 {
        Err(Failure::Invalid)
    } else {
        Ok(unsafe { File::from_raw_fd(fd) })
    }
}
fn root() -> Result<File, Failure> {
    File::open("/").map_err(|_| Failure::Invalid)
}
pub(crate) fn open_dir(path: &Path, create: bool) -> Result<File, Failure> {
    let mut fd = root()?;
    for part in path.components() {
        match part {
            Component::RootDir => {}
            Component::Normal(name) => {
                if create {
                    let name = c(name)?;
                    let rc = unsafe { libc::mkdirat(fd.as_raw_fd(), name.as_ptr(), 0o700) };
                    if rc != 0
                        && std::io::Error::last_os_error().raw_os_error() != Some(libc::EEXIST)
                    {
                        return Err(Failure::Incomplete);
                    }
                    if rc == 0 {
                        fd.sync_all().map_err(|_| Failure::Incomplete)?
                    }
                }
                fd = directory(&fd, name)?
            }
            _ => return Err(Failure::Invalid),
        }
    }
    Ok(fd)
}
pub fn read_bounded(path: &Path, limit: usize) -> Result<Vec<u8>, Failure> {
    let path = crate::io::absolute(path)?;
    let parent = open_dir(path.parent().ok_or(Failure::Invalid)?, false)?;
    let name = c(path.file_name().ok_or(Failure::Invalid)?)?;
    let fd = unsafe {
        libc::openat(
            parent.as_raw_fd(),
            name.as_ptr(),
            libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC,
        )
    };
    if fd < 0 {
        return Err(Failure::Invalid);
    }
    let file = unsafe { File::from_raw_fd(fd) };
    if !file.metadata().map_err(|_| Failure::Invalid)?.is_file() {
        return Err(Failure::Invalid);
    }
    let mut bytes = Vec::new();
    file.take(limit.checked_add(1).ok_or(Failure::Invalid)? as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| Failure::Invalid)?;
    if bytes.len() > limit {
        return Err(Failure::Invalid);
    }
    Ok(bytes)
}
pub(crate) fn same_parent(path: &Path, held: &File) -> Result<(), Failure> {
    let now = open_dir(path, false)?;
    let a = now.metadata().map_err(|_| Failure::Incomplete)?;
    let b = held.metadata().map_err(|_| Failure::Incomplete)?;
    if a.dev() != b.dev() || a.ino() != b.ino() {
        return Err(Failure::Incomplete);
    }
    Ok(())
}
pub struct PublicationTarget {
    path: std::path::PathBuf,
    ancestor: std::path::PathBuf,
    anchor: File,
}
impl PublicationTarget {
    pub(crate) fn prepare(path: std::path::PathBuf) -> Result<Self, Failure> {
        let path = crate::io::absolute(&path)?;
        let mut ancestor = path.parent().ok_or(Failure::Invalid)?.to_path_buf();
        while !ancestor.exists() {
            if !ancestor.pop() {
                return Err(Failure::Invalid);
            }
        }
        let anchor = open_dir(&ancestor, false)?;
        Ok(Self {
            path,
            ancestor,
            anchor,
        })
    }
}
#[derive(Clone, Copy, Default)]
struct PublicationHooks {
    before_rename: Option<fn(&Path)>,
    fail_at: Option<&'static str>,
}
fn fail(h: PublicationHooks, point: &str) -> Result<(), Failure> {
    if h.fail_at == Some(point) {
        Err(Failure::Incomplete)
    } else {
        Ok(())
    }
}
/// Tests may inject failures at durability boundaries; production uses Default.
/// Fixed artifact names only. Stage directories are retained on all pre-rename errors.
pub fn publish(target: &PublicationTarget, artifacts: &[(&str, Vec<u8>)]) -> Result<(), Failure> {
    publish_internal(target, artifacts, PublicationHooks::default())
}
fn publish_internal(
    target: &PublicationTarget,
    artifacts: &[(&str, Vec<u8>)],
    hooks: PublicationHooks,
) -> Result<(), Failure> {
    if artifacts.len() != 3
        || ["report.json", "REPORT.md", "identity.json"]
            .iter()
            .any(|expected| {
                artifacts
                    .iter()
                    .filter(|(name, _)| name == expected)
                    .count()
                    != 1
            })
    {
        return Err(Failure::Invalid);
    }
    publish_admitted(target, artifacts, hooks).map_err(|error| match error {
        Failure::PublishedDurabilityUnconfirmed => error,
        _ => Failure::Incomplete,
    })
}
fn publish_admitted(
    target: &PublicationTarget,
    artifacts: &[(&str, Vec<u8>)],
    hooks: PublicationHooks,
) -> Result<(), Failure> {
    let output = &target.path;
    let parent_path = output.parent().ok_or(Failure::Invalid)?;
    crate::io::reject_symlinks(output)?;
    let ancestor = &target.ancestor;
    let anchor = &target.anchor;
    same_parent(ancestor, anchor)?;
    let mut parent = anchor.try_clone().map_err(|_| Failure::Incomplete)?;
    for component in parent_path
        .strip_prefix(ancestor)
        .map_err(|_| Failure::Invalid)?
        .components()
    {
        let Component::Normal(name) = component else {
            return Err(Failure::Invalid);
        };
        same_parent(ancestor, anchor)?;
        let encoded = c(name)?;
        let rc = unsafe { libc::mkdirat(parent.as_raw_fd(), encoded.as_ptr(), 0o700) };
        if rc != 0 && std::io::Error::last_os_error().raw_os_error() != Some(libc::EEXIST) {
            return Err(Failure::Incomplete);
        }
        if rc == 0 {
            parent.sync_all().map_err(|_| Failure::Incomplete)?;
        }
        parent = directory(&parent, name)?;
    }
    same_parent(ancestor, anchor)?;
    same_parent(parent_path, &parent)?;
    let dst = c(output.file_name().ok_or(Failure::Invalid)?)?;
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    let stage_name = format!(
        ".options-workbench-incomplete-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    );
    let stage = c(std::ffi::OsStr::new(&stage_name))?;
    if unsafe { libc::mkdirat(parent.as_raw_fd(), stage.as_ptr(), 0o700) } != 0 {
        return Err(Failure::Incomplete);
    }
    parent.sync_all().map_err(|_| Failure::Incomplete)?;
    let staging = directory(&parent, std::ffi::OsStr::new(&stage_name))?;
    for (name, bytes) in artifacts {
        fail(hooks, "write")?;
        let name = c(std::ffi::OsStr::new(name))?;
        let fd = unsafe {
            libc::openat(
                staging.as_raw_fd(),
                name.as_ptr(),
                libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                0o600,
            )
        };
        if fd < 0 {
            return Err(Failure::Incomplete);
        }
        let mut file = unsafe { File::from_raw_fd(fd) };
        file.write_all(bytes).map_err(|_| Failure::Incomplete)?;
        fail(hooks, "file_fsync")?;
        file.sync_all().map_err(|_| Failure::Incomplete)?;
    }
    fail(hooks, "stage_fsync")?;
    staging.sync_all().map_err(|_| Failure::Incomplete)?;
    if let Some(f) = hooks.before_rename {
        f(output)
    }
    same_parent(ancestor, anchor)?;
    same_parent(parent_path, &parent)?;
    same_parent(&parent_path.join(&stage_name), &staging)?;
    fail(hooks, "rename")?;
    #[cfg(target_os = "macos")]
    let rc = unsafe {
        libc::renameatx_np(
            parent.as_raw_fd(),
            stage.as_ptr(),
            parent.as_raw_fd(),
            dst.as_ptr(),
            libc::RENAME_EXCL,
        )
    };
    #[cfg(target_os = "linux")]
    let rc = unsafe {
        libc::renameat2(
            parent.as_raw_fd(),
            stage.as_ptr(),
            parent.as_raw_fd(),
            dst.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    let rc = -1;
    if rc != 0 {
        return Err(Failure::Incomplete);
    }
    if fail(hooks, "parent_fsync").is_err()
        || parent.sync_all().is_err()
        || same_parent(parent_path, &parent).is_err()
        || same_parent(ancestor, anchor).is_err()
        || same_parent(output, &staging).is_err()
    {
        return Err(Failure::PublishedDurabilityUnconfirmed);
    }
    Ok(())
}
#[cfg(test)]
#[path = "publication_tests.rs"]
mod tests;
