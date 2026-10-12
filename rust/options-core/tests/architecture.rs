//! Direct source-dependency guard, not a Rust resolver or whole-program purity proof.
//! See docs/RUST_MODULE_BOUNDARIES.md for supported syntax and explicit exclusions.
use std::{collections::BTreeSet, fs, path::PathBuf, process::Command};

const FOUNDATION: &[&str] = &[
    "canonical",
    "codec",
    "contracts",
    "decimal",
    "error",
    "measurement",
];
const ANALYSIS: &[&str] = &[
    "constraints",
    "costs",
    "expressions",
    "inputs",
    "payoff_curve",
    "quote_diagnostics",
    "terminal_diagnostics",
];
const TRADING: &[&str] = &["intents", "reservations", "paper"];
const PURE_STD: &[&str] = &["collections", "fmt", "str", "error", "result"];

// Tokenize identifiers and punctuation, ignoring nested comments and literal contents.
// This avoids treating examples, documentation and strings as dependency edges.
fn tokens(source: &str) -> Vec<String> {
    let c: Vec<_> = source.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < c.len() {
        if c[i].is_whitespace() {
            i += 1;
            continue;
        }
        if c[i..].starts_with(&['/', '/']) {
            while i < c.len() && c[i] != '\n' {
                i += 1;
            }
            continue;
        }
        if c[i..].starts_with(&['/', '*']) {
            i += 2;
            let mut depth = 1;
            while depth > 0 {
                assert!(i < c.len(), "unterminated block comment");
                if c[i..].starts_with(&['/', '*']) {
                    depth += 1;
                    i += 2;
                } else if c[i..].starts_with(&['*', '/']) {
                    depth -= 1;
                    i += 2;
                } else {
                    i += 1;
                }
            }
            continue;
        }
        let mut q = i + usize::from(c[i] == 'r');
        while q < c.len() && c[q] == '#' {
            q += 1;
        }
        if c[i] == 'r' && q < c.len() && c[q] == '"' {
            let hashes = q - i - 1;
            i = q + 1;
            while !(i < c.len()
                && c[i] == '"'
                && c.get(i + 1..i + 1 + hashes)
                    .is_some_and(|s| s.iter().all(|x| *x == '#')))
            {
                assert!(i < c.len(), "unterminated raw string");
                i += 1;
            }
            i += 1 + hashes;
            continue;
        }
        if c[i] == '"' {
            i += 1;
            while i < c.len() && c[i] != '"' {
                i += if c[i] == '\\' { 2 } else { 1 };
            }
            assert!(i < c.len(), "unterminated string");
            i += 1;
            continue;
        }
        if c[i] == '\'' {
            if c.get(i + 2) == Some(&'\'') {
                i += 3;
                continue;
            }
            if c.get(i + 1) == Some(&'\\') && c.get(i + 3) == Some(&'\'') {
                i += 4;
                continue;
            }
        }
        if c[i].is_ascii_alphabetic() || c[i] == '_' {
            let start = i;
            while i < c.len() && (c[i].is_ascii_alphanumeric() || c[i] == '_') {
                i += 1;
            }
            out.push(c[start..i].iter().collect());
        } else if c[i..].starts_with(&[':', ':']) {
            out.push("::".into());
            i += 2;
        } else {
            out.push(c[i].to_string());
            i += 1;
        }
    }
    out
}
fn allowed(from: &str, to: &str) -> bool {
    FOUNDATION.contains(&to)
        || (ANALYSIS.contains(&from) && (ANALYSIS.contains(&to) || to == "fixtures"))
        || (TRADING.contains(&from) && TRADING.contains(&to))
        // Existing explicit mathematical fixtures construct both kinds of inputs.
        || (from == "fixtures" && (ANALYSIS.contains(&to) || TRADING.contains(&to)))
}
fn check(source: &str, module: &str) -> Result<(), String> {
    let t = tokens(source);
    let edge = |root: &str, target: &str| -> Result<(), String> {
        let ok = match root {
            "crate" => allowed(module, target),
            "std" | "core" | "alloc" => PURE_STD.contains(&target),
            _ => false,
        };
        if ok {
            Ok(())
        } else {
            Err(format!("{module} -> {root}::{target}"))
        }
    };
    for (i, token) in t.iter().enumerate() {
        if token == "extern" || token == "macro_rules" {
            return Err(format!("unsupported indirection: {token}"));
        }
        if token
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
            && t.get(i + 1).is_some_and(|s| s == "!")
            && t.get(i + 2)
                .is_some_and(|s| matches!(s.as_str(), "(" | "[" | "{"))
            && !["format", "vec", "matches", "write"].contains(&token.as_str())
        {
            return Err(format!("unreviewed macro: {token}!"));
        }
        if !["crate", "std", "core", "alloc", "super", "self"].contains(&token.as_str()) {
            continue;
        }
        if t.get(i + 1).is_some_and(|s| s == "::") {
            let first = t.get(i + 2).ok_or("incomplete path")?;
            if first != "{" {
                edge(token, first)?;
                continue;
            }
            let mut depth = 0;
            let mut next = true;
            for part in &t[i + 3..] {
                if depth == 0 && part == "}" {
                    break;
                }
                if depth == 0 && part == "," {
                    next = true;
                    continue;
                }
                if next {
                    edge(token, part)?;
                    next = false;
                }
                if part == "{" {
                    depth += 1;
                }
                if part == "}" {
                    depth -= 1;
                }
            }
        } else if i > 0 && t[i - 1] == "use" {
            return Err(format!("unqualified root alias: {token}"));
        }
    }
    Ok(())
}
#[test]
fn checked_core_source_edges_follow_the_four_logical_boundaries() {
    let src = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut found = BTreeSet::new();
    for entry in fs::read_dir(&src).unwrap() {
        let path = entry.unwrap().path();
        assert!(
            path.is_file(),
            "register and review nested source modules explicitly"
        );
        assert_eq!(path.extension().unwrap(), "rs");
        let name = path.file_stem().unwrap().to_str().unwrap();
        assert!(
            name == "lib"
                || name == "fixtures"
                || FOUNDATION.contains(&name)
                || ANALYSIS.contains(&name)
                || TRADING.contains(&name),
            "unclassified module {name}"
        );
        let source = fs::read_to_string(&path).unwrap();
        assert!(
            check(&source, name).is_ok(),
            "{}: {:?}",
            path.display(),
            check(&source, name)
        );
        found.insert(name.to_owned());
    }
    assert_eq!(
        found.len(),
        FOUNDATION.len() + ANALYSIS.len() + TRADING.len() + 2
    );
}
#[test]
fn source_guard_handles_groups_aliases_literals_and_forbidden_edges() {
    assert!(check("#![forbid(unsafe_code)]", "lib").is_ok());
    assert!(check("use crate::{contracts::*, measurement::{legal_quantity, tick}}; use std::{collections::BTreeSet, fmt};", "intents").is_ok());
    assert!(check("/* std::fs /* nested */ */ let x = r###\"crate::inputs std::net\"###; let y = \"std::fs\";", "intents").is_ok());
    for bad in [
        "use crate::inputs as old;",
        "crate::costs::quote_cost(q);",
        "use std::{fmt, fs::File};",
        "use std::net as sockets;",
        "std::time::SystemTime::now();",
        "use std as host;",
        "super::inputs::validate_spec(s);",
        "include!(\"hidden.rs\");",
        "println!(\"external write\");",
        "extern crate std as host;",
    ] {
        assert!(check(bad, "intents").is_err(), "missed {bad}");
    }
    assert!(check("use crate::reservations::Authority;", "quote_diagnostics").is_err());
    assert!(check("use crate::intents::Intent;", "measurement").is_err());
}
#[test]
fn workspace_stays_two_crates_with_only_reviewed_pure_core_dependencies() {
    let output = Command::new(env!("CARGO"))
        .args([
            "metadata",
            "--no-deps",
            "--format-version",
            "1",
            "--locked",
            "--offline",
        ])
        .current_dir(env!("CARGO_MANIFEST_DIR"))
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let meta: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(meta["workspace_members"].as_array().unwrap().len(), 2);
    let core = meta["packages"]
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["name"] == "options-core")
        .unwrap();
    assert!(core["targets"].as_array().unwrap().iter().all(|t| {
        !t["kind"]
            .as_array()
            .unwrap()
            .iter()
            .any(|k| k == "custom-build")
    }));
    let deps: BTreeSet<_> = core["dependencies"]
        .as_array()
        .unwrap()
        .iter()
        .map(|d| d["name"].as_str().unwrap())
        .collect();
    assert_eq!(
        deps,
        BTreeSet::from([
            "serde",
            "serde_json",
            "sha2",
            "num-bigint",
            "num-rational",
            "num-traits"
        ])
    );
}
