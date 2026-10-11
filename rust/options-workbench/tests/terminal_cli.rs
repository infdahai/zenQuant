use options_core::terminal_diagnostics;
use serde_json::Value;
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
        let path = fs::canonicalize(std::env::temp_dir())
            .unwrap()
            .join(format!(
                "terminal-cli-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn join(&self, path: &str) -> PathBuf {
        self.0.join(path)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!("fixtures/{name}.json"))
}
fn command() -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_options-workbench"));
    cmd.env("PATH", "/nonexistent");
    cmd
}
fn run(request: &Path, output: &Path) -> Output {
    command()
        .arg("--terminal-request-file")
        .arg(request)
        .arg("--output-dir")
        .arg(output)
        .output()
        .unwrap()
}
fn read(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}
fn request() -> Value {
    read(&fixture("terminal-call-scenarios"))
}
fn write(path: &Path, value: &Value) {
    fs::write(path, serde_json::to_vec(value).unwrap()).unwrap();
}

#[test]
fn terminal_fixture_is_exact_deterministic_and_without_funding_or_authority() {
    let p = Temp::new();
    for dir in ["a", "b"] {
        assert!(
            run(&fixture("terminal-call-scenarios"), &p.join(dir))
                .status
                .success()
        );
    }
    for file in ["report.json", "identity.json", "REPORT.md"] {
        assert_eq!(
            fs::read(p.join(&format!("a/{file}"))).unwrap(),
            fs::read(p.join(&format!("b/{file}"))).unwrap()
        );
    }
    let v = read(&p.join("a/report.json"));
    assert_eq!(v["schema_version"], terminal_diagnostics::REPORT_SCHEMA);
    assert_eq!(v["diagnostic_only"], true);
    assert_eq!(v["rankable"], false);
    assert_eq!(v["live_eligible"], false);
    assert_eq!(v["execution_authority"], "NONE");
    for key in [
        "actual_pnl",
        "paper_pnl",
        "sharpe",
        "capacity",
        "lifecycle_arbitrage_edge",
    ] {
        assert_eq!(v[key], Value::Null);
    }
    for key in ["view", "views", "hypothetical_balances", "risk_policy"] {
        assert!(v["input"].get(key).is_none());
    }
    let d = &v["terminal_diagnostic"];
    assert_eq!(d["outcomes"][3]["gross_payoff"], "0.1");
    assert_eq!(d["outcomes"][3]["conditional_model_pnl"], "0.0787");
    assert_eq!(
        d["support"]["modeled_feasibility"]["status"],
        "NOT_EVALUABLE"
    );
    assert_eq!(
        d["support"]["modeled_feasibility"]["reason"],
        "FUNDING_NOT_ASSESSED"
    );
    assert_eq!(d["support"]["account_eligibility"]["status"], "UNVERIFIED");
    assert_eq!(d["support"]["execution_evidence"]["status"], "UNVERIFIED");
    assert_eq!(
        v["identity"]["raw_source_integrity"],
        "NOT_INDEPENDENTLY_VERIFIED"
    );
    let md = fs::read_to_string(p.join("a/REPORT.md")).unwrap();
    assert!(md.contains("not an actual fill") || md.contains("neither is an actual fill"));
    assert!(md.contains("Funding feasibility"));
}

#[test]
fn quote_dependency_ablation_and_unsupported_expiry_survive_actual_cli() {
    let p = Temp::new();
    let path = p.join("request");
    let mut r = request();
    r["quotes"] = serde_json::json!([]);
    write(&path, &r);
    assert!(run(&path, &p.join("gross")).status.success());
    let v = read(&p.join("gross/report.json"));
    assert_eq!(
        v["terminal_diagnostic"]["outcomes"][3]["gross_payoff"],
        "0.1"
    );
    assert_eq!(
        v["terminal_diagnostic"]["outcomes"][3]["conditional_model_pnl"],
        Value::Null
    );
    assert_eq!(
        v["terminal_diagnostic"]["net_status"]["status"],
        "NOT_EVALUABLE"
    );
    r["scenarios"]["scenarios"][0]["at_ns"] = 999.into();
    write(&path, &r);
    assert!(run(&path, &p.join("unsupported")).status.success());
    let v = read(&p.join("unsupported/report.json"));
    assert_eq!(
        v["terminal_diagnostic"]["support"]["model_support"]["status"],
        "UNSUPPORTED"
    );
    for o in v["terminal_diagnostic"]["outcomes"].as_array().unwrap() {
        assert_eq!(o["gross_payoff"], Value::Null);
    }
}

#[test]
fn modes_are_mutually_exclusive_in_both_orders_and_do_not_cross_admit() {
    let p = Temp::new();
    let out = p.join("missing/report");
    let terminal = fixture("terminal-call-scenarios");
    for flag in [
        "--request-file",
        "--quote-request-file",
        "--panel-file",
        "--terminal-request-file",
    ] {
        for reversed in [false, true] {
            let flags = if reversed {
                [flag, "--terminal-request-file"]
            } else {
                ["--terminal-request-file", flag]
            };
            let o = command()
                .arg(flags[0])
                .arg(&terminal)
                .arg(flags[1])
                .arg(&terminal)
                .arg("--output-dir")
                .arg(&out)
                .output()
                .unwrap();
            assert_eq!(o.status.code(), Some(2));
            assert_eq!(o.stderr, b"INVALID_INPUT\n");
        }
    }
    for (flag, file) in [
        ("--request-file", "terminal-eth-call"),
        ("--quote-request-file", "quote-costs"),
    ] {
        assert_eq!(run(&fixture(file), &out).status.code(), Some(2));
        let o = command()
            .arg(flag)
            .arg(&terminal)
            .arg("--output-dir")
            .arg(&out)
            .output()
            .unwrap();
        assert_eq!(o.status.code(), Some(2));
    }
    assert!(!p.join("missing").exists());
}

#[test]
fn malformed_and_tampered_inputs_fail_before_publication() {
    let p = Temp::new();
    let path = p.join("request");
    let out = p.join("missing/report");
    for variant in 0..4 {
        let mut r = request();
        match variant {
            0 => r["views"] = serde_json::json!([]),
            1 => r["scenarios"]["spec_hash"] = "0".repeat(64).into(),
            2 => r["scenarios"]["meta"]["available_ns"] = 11.into(),
            _ => r["scenarios"]["scenarios"][0]["delivery_price"] = 1.0.into(),
        }
        write(&path, &r);
        assert_eq!(run(&path, &out).status.code(), Some(2));
        assert!(!p.join("missing").exists());
    }
    let bytes = fs::read(fixture("terminal-call-scenarios")).unwrap();
    let mut typed = terminal_diagnostics::decode_request(&bytes).unwrap();
    typed.scenarios.spec_hash = "0".repeat(64);
    assert!(options_workbench::report::render_terminal_diagnostics(&typed, &bytes).is_err());
}

#[test]
fn terminal_report_retains_evidence_identity_markup_and_existing_outputs() {
    use std::os::unix::fs::symlink;
    let p = Temp::new();
    let path = p.join("request");
    let mut r = request();
    r["scenarios"]["meta"]["source_ref"] = "<script>\u{1b}".into();
    write(&path, &r);
    assert!(run(&path, &p.join("a")).status.success());
    let a = read(&p.join("a/report.json"));
    let original: Vec<_> = ["report.json", "REPORT.md", "identity.json"]
        .iter()
        .map(|f| fs::read(p.join(&format!("a/{f}"))).unwrap())
        .collect();
    assert_eq!(run(&path, &p.join("a")).status.code(), Some(2));
    for (f, bytes) in ["report.json", "REPORT.md", "identity.json"]
        .iter()
        .zip(original)
    {
        assert_eq!(fs::read(p.join(&format!("a/{f}"))).unwrap(), bytes);
    }
    let md = fs::read_to_string(p.join("a/REPORT.md")).unwrap();
    assert!(!md.contains("<script>"));
    assert!(md.contains("&lt;script&gt;"));
    assert!(!md.contains('\u{1b}'));
    let mut bytes = fs::read(&path).unwrap();
    bytes.push(b'\n');
    fs::write(&path, bytes).unwrap();
    assert!(run(&path, &p.join("b")).status.success());
    let b = read(&p.join("b/report.json"));
    assert_eq!(
        a["identity"]["canonical_input_sha256"],
        b["identity"]["canonical_input_sha256"]
    );
    assert_ne!(
        a["identity"]["request_bytes_sha256"],
        b["identity"]["request_bytes_sha256"]
    );
    symlink(&p.0, p.join("link")).unwrap();
    assert_eq!(run(&path, &p.join("link/out")).status.code(), Some(2));
    assert_eq!(run(&path, &p.join("a/../out")).status.code(), Some(2));
    for root in options_workbench::io::trusted_roots().unwrap() {
        let output = root.join("terminal-must-not-create");
        assert_eq!(run(&path, &output).status.code(), Some(2));
        assert!(!output.exists());
    }
}
