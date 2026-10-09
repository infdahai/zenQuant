use super::*;
use options_core::contracts::*;
use std::sync::atomic::{AtomicU64, Ordering};
fn temp() -> PathBuf {
    static N: AtomicU64 = AtomicU64::new(0);
    let p = fs::canonicalize(std::env::temp_dir())
        .unwrap()
        .join(format!(
            "options-io-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
    fs::create_dir(&p).unwrap();
    p
}
fn setup() -> (PathBuf, Vec<PathBuf>, Vec<String>, PanelEnvelope) {
    let p = temp();
    let mut roots = vec![];
    let mut hashes = vec![];
    let mut pins = vec![];
    for n in ["a", "b"] {
        let root = p.join(n);
        fs::create_dir(&root).unwrap();
        let raw = b"bounded original source";
        fs::write(root.join("unused.raw"), raw).unwrap();
        let bytes=serde_json::to_vec(&serde_json::json!({"files":{"unused.raw":{"bytes":raw.len(),"sha256":bytes_sha256(raw)}}})).unwrap();
        fs::write(root.join("seal.json"), &bytes).unwrap();
        let hash = bytes_sha256(&bytes);
        pins.push(PinnedSource {
            path: root.join("seal.json").to_str().unwrap().into(),
            size_bytes: bytes.len() as u64,
            sha256: hash.clone(),
        });
        pins.push(PinnedSource {
            path: root.join("unused.raw").to_str().unwrap().into(),
            size_bytes: raw.len() as u64,
            sha256: bytes_sha256(raw),
        });
        roots.push(root);
        hashes.push(hash);
    }
    let envelope = PanelEnvelope {
        schema_version: "options-workbench-panel.rust.v1".into(),
        exporter_version: "options-workbench-exporter.rust.v1".into(),
        raw_source_integrity: RawSourceIntegrity::RawPinnedBytesVerified,
        projection_trust: ProjectionTrust::ExportedProjectionUnattested,
        pinned_sources: pins,
        projection_sha256: "0".repeat(64),
        quotes: vec![],
        gaps: vec![],
    };
    (p, roots, hashes, envelope)
}
#[test]
fn complete_pins_include_both_seals_and_every_unused_source() {
    let (p, roots, hashes, panel) = setup();
    let hashes: Vec<_> = hashes.iter().map(String::as_str).collect();
    assert_eq!(verify_roots(&panel, &roots, &hashes).unwrap().len(), 4);
    let mut omission = panel.clone();
    omission.pinned_sources.pop();
    assert_eq!(
        verify_roots(&omission, &roots, &hashes),
        Err(Failure::Invalid)
    );
    let mut duplicate = panel.clone();
    duplicate
        .pinned_sources
        .push(duplicate.pinned_sources[0].clone());
    assert_eq!(
        verify_roots(&duplicate, &roots, &hashes),
        Err(Failure::Invalid)
    );
    let mut arbitrary = panel.clone();
    arbitrary.pinned_sources[0].path = p.join("attacker").to_str().unwrap().into();
    assert_eq!(
        verify_roots(&arbitrary, &roots, &hashes),
        Err(Failure::Invalid)
    );
    fs::write(roots[1].join("unused.raw"), b"altered unused evidence").unwrap();
    assert_eq!(verify_roots(&panel, &roots, &hashes), Err(Failure::Invalid));
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn seal_hashes_are_authenticated_and_traversal_and_symlinks_reject() {
    let (p, roots, mut hashes, panel) = setup();
    fs::write(
        roots[0].join("seal.json"),
        b"{\"files\":{\"../../outside\":{\"bytes\":0,\"sha256\":\"bad\"}}}",
    )
    .unwrap();
    let refs: Vec<_> = hashes.iter().map(String::as_str).collect();
    assert_eq!(verify_roots(&panel, &roots, &refs), Err(Failure::Invalid));
    hashes[0] = bytes_sha256(&fs::read(roots[0].join("seal.json")).unwrap());
    let refs: Vec<_> = hashes.iter().map(String::as_str).collect();
    assert_eq!(verify_roots(&panel, &roots, &refs), Err(Failure::Invalid));
    fs::remove_dir_all(p).unwrap();
    let (p, roots, hashes, panel) = setup();
    fs::rename(roots[0].join("unused.raw"), p.join("outside")).unwrap();
    std::os::unix::fs::symlink(p.join("outside"), roots[0].join("unused.raw")).unwrap();
    let refs: Vec<_> = hashes.iter().map(String::as_str).collect();
    assert_eq!(verify_roots(&panel, &roots, &refs), Err(Failure::Invalid));
    fs::remove_dir_all(p).unwrap();
}
