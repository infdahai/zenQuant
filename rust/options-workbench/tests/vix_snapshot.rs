//! Synthetic evidence only. No licensed quotes or credentials in fixtures.
use options_core::{canonical::bytes_sha256, error::ErrorCode, vix_observation as vix};
use options_workbench::{vix_api::DEMO, vix_snapshot::evaluate};
use serde_json::{Value, json};
use std::{
    fs,
    path::PathBuf,
    process::Command,
    sync::atomic::{AtomicU64, Ordering},
};
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Bundle {
    dir: PathBuf,
    request: vix::Request,
    manifest: Value,
}
impl Bundle {
    fn new() -> Self {
        let dir = std::env::temp_dir().join(format!(
            "vix-snapshot-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&dir).unwrap();
        let raw = b"SYNTHETIC quote/calendar evidence, invented prices";
        let permission = b"SYNTHETIC test permission record, no real data license";
        fs::write(dir.join("raw.txt"), raw).unwrap();
        fs::write(dir.join("permission.txt"), permission).unwrap();
        let mut request = vix::decode_request(DEMO).unwrap();
        let f = request.futures.as_mut().unwrap();
        f.calendar.source.sha256 = bytes_sha256(raw);
        for q in &mut f.quotes {
            q.source.sha256 = bytes_sha256(raw);
        }
        let s = request.spot.as_mut().unwrap();
        for q in s.vix9d.iter_mut().chain(s.vix.iter_mut()) {
            q.source.sha256 = bytes_sha256(raw);
        }
        Self {
            dir,
            request,
            manifest: json!({
                "schema_version": "vix-snapshot-manifest.rust.v1",
                "time_basis": "SOURCE_EFFECTIVE_TIMESTAMP",
                "rights": {"status": "SYNTHETIC_TEST_ONLY", "reference": "SYNTHETIC test only",
                    "evidence_sha256": bytes_sha256(permission), "local_compute": true,
                    "local_display": true, "retain_raw": true},
                "files": [{"filename": "raw.txt", "sha256": bytes_sha256(raw)},
                    {"filename": "permission.txt", "sha256": bytes_sha256(permission)}]
            }),
        }
    }
    fn run(&self) -> options_core::error::Result<Value> {
        let bytes = evaluate(
            &serde_json::to_vec(&self.request).unwrap(),
            &serde_json::to_vec(&self.manifest).unwrap(),
            &self.dir,
        )?;
        Ok(serde_json::from_slice(&bytes).unwrap())
    }
}
impl Drop for Bundle {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.dir).unwrap();
    }
}
#[test]
fn admitted_bytes_do_not_upgrade_projection_or_license_trust() {
    let b = Bundle::new();
    let out = b.run().unwrap();
    assert_eq!(out["raw_integrity"], "LOCAL_PINNED_BYTES_MATCH");
    assert_eq!(out["projection_status"], "SUPPLIED_PROJECTION_UNVERIFIED");
    assert_eq!(out["redistribution_authority"], "NONE_GRANTED_BY_ADAPTER");
    assert_eq!(out["observation"]["report"]["mode"], "DEMONSTRATION");
    assert_eq!(
        out["observation"]["report"]["evidence_status"],
        "UNVERIFIED"
    );
    assert_eq!(out["observation"]["report"]["futures"]["status"], "READY");
    let request: vix::Request =
        serde_json::from_str(out["observation"]["request_json"].as_str().unwrap()).unwrap();
    assert_eq!(
        request.futures.unwrap().quotes[0].observed_ms,
        b.request.futures.as_ref().unwrap().quotes[0].observed_ms
    );
}
#[test]
fn corrupt_missing_and_unpinned_evidence_fail_before_calculation() {
    let b = Bundle::new();
    fs::write(b.dir.join("raw.txt"), b"changed").unwrap();
    assert_eq!(b.run().unwrap_err().code, ErrorCode::ConflictingEvidence);
    fs::remove_file(b.dir.join("raw.txt")).unwrap();
    assert_eq!(b.run().unwrap_err().code, ErrorCode::MissingEvidence);
    let mut b = Bundle::new();
    b.request.futures.as_mut().unwrap().calendar.source.sha256 = "a".repeat(64);
    assert_eq!(b.run().unwrap_err().code, ErrorCode::MissingEvidence);
}
#[test]
fn license_record_and_explicit_local_rights_are_required() {
    for field in ["local_compute", "local_display", "retain_raw"] {
        let mut b = Bundle::new();
        b.manifest["rights"][field] = json!(false);
        assert_eq!(b.run().unwrap_err().code, ErrorCode::MissingEvidence);
    }
    let mut b = Bundle::new();
    b.manifest["rights"]["evidence_sha256"] = json!("a".repeat(64));
    assert_eq!(b.run().unwrap_err().code, ErrorCode::MissingEvidence);
}
#[test]
fn synthetic_and_supplied_modes_require_distinct_declarations() {
    let mut b = Bundle::new();
    b.request.mode = vix::DataMode::SuppliedSnapshotUnverified;
    assert_eq!(b.run().unwrap_err().code, ErrorCode::MissingEvidence);
    // Tests a declared branch only; this synthetic record is not a real permission.
    b.manifest["rights"]["status"] = json!("DECLARED_AUTHORIZED_LOCAL_ONLY");
    assert_eq!(
        b.run().unwrap()["observation"]["report"]["mode"],
        "SUPPLIED_SNAPSHOT_UNVERIFIED"
    );
    b.request.mode = vix::DataMode::Demonstration;
    assert_eq!(b.run().unwrap_err().code, ErrorCode::MissingEvidence);
}
#[test]
fn date_only_settlement_cannot_be_made_fresh_by_import_time() {
    let mut b = Bundle::new();
    b.manifest["time_basis"] = json!("SESSION_DATE_ONLY");
    assert_eq!(b.run().unwrap_err().code, ErrorCode::Unsupported);
}
#[test]
fn unsafe_duplicate_and_symlink_paths_are_rejected() {
    for name in [
        "../raw.txt",
        "/raw.txt",
        "a/raw.txt",
        ".",
        "..",
        "https://example.com",
    ] {
        let mut b = Bundle::new();
        b.manifest["files"][0]["filename"] = json!(name);
        assert_eq!(b.run().unwrap_err().code, ErrorCode::InvalidInput);
    }
    let mut b = Bundle::new();
    let pin = b.manifest["files"][0].clone();
    b.manifest["files"].as_array_mut().unwrap().push(pin);
    assert_eq!(b.run().unwrap_err().code, ErrorCode::InvalidInput);
    let b = Bundle::new();
    fs::rename(b.dir.join("raw.txt"), b.dir.join("other.txt")).unwrap();
    std::os::unix::fs::symlink("other.txt", b.dir.join("raw.txt")).unwrap();
    assert_eq!(b.run().unwrap_err().code, ErrorCode::MissingEvidence);
}
#[test]
fn october_cutoff_is_1300_utc_and_roll_does_not_refresh_old_prices() {
    let mut b = Bundle::new();
    // CFE notice 2026011501: Oct 21 08:00 CT = 13:00 UTC = 21:00 Shanghai.
    let boundary = 1_792_587_600_000;
    let f = b.request.futures.as_mut().unwrap();
    assert_eq!(f.calendar.contracts[0].last_trade_ms, boundary);
    // November is CST (UTC-6), not October's CDT (UTC-5).
    assert_eq!(f.calendar.contracts[2].last_trade_ms, 1_795_010_400_000);
    f.previous_pair = Some(vix::PreviousPair {
        cutoff_ms: b.request.cutoff_ms,
        front: "VX/V6".into(),
        next: "VX/X6".into(),
    });
    for (cutoff, front, next, roll) in [
        (
            boundary - 1,
            "VX/V6",
            "VX/X6",
            "UNCHANGED_FROM_SUPPLIED_PAIR",
        ),
        (boundary, "VX/X6", "VX/Z6", "CHANGED_FROM_SUPPLIED_PAIR"),
        (boundary + 1, "VX/X6", "VX/Z6", "CHANGED_FROM_SUPPLIED_PAIR"),
    ] {
        b.request.cutoff_ms = cutoff;
        let out = b.run().unwrap();
        let p = &out["observation"]["report"]["futures"];
        assert_eq!(p["selected_contracts"][0]["symbol"], front);
        assert_eq!(p["selected_contracts"][1]["symbol"], next);
        assert_eq!(p["roll"], roll);
        assert_eq!(p["status"], "STALE");
        assert!(p["metrics"].is_null());
    }
}
#[test]
fn importer_preserves_missing_asynchronous_and_mismatched_calendar_failures() {
    for case in 0..3 {
        let mut b = Bundle::new();
        let f = b.request.futures.as_mut().unwrap();
        match case {
            0 => {
                f.quotes.remove(0);
            }
            1 => f.quotes[1].observed_ms -= 1,
            _ => {
                f.calendar.contracts.remove(2);
            }
        }
        let out = b.run().unwrap();
        let p = &out["observation"]["report"]["futures"];
        assert_eq!(p["status"], if case == 0 { "MISSING" } else { "INVALID" });
        assert!(p["metrics"].is_null());
    }
}
#[test]
fn manifest_decoding_is_strict_and_bounded() {
    let b = Bundle::new();
    for bytes in [
        br#"{"schema_version":"a","schema_version":"b"}"#.as_slice(),
        br#"{"unknown":1}"#,
    ] {
        assert!(evaluate(DEMO, bytes, &b.dir).is_err());
    }
    let mut b = Bundle::new();
    b.manifest["files"] = json!([]);
    assert_eq!(b.run().unwrap_err().code, ErrorCode::LimitExceeded);
}
#[test]
fn cli_emits_audit_json_and_no_output_on_admission_failure() {
    let b = Bundle::new();
    fs::write(
        b.dir.join("request.json"),
        serde_json::to_vec(&b.request).unwrap(),
    )
    .unwrap();
    fs::write(
        b.dir.join("manifest.json"),
        serde_json::to_vec(&b.manifest).unwrap(),
    )
    .unwrap();
    let run = || {
        Command::new(env!("CARGO_BIN_EXE_vix-snapshot"))
            .arg(b.dir.join("request.json"))
            .arg(b.dir.join("manifest.json"))
            .arg(&b.dir)
            .env("PATH", "/nonexistent")
            .output()
            .unwrap()
    };
    let output = run();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["schema_version"], "vix-snapshot-admission.rust.v1");
    fs::write(b.dir.join("raw.txt"), b"tampered").unwrap();
    let output = run();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
}
