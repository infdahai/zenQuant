//! Bounded offline admission. Input pin paths never grant filesystem authority.
use crate::publication::{Failure, read_bounded};
use options_core::{
    canonical::bytes_sha256,
    codec,
    contracts::{PanelEnvelope, PinnedSource},
};
use serde::Deserialize;
use std::{
    collections::BTreeMap,
    fs,
    path::{Component, Path, PathBuf},
};
pub const SOURCE_LIMIT: usize = 16 * 1024 * 1024;
const DATASETS: [(&str, &str); 2] = [
    (
        "20261009_options_liquidity_panel_v1",
        "b61d04c898cd53545e2bc95ffdede6da6861b18d602d49cb83d57eb52816423b",
    ),
    (
        "20261009_options_venue_scout_v1",
        "c35edb8ad23c8d958fe8f2383786f963160842c55ea8c3489e36efb96d25b983",
    ),
];
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    bytes: u64,
    sha256: String,
}
// Authenticated seal metadata is allowed only after its complete raw hash matches.
#[derive(Deserialize)]
struct Seal {
    files: BTreeMap<String, Entry>,
}
pub fn repository_root() -> Result<PathBuf, Failure> {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .ok_or(Failure::Invalid)?
        .to_path_buf();
    let git = repo.join(".git");
    if git.is_file() {
        let text = fs::read_to_string(&git).map_err(|_| Failure::Invalid)?;
        let path = text
            .strip_prefix("gitdir: ")
            .ok_or(Failure::Invalid)?
            .trim();
        let gitdir = absolute(&repo.join(path))?;
        let common = fs::read_to_string(gitdir.join("commondir")).map_err(|_| Failure::Invalid)?;
        let common = absolute(&gitdir.join(common.trim()))?;
        return common
            .parent()
            .map(Path::to_path_buf)
            .ok_or(Failure::Invalid);
    }
    Ok(repo)
}
pub fn trusted_roots() -> Result<Vec<PathBuf>, Failure> {
    let repo = repository_root()?;
    Ok(DATASETS
        .iter()
        .map(|(name, _)| repo.join("results/crypto_opportunity").join(name))
        .collect())
}
pub fn absolute(path: &Path) -> Result<PathBuf, Failure> {
    let path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|_| Failure::Invalid)?
            .join(path)
    };
    let mut clean = PathBuf::new();
    for c in path.components() {
        match c {
            Component::RootDir => clean.push("/"),
            Component::Normal(p) => clean.push(p),
            Component::CurDir => {}
            Component::ParentDir => {
                if !clean.pop() {
                    return Err(Failure::Invalid);
                }
            }
            _ => return Err(Failure::Invalid),
        }
    }
    Ok(clean)
}
pub fn reject_symlinks(path: &Path) -> Result<(), Failure> {
    let mut p = PathBuf::new();
    for c in absolute(path)?.components() {
        p.push(c);
        match fs::symlink_metadata(&p) {
            Ok(m) if m.file_type().is_symlink() => return Err(Failure::Invalid),
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err(Failure::Invalid),
        }
    }
    Ok(())
}
pub fn validate_output(
    output: &Path,
    protected: &[PathBuf],
) -> Result<crate::publication::PublicationTarget, Failure> {
    let output = absolute(output)?;
    reject_symlinks(&output)?;
    if fs::symlink_metadata(&output).is_ok() {
        return Err(Failure::Invalid);
    };
    if output.file_name().is_none() {
        return Err(Failure::Invalid);
    };
    for p in protected {
        let p = absolute(p)?;
        reject_symlinks(&p)?;
        if output.starts_with(&p) || p.starts_with(&output) {
            return Err(Failure::Invalid);
        }
    }
    crate::publication::PublicationTarget::prepare(output)
}
pub struct VerifiedSources {
    pins: Vec<PinnedSource>,
}
impl VerifiedSources {
    pub fn pins(&self) -> &[PinnedSource] {
        &self.pins
    }
}
pub fn verify_panel_sources(panel: &PanelEnvelope) -> Result<VerifiedSources, Failure> {
    let roots = trusted_roots()?;
    let hashes: Vec<_> = DATASETS.iter().map(|(_, hash)| *hash).collect();
    Ok(VerifiedSources {
        pins: verify_roots(panel, &roots, &hashes)?,
    })
}
fn verify_roots(
    panel: &PanelEnvelope,
    roots: &[PathBuf],
    hashes: &[&str],
) -> Result<Vec<PinnedSource>, Failure> {
    if panel.pinned_sources.len() > 1024 {
        return Err(Failure::Invalid);
    }
    let mut expected = BTreeMap::new();
    let mut total = 0usize;
    // Both seals authenticate before either supplies a path.
    let seals: Vec<_> = roots
        .iter()
        .zip(hashes)
        .map(|(root, hash)| {
            let bytes = read_bounded(&root.join("seal.json"), SOURCE_LIMIT)?;
            if bytes_sha256(&bytes) != *hash {
                return Err(Failure::Invalid);
            }
            Ok(bytes)
        })
        .collect::<Result<_, _>>()?;
    for ((root, bytes), hash) in roots.iter().zip(seals).zip(hashes) {
        total = total.checked_add(bytes.len()).ok_or(Failure::Invalid)?;
        let seal: Seal = codec::decode(&bytes, SOURCE_LIMIT).map_err(|_| Failure::Invalid)?;
        if total > 64 * 1024 * 1024 || expected.len() >= 1024 {
            return Err(Failure::Invalid);
        }
        let sealpath = root.join("seal.json");
        expected.insert(
            sealpath.to_str().ok_or(Failure::Invalid)?.to_owned(),
            PinnedSource {
                path: sealpath.to_str().ok_or(Failure::Invalid)?.into(),
                size_bytes: bytes.len() as u64,
                sha256: (*hash).into(),
            },
        );
        for (relative, e) in seal.files {
            if expected.len() >= 1024 {
                return Err(Failure::Invalid);
            }
            let p = Path::new(&relative);
            if p.components().any(|c| !matches!(c, Component::Normal(_)))
                || relative.is_empty()
                || relative == "seal.json"
                || e.bytes > SOURCE_LIMIT as u64
            {
                return Err(Failure::Invalid);
            };
            let path = root.join(p);
            let bytes = read_bounded(&path, SOURCE_LIMIT)?;
            total = total.checked_add(bytes.len()).ok_or(Failure::Invalid)?;
            if total > 64 * 1024 * 1024
                || expected.len() >= 1024
                || e.bytes != bytes.len() as u64
                || e.sha256 != bytes_sha256(&bytes)
            {
                return Err(Failure::Invalid);
            }
            let path = path.to_str().ok_or(Failure::Invalid)?.to_owned();
            if expected
                .insert(
                    path.clone(),
                    PinnedSource {
                        path,
                        size_bytes: e.bytes,
                        sha256: e.sha256,
                    },
                )
                .is_some()
            {
                return Err(Failure::Invalid);
            }
        }
    }
    let mut actual = BTreeMap::new();
    for pin in &panel.pinned_sources {
        if actual.insert(pin.path.clone(), pin.clone()).is_some() {
            return Err(Failure::Invalid);
        }
    }
    if actual != expected {
        return Err(Failure::Invalid);
    }
    Ok(expected.into_values().collect())
}
