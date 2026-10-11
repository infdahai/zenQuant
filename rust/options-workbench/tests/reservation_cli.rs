#![cfg(target_os = "linux")]
use std::process::Command;
#[test]
fn production_cli_has_no_authority_clock_release_or_cancel_override() {
    let binary = env!("CARGO_BIN_EXE_simulated-reservations");
    let help = Command::new(binary)
        .env("PATH", "/nonexistent")
        .arg("--help")
        .output()
        .unwrap();
    assert!(help.status.success());
    assert!(
        String::from_utf8(help.stdout)
            .unwrap()
            .contains("One repository simulation authority")
    );
    for args in [
        vec!["init", "--store-dir", "/unused"],
        vec!["reserve", "--authority-dir", "/unused"],
        vec!["reserve", "--evaluation-ns", "10"],
        vec!["reserve", "--request-file", "x", "--clock", "10"],
        vec!["release", "--request-id", "one"],
        vec!["cancel", "--request-id", "one"],
        vec!["replay", "--store-dir", "/unused"],
    ] {
        let out = Command::new(binary)
            .env("PATH", "/nonexistent")
            .args(args)
            .output()
            .unwrap();
        assert_eq!(out.status.code(), Some(2));
        assert_eq!(out.stderr, b"INVALID_RESERVATION_INPUT\n");
    }
    assert_eq!(
        options_workbench::reservation_store::authority_path().unwrap(),
        options_workbench::io::repository_root()
            .unwrap()
            .join(".simulated-reservations")
    );
}
