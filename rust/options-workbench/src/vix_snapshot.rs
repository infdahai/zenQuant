//! Offline evidence admission; no vendor download, timestamp invention or trading authority.
use options_core::{
    canonical::bytes_sha256,
    codec,
    contracts::MAX_REQUEST_BYTES,
    error::{Error, ErrorCode, Result},
    vix_observation::{self as vix, DataMode, Source},
};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, path::Path};

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Pin {
    filename: String,
    sha256: String,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Rights {
    status: RightsStatus,
    reference: String,
    evidence_sha256: String,
    local_compute: bool,
    local_display: bool,
    retain_raw: bool,
}
#[derive(Debug, PartialEq, Deserialize, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum RightsStatus {
    SyntheticTestOnly,
    DeclaredAuthorizedLocalOnly,
}
#[derive(Debug, PartialEq, Deserialize, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum TimeBasis {
    SourceEffectiveTimestamp,
    SessionDateOnly,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema_version: String,
    time_basis: TimeBasis,
    rights: Rights,
    files: Vec<Pin>,
}
#[derive(Serialize)]
struct Admission<'a> {
    schema_version: &'static str,
    raw_integrity: &'static str,
    projection_status: &'static str,
    authorization_status: &'static str,
    redistribution_authority: &'static str,
    manifest_sha256: String,
    manifest: &'a Manifest,
    observation: serde_json::Value,
}
fn fail(code: ErrorCode) -> Error {
    Error::new(code)
}
fn hash(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Verifies retained bytes, not whether a vendor licensed them or a projection is faithful.
/// All Source hashes, including the calendar and unselected quotes, must pin local bytes.
pub fn evaluate(request: &[u8], manifest: &[u8], evidence_dir: &Path) -> Result<Vec<u8>> {
    let m: Manifest = codec::decode(manifest, MAX_REQUEST_BYTES)?;
    let r = vix::decode_request(request)?;
    if m.schema_version != "vix-snapshot-manifest.rust.v1" {
        return Err(fail(ErrorCode::InvalidSchema));
    }
    if m.time_basis != TimeBasis::SourceEffectiveTimestamp {
        // A settlement session date is not an observation timestamp.
        return Err(fail(ErrorCode::Unsupported));
    }
    let compatible = matches!(
        (r.mode, &m.rights.status),
        (DataMode::Demonstration, RightsStatus::SyntheticTestOnly)
            | (
                DataMode::SuppliedSnapshotUnverified,
                RightsStatus::DeclaredAuthorizedLocalOnly
            )
    );
    if !compatible
        || m.rights.reference.trim().is_empty()
        || m.rights.reference.len() > 512
        || !m.rights.local_compute
        || !m.rights.local_display
        || !m.rights.retain_raw
        || !hash(&m.rights.evidence_sha256)
    {
        return Err(fail(ErrorCode::MissingEvidence));
    }
    if m.files.is_empty() || m.files.len() > 32 {
        return Err(fail(ErrorCode::LimitExceeded));
    }
    let mut names = BTreeSet::new();
    let mut hashes = BTreeSet::new();
    let mut total = 0usize;
    for pin in &m.files {
        // Flat bundle: no URL, traversal, absolute path or subdirectory from input.
        if pin.filename.is_empty()
            || pin.filename.len() > 128
            || pin.filename == "."
            || pin.filename == ".."
            || !pin
                .filename
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
            || !hash(&pin.sha256)
            || !names.insert(&pin.filename)
        {
            return Err(fail(ErrorCode::InvalidInput));
        }
        let bytes = crate::publication::read_bounded(
            &evidence_dir.join(&pin.filename),
            crate::io::SOURCE_LIMIT,
        )
        .map_err(|_| fail(ErrorCode::MissingEvidence))?;
        total = total
            .checked_add(bytes.len())
            .ok_or(fail(ErrorCode::LimitExceeded))?;
        if total > 64 * 1024 * 1024 {
            return Err(fail(ErrorCode::LimitExceeded));
        }
        if bytes_sha256(&bytes) != pin.sha256 {
            return Err(fail(ErrorCode::ConflictingEvidence));
        }
        hashes.insert(pin.sha256.as_str());
    }
    if !hashes.contains(m.rights.evidence_sha256.as_str()) {
        return Err(fail(ErrorCode::MissingEvidence));
    }
    let pinned = |s: &Source| -> Result<()> {
        if !hashes.contains(s.sha256.as_str()) {
            return Err(fail(ErrorCode::MissingEvidence));
        }
        Ok(())
    };
    if let Some(f) = &r.futures {
        pinned(&f.calendar.source)?;
        for q in &f.quotes {
            pinned(&q.source)?;
        }
    }
    if let Some(s) = &r.spot {
        for q in s.vix9d.iter().chain(s.vix.iter()) {
            pinned(&q.source)?;
        }
    }
    let observation = crate::vix_api::evaluate("/api/vix/evaluate", request)?;
    serde_json::to_vec_pretty(&Admission {
        schema_version: "vix-snapshot-admission.rust.v1",
        raw_integrity: "LOCAL_PINNED_BYTES_MATCH",
        projection_status: "SUPPLIED_PROJECTION_UNVERIFIED",
        authorization_status: "SUPPLIER_DECLARATION_NOT_INDEPENDENTLY_VERIFIED",
        redistribution_authority: "NONE_GRANTED_BY_ADAPTER",
        manifest_sha256: bytes_sha256(manifest),
        manifest: &m,
        observation: serde_json::from_slice(&observation).map_err(|_| fail(ErrorCode::Internal))?,
    })
    .map_err(|_| fail(ErrorCode::Internal))
}
