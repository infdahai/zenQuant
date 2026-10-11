use options_core::{canonical::canonical_hash, quote_diagnostics};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Output},
    sync::atomic::{AtomicUsize, Ordering},
};

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let p = fs::canonicalize(std::env::temp_dir())
            .unwrap()
            .join(format!(
                "options-quote-cli-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
    fn join(&self, p: &str) -> PathBuf {
        self.0.join(p)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
fn fixture() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures/quote-costs.json")
}
fn command() -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_options-workbench"));
    // The runtime must work with no Python, shell or other executable on PATH.
    command.env("PATH", "/nonexistent");
    command
}
fn run(request: &Path, out: &Path) -> Output {
    command()
        .arg("--quote-request-file")
        .arg(request)
        .arg("--output-dir")
        .arg(out)
        .output()
        .unwrap()
}
fn json_file(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

#[test]
fn view_free_fixture_is_deterministic_exact_and_has_no_authority() {
    let temp = Temp::new();
    let a = temp.join("a");
    let b = temp.join("b");
    assert!(run(&fixture(), &a).status.success());
    assert!(run(&fixture(), &b).status.success());
    for file in ["report.json", "REPORT.md", "identity.json"] {
        assert_eq!(
            fs::read(a.join(file)).unwrap(),
            fs::read(b.join(file)).unwrap()
        );
    }
    let report = json_file(&a.join("report.json"));
    assert_eq!(report["schema_version"], quote_diagnostics::REPORT_SCHEMA);
    assert_eq!(report["execution_authority"], "NONE");
    assert_eq!(report["diagnostic_only"], true);
    assert_eq!(report["rankable"], false);
    assert_eq!(report["live_eligible"], false);
    for key in [
        "actual_pnl",
        "paper_pnl",
        "sharpe",
        "capacity",
        "lifecycle_arbitrage_edge",
    ] {
        assert_eq!(report[key], Value::Null);
    }
    for key in [
        "view",
        "views",
        "scenarios",
        "risk_policy",
        "hypothetical_balances",
    ] {
        assert!(report["input"].get(key).is_none());
    }
    assert_eq!(
        report["quote_diagnostics"][0]["cost"]["total_report_currency"],
        "0.0203"
    );
    assert_eq!(
        report["quote_diagnostics"][1]["cost"]["total_report_currency"],
        "0.0187"
    );
    for diagnostic in report["quote_diagnostics"].as_array().unwrap() {
        assert_eq!(diagnostic["account_eligibility"]["status"], "UNVERIFIED");
        assert_eq!(diagnostic["execution_evidence"]["status"], "UNVERIFIED");
    }
    assert_eq!(
        report["identity"]["raw_source_integrity"],
        "NOT_INDEPENDENTLY_VERIFIED"
    );
    assert_eq!(report["identity"]["projection_trust"], "MANUAL_UNATTESTED");
    assert_eq!(report["identity"]["verified_raw_sources"], json!([]));
}

#[test]
fn invalid_input_and_incompatible_flags_create_no_output_parents() {
    let temp = Temp::new();
    let path = temp.join("request");
    let output = temp.join("missing/out");
    let mut invalid = json_file(&fixture());
    invalid["views"] = json!([]);
    for bytes in [
        serde_json::to_vec(&invalid).unwrap(),
        b"{}".to_vec(),
        vec![b' '; 1048577],
    ] {
        fs::write(&path, bytes).unwrap();
        let result = run(&path, &output);
        assert_eq!(result.status.code(), Some(2));
        assert_eq!(result.stderr, b"INVALID_INPUT\n");
        assert!(!temp.join("missing").exists());
    }
    for flag in ["--request-file", "--quote-request-file", "--panel-file"] {
        let result = command()
            .arg("--quote-request-file")
            .arg(fixture())
            .arg(flag)
            .arg(fixture())
            .arg("--output-dir")
            .arg(&output)
            .output()
            .unwrap();
        assert_eq!(result.status.code(), Some(2));
        assert!(!temp.join("missing").exists());
    }
    let old = fixture().with_file_name("terminal-eth-call.json");
    assert_eq!(run(&old, &output).status.code(), Some(2));
    let result = command()
        .arg("--request-file")
        .arg(fixture())
        .arg("--output-dir")
        .arg(&output)
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(2));
    assert!(!temp.join("missing").exists());
}

#[test]
fn missing_evidence_is_reported_and_public_render_revalidates_typed_input() {
    let temp = Temp::new();
    let bytes = fs::read(fixture()).unwrap();
    let mut request = quote_diagnostics::decode_request(&bytes).unwrap();
    request.quotes[0].fee_known_ns = Some(request.cutoff_ns + 1);
    let path = temp.join("request");
    fs::write(&path, serde_json::to_vec(&request).unwrap()).unwrap();
    assert!(run(&path, &temp.join("out")).status.success());
    let report = json_file(&temp.join("out/report.json"));
    assert_eq!(
        report["quote_diagnostics"][0]["status"]["status"],
        "NOT_EVALUABLE"
    );
    assert_eq!(
        report["quote_diagnostics"][0]["cost"]["total_report_currency"],
        Value::Null
    );
    request.requests[0].leg.instrument.venue = "tampered".into();
    assert!(options_workbench::report::render_quote_diagnostics(&request, &bytes).is_err());
}

#[test]
fn full_future_evidence_is_hashed_and_markup_is_escaped() {
    let temp = Temp::new();
    let mut request = quote_diagnostics::decode_request(&fs::read(fixture()).unwrap()).unwrap();
    let mut future = request.quotes[0].clone();
    future.quote_id = "future<script>\u{1b}".into();
    future.book.received_ns = request.cutoff_ns + 1;
    future.book.available_ns = request.cutoff_ns + 1;
    request.quotes.push(future);
    let path = temp.join("request");
    let bytes = serde_json::to_vec(&request).unwrap();
    fs::write(&path, &bytes).unwrap();
    assert!(run(&path, &temp.join("a")).status.success());
    let a = json_file(&temp.join("a/report.json"));
    assert_eq!(a["input"]["quotes"].as_array().unwrap().len(), 2);
    assert_eq!(
        a["identity"]["canonical_input_sha256"],
        canonical_hash(&request).unwrap()
    );
    let md = fs::read_to_string(temp.join("a/REPORT.md")).unwrap();
    assert!(!md.contains("<script>"));
    assert!(md.contains("&lt;script&gt;"));
    assert!(!md.contains('\u{1b}'));
    let mut spaced = bytes;
    spaced.push(b'\n');
    fs::write(&path, spaced).unwrap();
    assert!(run(&path, &temp.join("b")).status.success());
    let b = json_file(&temp.join("b/report.json"));
    assert_ne!(
        a["identity"]["request_bytes_sha256"],
        b["identity"]["request_bytes_sha256"]
    );
    assert_eq!(
        a["identity"]["canonical_input_sha256"],
        b["identity"]["canonical_input_sha256"]
    );
    request.quotes[1].book.asks[0].price = options_core::decimal::Dec::parse("3").unwrap();
    fs::write(&path, serde_json::to_vec(&request).unwrap()).unwrap();
    assert!(run(&path, &temp.join("c")).status.success());
    let c = json_file(&temp.join("c/report.json"));
    assert_ne!(
        a["identity"]["canonical_input_sha256"],
        c["identity"]["canonical_input_sha256"]
    );
    assert_ne!(
        a["quote_diagnostics"][0]["input_hash"],
        c["quote_diagnostics"][0]["input_hash"]
    );
    assert_eq!(
        a["quote_diagnostics"][0]["cost"],
        c["quote_diagnostics"][0]["cost"]
    );
    assert_eq!(
        a["quote_diagnostics"][0]["selected_quote_hash"],
        c["quote_diagnostics"][0]["selected_quote_hash"]
    );
}

#[test]
fn quote_mode_preserves_publication_and_sealed_root_protections() {
    use std::os::unix::fs::{PermissionsExt, symlink};
    let temp = Temp::new();
    let output = temp.join("out");
    assert!(run(&fixture(), &output).status.success());
    let original = fs::read(output.join("report.json")).unwrap();
    assert_eq!(run(&fixture(), &output).status.code(), Some(2));
    assert_eq!(fs::read(output.join("report.json")).unwrap(), original);
    symlink(&temp.0, temp.join("link")).unwrap();
    for out in [
        temp.join("link/new"),
        temp.join("out/../new"),
        fixture().join("out"),
    ] {
        assert_eq!(run(&fixture(), &out).status.code(), Some(2));
    }
    assert!(!temp.join("new").exists());
    for root in options_workbench::io::trusted_roots().unwrap() {
        let out = root.join("quote-must-not-create");
        assert_eq!(run(&fixture(), &out).status.code(), Some(2));
        assert!(!out.exists());
    }
    let parent = temp.join("readonly");
    fs::create_dir(&parent).unwrap();
    fs::set_permissions(&parent, fs::Permissions::from_mode(0o500)).unwrap();
    let result = run(&fixture(), &parent.join("report"));
    fs::set_permissions(&parent, fs::Permissions::from_mode(0o700)).unwrap();
    assert_eq!(result.status.code(), Some(1));
    assert_eq!(result.stderr, b"INCOMPLETE_PUBLICATION\n");
    assert!(!parent.join("report").exists());
}
