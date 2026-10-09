use std::{
    fs,
    path::PathBuf,
    process::Command,
    sync::atomic::{AtomicUsize, Ordering},
};
fn temp() -> PathBuf {
    static N: AtomicUsize = AtomicUsize::new(0);
    let p = fs::canonicalize(std::env::temp_dir())
        .unwrap()
        .join(format!(
            "options-cli-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
    fs::create_dir(&p).unwrap();
    p
}
fn run(request: &std::path::Path, output: &std::path::Path) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_options-workbench"))
        .args(["--request-file"])
        .arg(request)
        .arg("--output-dir")
        .arg(output)
        .output()
        .unwrap()
}
#[test]
fn invalid_request_creates_no_directory() {
    let p = temp();
    let r = p.join("request.json");
    fs::write(&r, b"{}").unwrap();
    let out = p.join("missing/report");
    let result = run(&r, &out);
    assert_eq!(result.status.code(), Some(2));
    assert!(!p.join("missing").exists());
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn fixture_is_deterministic_and_has_no_authority() {
    let p = temp();
    let request = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures/terminal-eth-call.json");
    let a = p.join("a");
    let b = p.join("b");
    assert!(run(&request, &a).status.success());
    assert!(run(&request, &b).status.success());
    for f in ["report.json", "REPORT.md", "identity.json"] {
        assert_eq!(fs::read(a.join(f)).unwrap(), fs::read(b.join(f)).unwrap());
    }
    let v: serde_json::Value =
        serde_json::from_slice(&fs::read(a.join("report.json")).unwrap()).unwrap();
    assert_eq!(v["execution_authority"], "NONE");
    assert_eq!(v["rankable"], false);
    assert!(v["actual_pnl"].is_null());
    fs::remove_dir_all(p).unwrap();
}
fn fixture() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures/terminal-eth-call.json")
}
#[test]
fn existing_target_and_source_overlap_are_rejected() {
    let p = temp();
    let out = p.join("existing");
    fs::create_dir(&out).unwrap();
    fs::write(out.join("keep"), b"unchanged").unwrap();
    assert_eq!(run(&fixture(), &out).status.code(), Some(2));
    assert_eq!(fs::read(out.join("keep")).unwrap(), b"unchanged");
    let r = p.join("request");
    fs::write(&r, fs::read(fixture()).unwrap()).unwrap();
    assert_eq!(run(&r, &r.join("out")).status.code(), Some(2));
    assert_eq!(run(&r, &p).status.code(), Some(2));
    let root = options_workbench::io::trusted_roots().unwrap().remove(0);
    assert_eq!(
        run(&fixture(), &root.join("task4-must-not-create"))
            .status
            .code(),
        Some(2)
    );
    assert!(!root.join("task4-must-not-create").exists());
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn symlink_and_dangling_target_are_rejected() {
    use std::os::unix::fs::symlink;
    let p = temp();
    let real = p.join("real");
    fs::create_dir(&real).unwrap();
    let link = p.join("link");
    symlink(&real, &link).unwrap();
    assert_eq!(
        run(&fixture(), &link.join("missing/out")).status.code(),
        Some(2)
    );
    assert!(!real.join("missing").exists());
    let dangling = p.join("dangling");
    symlink(p.join("absent"), &dangling).unwrap();
    assert_eq!(run(&fixture(), &dangling).status.code(), Some(2));
    let req = p.join("request-link");
    symlink(fixture(), &req).unwrap();
    assert_eq!(run(&req, &p.join("out")).status.code(), Some(2));
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn exact_flags_and_bounded_input_errors_never_echo_paths() {
    let p = temp();
    let r = p.join("<script>\u{1b}request");
    fs::write(&r, vec![b' '; 1024 * 1024 + 1]).unwrap();
    let o = run(&r, &p.join("missing/out"));
    assert_eq!(o.status.code(), Some(2));
    assert_eq!(o.stderr, b"INVALID_INPUT\n");
    assert!(!p.join("missing").exists());
    for args in [
        vec!["--live"],
        vec!["--request-file"],
        vec!["--help", "--live"],
        vec!["--request-file", "a", "--request-file", "b"],
    ] {
        let o = Command::new(env!("CARGO_BIN_EXE_options-workbench"))
            .args(args)
            .output()
            .unwrap();
        assert_eq!(o.status.code(), Some(2));
    }
    let o = Command::new(env!("CARGO_BIN_EXE_options-workbench"))
        .arg("--help")
        .output()
        .unwrap();
    assert!(o.status.success());
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn actual_cli_racing_target_has_one_winner_and_preserves_complete_artifacts() {
    let p = temp();
    let out = p.join("race");
    let spawn = || {
        Command::new(env!("CARGO_BIN_EXE_options-workbench"))
            .arg("--request-file")
            .arg(fixture())
            .arg("--output-dir")
            .arg(&out)
            .spawn()
            .unwrap()
    };
    let mut a = spawn();
    let mut b = spawn();
    let statuses = [a.wait().unwrap(), b.wait().unwrap()];
    assert_eq!(statuses.iter().filter(|s| s.success()).count(), 1);
    let v: serde_json::Value =
        serde_json::from_slice(&fs::read(out.join("report.json")).unwrap()).unwrap();
    assert_eq!(v["execution_authority"], "NONE");
    assert!(out.join("identity.json").is_file());
    assert!(out.join("REPORT.md").is_file());
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn full_timeline_hypotheses_and_escaped_markup_survive() {
    use options_core::{canonical::canonical_hash, inputs::decode_request};
    let p = temp();
    let mut request = decode_request(&fs::read(fixture()).unwrap()).unwrap();
    request.views[0].competing_explanations = vec!["<script>alert(1)</script>\u{1b}".into()];
    request.scenarios.view_hash = canonical_hash(&request.views[0]).unwrap();
    let mut revision = request.views[0].clone();
    revision.meta.revision = 2;
    revision.meta.parent_hash = Some(canonical_hash(&request.views[0]).unwrap());
    revision.meta.available_ns = 20;
    revision.meta.received_ns = 20;
    revision.meta.cutoff_ns = 20;
    request.views.push(revision);
    let r = p.join("request");
    fs::write(&r, serde_json::to_vec(&request).unwrap()).unwrap();
    let out = p.join("out");
    assert!(run(&r, &out).status.success());
    let report: serde_json::Value =
        serde_json::from_slice(&fs::read(out.join("report.json")).unwrap()).unwrap();
    assert_eq!(report["input"]["view_history"].as_array().unwrap().len(), 2);
    assert_eq!(report["input"]["view"]["meta"]["revision"], 1);
    let md = fs::read_to_string(out.join("REPORT.md")).unwrap();
    assert!(!md.contains("<script>"));
    assert!(md.contains("&lt;script&gt;"));
    assert!(!md.contains('\u{1b}'));
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn request_bytes_identity_is_separate_from_canonical_input() {
    let p = temp();
    let r = p.join("request");
    let bytes = fs::read(fixture()).unwrap();
    fs::write(&r, &bytes).unwrap();
    let a = p.join("a");
    assert!(run(&r, &a).status.success());
    let mut extended = bytes;
    extended.push(b'\n');
    fs::write(&r, &extended).unwrap();
    let b = p.join("b");
    assert!(run(&r, &b).status.success());
    let identity = |dir: &std::path::Path| {
        serde_json::from_slice::<serde_json::Value>(&fs::read(dir.join("identity.json")).unwrap())
            .unwrap()
    };
    let a = identity(&a);
    let b = identity(&b);
    assert_ne!(a["request_bytes_sha256"], b["request_bytes_sha256"]);
    assert_eq!(a["canonical_input_sha256"], b["canonical_input_sha256"]);
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn actual_cli_write_protection_returns_internal_exit() {
    use std::os::unix::fs::PermissionsExt;
    let p = temp();
    let parent = p.join("readonly");
    fs::create_dir(&parent).unwrap();
    fs::set_permissions(&parent, fs::Permissions::from_mode(0o500)).unwrap();
    let o = run(&fixture(), &parent.join("report"));
    fs::set_permissions(&parent, fs::Permissions::from_mode(0o700)).unwrap();
    assert_eq!(o.status.code(), Some(1));
    assert_eq!(o.stderr, b"INCOMPLETE_PUBLICATION\n");
    assert!(!parent.join("report").exists());
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn all_explicit_mathematical_fixtures_keep_null_performance() {
    let p = temp();
    for name in [
        "terminal-eth-call",
        "terminal-eth-straddle",
        "terminal-vertical",
    ] {
        let request =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!("fixtures/{name}.json"));
        let out = p.join(name);
        assert!(run(&request, &out).status.success());
        let v: serde_json::Value =
            serde_json::from_slice(&fs::read(out.join("report.json")).unwrap()).unwrap();
        for field in [
            "actual_pnl",
            "paper_pnl",
            "sharpe",
            "capacity",
            "lifecycle_arbitrage_edge",
        ] {
            assert!(v[field].is_null())
        }
        assert_eq!(v["diagnostic_only"], true);
        assert_eq!(v["rankable"], false);
        assert_eq!(v["live_eligible"], false);
        assert_eq!(
            v["expressions"][0]["support"]["account_eligibility"]["status"],
            "UNVERIFIED"
        );
        assert_eq!(
            v["expressions"][0]["support"]["execution_evidence"]["status"],
            "UNVERIFIED"
        );
        assert!(v["input"]["view"]["meta"]["parent_hash"].is_null());
    }
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn candidate_heading_cannot_escape_into_markdown_image_or_link() {
    let p = temp();
    let mut request = options_core::inputs::decode_request(&fs::read(fixture()).unwrap()).unwrap();
    request.candidates[0].request_id="safe\n\n![remote](https://example.invalid/pixel)\n\n[remote](https://example.invalid/link)\n\n".into();
    let r = p.join("request");
    fs::write(&r, serde_json::to_vec(&request).unwrap()).unwrap();
    let out = p.join("out");
    assert!(run(&r, &out).status.success());
    let md = fs::read_to_string(out.join("REPORT.md")).unwrap();
    let heading = md
        .split("<h2>Candidate: ")
        .nth(1)
        .unwrap()
        .split("</h2>")
        .next()
        .unwrap();
    assert!(!heading.contains('\n'));
    assert!(heading.contains("&#10;"));
    assert!(!md.contains("\n\n![remote]"));
    assert!(!md.contains("\n\n[remote]"));
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn original_parent_traversal_paths_are_rejected_before_io_or_output_creation() {
    use std::os::unix::fs::symlink;
    let p = temp();
    fs::create_dir_all(p.join("elsewhere/deep")).unwrap();
    symlink(p.join("elsewhere/deep"), p.join("link")).unwrap();
    let r = p.join("request");
    fs::write(&r, fs::read(fixture()).unwrap()).unwrap();
    for out in [
        p.join("link/../unexpected"),
        p.join("elsewhere/../unexpected"),
    ] {
        assert_eq!(run(&r, &out).status.code(), Some(2));
        assert!(!p.join("unexpected").exists());
        assert!(!p.join("elsewhere/unexpected").exists());
    }
    for req in [p.join("link/../request"), p.join("elsewhere/../request")] {
        assert_eq!(run(&req, &p.join("missing/out")).status.code(), Some(2));
        assert!(!p.join("missing").exists());
    }
    assert_eq!(
        options_workbench::publication::read_bounded(&p.join("link/../request"), 1024 * 1024),
        Err(options_workbench::publication::Failure::Invalid)
    );
    let badpanel = p.join("elsewhere/../panel");
    fs::write(p.join("panel"), b"{}").unwrap();
    let o = Command::new(env!("CARGO_BIN_EXE_options-workbench"))
        .arg("--request-file")
        .arg(&r)
        .arg("--output-dir")
        .arg(p.join("missing/out"))
        .arg("--panel-file")
        .arg(badpanel)
        .output()
        .unwrap();
    assert_eq!(o.status.code(), Some(2));
    assert!(!p.join("missing").exists());
    assert_eq!(
        options_workbench::publication::read_bounded(&p.join("elsewhere/../request"), 1024 * 1024),
        Err(options_workbench::publication::Failure::Invalid)
    );
    assert!(options_workbench::io::validate_output(&p.join("link/../unexpected"), &[]).is_err());
    fs::remove_dir_all(p).unwrap();
}

#[test]
fn cash_request_reaches_actual_cli_report() {
    let p = temp();
    let mut r = options_core::inputs::decode_request(&fs::read(fixture()).unwrap()).unwrap();
    r.fixture_name = None;
    r.candidates[0].template_id = "cash".into();
    r.candidates[0].legs.clear();
    let path = p.join("cash.json");
    fs::write(&path, serde_json::to_vec(&r).unwrap()).unwrap();
    let out = p.join("out");
    assert_eq!(run(&path, &out).status.code(), Some(0));
    let report: serde_json::Value =
        serde_json::from_slice(&fs::read(out.join("report.json")).unwrap()).unwrap();
    assert_eq!(
        report["expressions"][0]["support"]["model_support"]["status"],
        "VALID"
    );
    assert_eq!(
        report["expressions"][0]["outcomes"][0]["conditional_model_pnl"],
        "0"
    );
    assert_eq!(report["execution_authority"], "NONE");
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn report_public_boundary_rejects_invalid_analysis_before_render() {
    use options_core::{
        canonical::canonical_hash,
        inputs::{decode_request, load_analysis},
    };
    let bytes = fs::read(fixture()).unwrap();
    let base = load_analysis(decode_request(&bytes).unwrap()).unwrap();
    for variant in 0..5 {
        let mut a = base.clone();
        match variant {
            0 => a.scenarios.view_hash = "0".repeat(64),
            1 => {
                a.scenarios.meta.received_ns = a.cutoff_ns + 1;
                a.scenarios.meta.available_ns = a.cutoff_ns + 1;
                a.scenarios.meta.cutoff_ns = a.cutoff_ns + 1;
            }
            2 => {
                a.view.mechanism = "different history".into();
                a.scenarios.view_hash = canonical_hash(&a.view).unwrap();
            }
            3 => a.scenarios.scenarios.clear(),
            _ => a.view_history.clear(),
        }
        assert!(options_workbench::report::render(&a, &bytes, None).is_err());
    }
}
