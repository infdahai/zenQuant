"""Offline diagnostics have no permissions or writable source paths."""

import hashlib
import importlib.util
import json
import socket
import urllib.request
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("options_cli", Path(__file__).resolve().parents[2] / "scripts/analyze_options_portfolio.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def read_report(out):
    return json.loads((out / "report.json").read_text())


@pytest.mark.parametrize("capital,scenario,delta", [("1000", "long-call", "0.27"), ("3000", "straddle", "0.14")])
def test_cli_never_promotes_math_scenario(tmp_path, monkeypatch, capital, scenario, delta):
    def forbidden(*args, **kwargs):
        raise AssertionError("network or account environment access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(cli.os, "getenv", forbidden)
    out = tmp_path / "report"
    assert cli.main(["--output-dir", str(out), "--capital", capital, "--scenario", scenario]) == 0
    data = read_report(out)
    assert data["diagnostic_only"] is True
    assert data["rankable"] is data["live_eligible"] is data["execution_authority"] is False
    assert all(data[key] is None for key in ("actual_pnl", "paper_pnl", "sharpe", "capacity"))
    assert data["provenance"] == "fixture"
    assert data["capital"] == capital
    assert data["math_scenarios"][0]["policy_enforced"] is False
    assert data["math_scenarios"][0]["wallet_cash_delta"][0]["delta"] == delta
    assert data["model_preflight"]["execution_authority"] == "NONE"
    assert data["model_preflight"]["status"]["status"] == "VALID"
    assert data["manual_inputs"]["currency"] == "USDT"
    assert "fixture" in (out / "REPORT.md").read_text().splitlines()[0]
    assert set(p.name for p in out.iterdir()) == {"report.json", "REPORT.md", "identity.json"}
    identity = json.loads((out / "identity.json").read_text())
    for name, entry in identity["artifacts"].items():
        raw = (out / name).read_bytes()
        assert entry == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    assert identity["inputs"] == data["identity"]
    assert not list(tmp_path.glob(".report.incomplete-*"))


@pytest.mark.parametrize("argument", ["--live", "--exchange", "--account", "--signal"])
def test_unapproved_options_rejected_before_creation(tmp_path, capsys, argument):
    out = tmp_path / "report"
    assert cli.main(["--output-dir", str(out), argument]) == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert not out.exists()


def test_existing_output_refuses_without_mutation(tmp_path, capsys):
    out = tmp_path / "report"
    out.mkdir()
    (out / "keep").write_bytes(b"unchanged")
    assert cli.main(["--output-dir", str(out)]) == 2
    assert "OUTPUT_EXISTS" in capsys.readouterr().err
    assert (out / "keep").read_bytes() == b"unchanged"
    assert set(out.iterdir()) == {out / "keep"}


@pytest.mark.parametrize("relation", ["equal", "descendant", "ancestor", "symlink"])
def test_panel_overlap_rejected_before_any_loading(tmp_path, monkeypatch, capsys, relation):
    panel = tmp_path / "source" / "panel"
    panel.mkdir(parents=True)
    out = {"equal": panel, "descendant": panel / "new", "ancestor": panel.parent}.get(relation)
    if relation == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(panel, target_is_directory=True)
        out = alias / "new"
    monkeypatch.setattr(cli, "load_panel", lambda path: pytest.fail("must validate paths first"))
    assert cli.main(["--panel-root", str(panel), "--output-dir", str(out)]) == 2
    assert "SOURCE_OUTPUT_OVERLAP" in capsys.readouterr().err
    assert list(panel.iterdir()) == []


def test_invalid_panel_leaves_no_output_or_parent(tmp_path, capsys):
    out = tmp_path / "new-parent" / "report"
    assert cli.main(["--panel-root", str(tmp_path / "invalid"), "--output-dir", str(out)]) == 2
    assert capsys.readouterr().err
    assert not out.parent.exists()


def test_incomplete_publish_keeps_identifiable_sibling(tmp_path, monkeypatch, capsys):
    out = tmp_path / "report"

    def fail(*args):
        raise OSError("fixture publish failure")

    monkeypatch.setattr(cli, "_publish", fail)
    assert cli.main(["--output-dir", str(out)]) == 1
    assert "INCOMPLETE" in capsys.readouterr().err
    assert not out.exists()
    leftovers = list(tmp_path.glob(".report.incomplete-*"))
    assert len(leftovers) == 1 and (leftovers[0] / "identity.json").exists()


def test_atomic_publish_never_overwrites_racing_empty_directory(tmp_path, monkeypatch, capsys):
    out = tmp_path / "report"
    publish = cli._publish

    def race(source, destination):
        destination.mkdir()
        publish(source, destination)

    monkeypatch.setattr(cli, "_publish", race)
    assert cli.main(["--output-dir", str(out)]) == 1
    assert list(out.iterdir()) == []
    assert "INCOMPLETE" in capsys.readouterr().err


def test_panel_budget_shortfall_is_successful_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_panel", lambda path: object())
    monkeypatch.setattr(
        cli,
        "diagnostic_panel_report",
        lambda **kwargs: {
            "status": "DIAGNOSTIC_ONLY", "input_integrity": "UNVERIFIED_INPUT", "hashes": {"data": "fixture-panel"},
            "assumptions": ["fixture panel substitution"], "gaps": [],
            "native_costs": [{"side": "BUY", "status": {"status": "VALID"}, "total_report_currency": "16.24", "report_currency": "USDT", "spec_hash": "s"}],
        },
    )
    out = tmp_path / "report"
    assert cli.main(["--panel-root", str(tmp_path / "panel"), "--output-dir", str(out)]) == 0
    data = read_report(out)
    assert data["panel_budget_diagnostics"][0]["status"] == "NO_TRADE"
    assert data["panel_budget_diagnostics"][0]["reason"] == "PER_INTENT_BUDGET_EXCEEDED"
    assert data["rankable"] is False
    assert data["identity"]["panel_data"] == "fixture-panel"
