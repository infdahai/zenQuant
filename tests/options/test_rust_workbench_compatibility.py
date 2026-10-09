"""Finite-domain P1 oracles against the actual offline native executable."""

import importlib.util
import json
import os
import subprocess
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from zenquant.options.contracts import Book, BookLevel, InstrumentSpec, Observation
from zenquant.options.costs import FeePolicy, TimingPolicy, quote_cost
from zenquant.options.payoffs import settlement_plan

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/export_options_workbench_panel.py"
MAIN = Path("/Users/disaster/workspace/code/zenQuant")


def exporter():
    assert SCRIPT.is_file(), "Task5 exporter must exist"
    spec = importlib.util.spec_from_file_location("rust_panel_exporter", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_canonical_decimal_is_context_independent():
    module = exporter()
    results = []
    for precision in (6, 100):
        with localcontext() as context:
            context.prec = precision
            results.append(module.native_hash({"z": Decimal("123456789.1234567800"), "a": Decimal("-0")}))
    assert results[0] == results[1]
    assert module.native_payload(Decimal("1.2300")) == "1.23"
    with pytest.raises(ValueError):
        module.native_hash({"bad": 1.5})
    with pytest.raises(ValueError):
        module.native_payload(Decimal("0." + "1" * 29))


@pytest.fixture(scope="module")
def binary():
    subprocess.run(
        ["cargo", "build", "--manifest-path", str(REPO / "rust/Cargo.toml"), "--locked", "-p", "options-workbench"],
        check=True,
        cwd=REPO,
        capture_output=True,
    )
    return REPO / "rust/target/debug/options-workbench"


def fixture(name="terminal-eth-call"):
    return json.loads((REPO / f"rust/options-workbench/fixtures/{name}.json").read_text())


def rebind(request):
    module = exporter()
    hashes = []
    for quote, leg in zip(request["quotes"], request["candidates"][0]["legs"]):
        digest = module.native_hash(quote["book"]["spec"])
        leg["instrument"]["spec_hash"] = digest
        hashes.append(digest)
    for scenario in request["scenarios"]["scenarios"]:
        point = scenario["points"][0]
        price = point["delivery_by_spec_hash"][0][1]
        point["delivery_by_spec_hash"] = [[digest, price] for digest in hashes]
    request["scenarios"]["view_hash"] = module.native_hash(request["views"][0])


def run_native(binary, tmp_path, request, *, panel=None):
    tmp_path = tmp_path.resolve()
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    output = tmp_path / "report"
    command = [str(binary), "--request-file", str(path), "--output-dir", str(output)]
    if panel:
        command.extend(["--panel-file", str(panel.resolve())])
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    return result, json.loads((output / "report.json").read_text()) if output.exists() else None


def p1_oracles(request):
    costs, outcomes = [], []
    for quote, leg in zip(request["quotes"], request["candidates"][0]["legs"]):
        raw = quote["book"]["spec"].copy()
        raw["exercise_style"] = "european"
        for key in ("strike", "multiplier", "min_quantity_native", "quantity_step_native"):
            raw[key] = Decimal(raw[key])
        raw["max_quantity_native"] = None if raw["max_quantity_native"] is None else Decimal(raw["max_quantity_native"])
        raw["tick_bands"] = tuple(tuple(Decimal(v) for v in band) for band in raw["tick_bands"])
        spec = InstrumentSpec(**raw)
        book = Book(
            spec=spec,
            **{k: quote["book"][k] for k in ("source_ns", "received_ns", "available_ns", "raw_hash")},
            **{
                side: tuple(
                    BookLevel(price=Decimal(v["price"]), quantity_native=Decimal(v["quantity_native"]))
                    for v in quote["book"][side]
                )
                for side in ("asks", "bids")
            },
        )
        observations = {
            key: Observation(**{**quote[key], "value": Decimal(quote[key]["value"])})
            for key in ("index_in_quote", "quote_to_report")
        }
        fee = quote["fee_policy"]
        cost = quote_cost(
            book=book,
            side=leg["side"],
            quantity_native=Decimal(leg["quantity_native"]),
            **observations,
            fee_policy=FeePolicy(**{**fee, "rate": Decimal(fee["rate"]), "premium_cap": Decimal(fee["premium_cap"])}),
            timing=TimingPolicy(**quote["timing"]),
            asof_ns=request["cutoff_ns"],
            report_currency=request["views"][0]["report_currency"],
        )
        costs.append(cost)
        leg_payoffs = []
        for scenario in request["scenarios"]["scenarios"]:
            point = scenario["points"][0]
            delivery = Observation(
                value=point["delivery_by_spec_hash"][0][1],
                source_ns=spec.expiry_ns,
                received_ns=spec.expiry_ns,
                available_ns=spec.expiry_ns,
                source_id="finite_oracle",
                provenance="fixture",
            )
            plan = settlement_plan(
                spec=spec, quantity_native=leg["quantity_native"], delivery=delivery, expiry_id=scenario["scenario_id"]
            )
            assert plan.status.status == "VALID"
            leg_payoffs.append(sum((action.amount for action in plan.actions), Decimal(0)))
        outcomes.append(leg_payoffs)
    return costs, outcomes


@pytest.mark.parametrize("name", ["terminal-eth-call", "terminal-eth-straddle"])
@pytest.mark.parametrize("mode", ["plain", "capped", "depth", "contracts", "wallet", "terminal_fee"])
def test_finite_cost_and_terminal_oracles(binary, tmp_path, name, mode):
    request = fixture(name)
    if mode == "capped":
        for quote in request["quotes"]:
            quote["fee_policy"]["rate"] = "0.1"
    if mode == "depth":
        for quote in request["quotes"]:
            quote["book"]["asks"] = [{"price": "2", "quantity_native": "0.005"}, {"price": "3", "quantity_native": "1"}]
    if mode == "contracts":
        for quote in request["quotes"]:
            quote["book"]["spec"]["quantity_unit"] = "contracts"
            quote["book"]["spec"]["multiplier"] = "0.1"
    if mode == "terminal_fee":
        request["candidates"][0]["terminal_fee_native"] = "0.001"
    if mode == "wallet":
        request["hypothetical_balances"][0]["cash"] = "0"
    rebind(request)
    oracles = []
    for precision in (6, 100):
        with localcontext() as context:
            context.prec = precision
            oracles.append(p1_oracles(request))
    assert oracles[0] == oracles[1]
    result, report = run_native(binary, tmp_path, request)
    assert result.returncode == 0, result.stderr
    analysis = report["expressions"][0]
    costs, outcomes = oracles[0]
    for native, old in zip(analysis["costs"], costs):
        assert native["status"]["status"] == old.status.status == "VALID"
        for field in ("premium_native", "fee_native", "total_report_currency"):
            assert Decimal(native[field]) == getattr(old, field)
    if mode == "wallet":
        assert analysis["support"]["modeled_feasibility"]["status"] == "REJECTED"
    else:
        for i, outcome in enumerate(analysis["outcomes"]):
            gross = sum((values[i] for values in outcomes), Decimal(0))
            assert Decimal(outcome["gross_payoff"]) == gross
            assert Decimal(outcome["conditional_model_pnl"]) == gross - sum(
                (cost.total_report_currency for cost in costs), Decimal(0)
            ) - Decimal(request["candidates"][0]["terminal_fee_native"])


@pytest.fixture(scope="module")
def sealed_export(tmp_path_factory):
    root = MAIN / "results/crypto_opportunity/20261009_options_liquidity_panel_v1"
    if not (root / "seal.json").is_file():
        pytest.skip("Original allowlisted sealed evidence absent")
    directory = tmp_path_factory.mktemp("rust-sealed").resolve()
    output = directory / "panel.json"
    environment = {**os.environ, "PYTHONPATH": str(MAIN)}
    result = subprocess.run(
        [str(MAIN / ".venv/bin/python"), str(SCRIPT), "--panel-root", str(root), "--output-file", str(output)],
        env=environment,
        cwd=directory,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return output, json.loads(output.read_text())


def panel_request(panel):
    request = fixture()
    quote = next(q for q in panel["quotes"] if q["book"]["spec"]["product"] == "linear_option")
    spec = quote["book"]["spec"]
    cutoff = max(q["book"]["available_ns"] for q in panel["quotes"])
    view = request["views"][0]
    view.update(underlying="BTC", horizon_ns=spec["expiry_ns"], report_currency=spec["quote_currency"])
    for meta in (view["meta"], request["scenarios"]["meta"]):
        meta.update(cutoff_ns=cutoff, received_ns=cutoff, available_ns=cutoff, provenance="manual_unattested")
    request["cutoff_ns"] = cutoff
    request["fixture_name"] = None
    request["quotes"] = []
    leg = request["candidates"][0]["legs"][0]
    leg["instrument"] = {key: spec[key] for key in ("venue", "product", "instrument_id", "underlying", "rule_version")}
    digest = exporter().native_hash(spec)
    leg["instrument"].update(spec_schema="options-instrument.rust.v1", spec_hash=digest)
    leg["quantity_native"] = spec["min_quantity_native"]
    request["candidates"][0]["terminal_fee_native"] = None
    request["risk_policy"]["report_currency"] = spec["quote_currency"]
    request["hypothetical_balances"][0]["wallet"].update(venue=spec["venue"], currency=spec["quote_currency"])
    for scenario in request["scenarios"]["scenarios"]:
        scenario["points"][0].update(at_ns=spec["expiry_ns"], delivery_by_spec_hash=[[digest, spec["strike"]]])
    request["scenarios"]["view_hash"] = exporter().native_hash(view)
    return request


def test_real_export_all_pins_native_admission(binary, tmp_path, sealed_export):
    path, panel = sealed_export
    assert len(panel["quotes"]) == 480
    assert len(panel["pinned_sources"]) == 543
    assert len({exporter().native_hash(q["book"]["spec"]) for q in panel["quotes"]}) == 48
    assert panel["projection_sha256"] == exporter().native_hash(panel["quotes"])
    assert all(
        q["fee_known_ns"] is None and q["fx_pair"] is None and q["index_currency"] is None for q in panel["quotes"]
    )
    result, report = run_native(binary, tmp_path, panel_request(panel), panel=path)
    assert result.returncode == 0, result.stderr
    assert len(report["input"]["quotes"]) == 480
    assert len(report["identity"]["verified_raw_sources"]) == 543
    assert report["identity"]["projection_trust"] == "EXPORTED_PROJECTION_UNATTESTED"
    analysis = report["expressions"][0]
    assert analysis["support"]["model_support"]["status"] == "UNSUPPORTED"
    assert analysis["support"]["modeled_feasibility"]["status"] == "UNSUPPORTED"
    assert analysis["costs"][0]["status"]["status"] == "NOT_EVALUABLE"
    assert analysis["support"]["account_eligibility"]["status"] == "UNVERIFIED"
    assert analysis["support"]["execution_evidence"]["status"] == "UNVERIFIED"
    assert report["execution_authority"] == "NONE"


@pytest.mark.parametrize("attack", ["spec", "fee", "book"])
def test_rehashed_projection_tamper_cannot_promote_authority(binary, tmp_path, sealed_export, attack):
    _, panel = sealed_export
    panel = json.loads(json.dumps(panel))
    request = panel_request(panel)
    for quote in panel["quotes"]:
        if attack == "spec":
            quote["book"]["spec"].update(
                exercise_style="EUROPEAN", settlement_rule="linear_cash", provenance="observed"
            )
        elif attack == "fee":
            quote["fee_policy"].update(rate="0", provenance="observed")
            quote["fee_known_ns"] = 0
        else:
            quote["book"]["asks"][0]["quantity_native"] = "999999999"
    panel["projection_sha256"] = exporter().native_hash(panel["quotes"])
    path = tmp_path.resolve() / "tampered.json"
    path.write_text(json.dumps(panel))
    result, report = run_native(binary, tmp_path, request, panel=path)
    assert result.returncode == 0, result.stderr
    assert report["execution_authority"] == "NONE"
    assert not report["rankable"] and not report["live_eligible"]
    assert report["expressions"][0]["support"]["model_support"]["status"] != "VALID"
    assert all(
        q["book"]["spec"]["exercise_style"] == "UNKNOWN" and q["book"]["spec"]["settlement_rule"] == "UNKNOWN"
        for q in report["input"]["quotes"]
    )


def test_exporter_no_replace_traversal_symlink_and_source_overlap(tmp_path, sealed_export):
    module = exporter()
    output, _ = sealed_export
    before = output.read_bytes()
    root = next(iter(module.TRUSTED_ROOTS))
    for target in (output, root / "forbidden.json", tmp_path / "unused" / ".." / "bad.json"):
        with pytest.raises((ValueError, OSError)):
            module.export_panel(root, target)
    link = tmp_path / "link"
    link.symlink_to(output.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        module.safe_path(link / "bad.json")
    assert output.read_bytes() == before
    assert not (tmp_path / "unused").exists()


def test_native_one_third_and_precision_domain_fail_closed(binary, tmp_path):
    request = fixture()
    request["quotes"][0]["book"]["asks"][0]["price"] = "3"
    request["quotes"][0]["book"]["bids"][0]["price"] = "2"
    request["quotes"][0]["book"]["spec"]["multiplier"] = "3"
    request["quotes"][0]["book"]["spec"]["quantity_unit"] = "contracts"
    request["quotes"][0]["book"]["spec"]["price_unit"] = "quote_per_contract"
    request["candidates"][0]["terminal_fee_native"] = "0.001"
    rebind(request)
    result, report = run_native(binary, tmp_path, request)
    assert result.returncode == 0, result.stderr
    analysis = report["expressions"][0]
    assert analysis["break_even_levels"] is None
    assert any(reason == "NUMERICAL_LIMITATION:NumericalLimitation" for reason in analysis["reasons"])


def test_sell_cost_diagnostic_matches_p1(binary, tmp_path):
    request = fixture("terminal-vertical")
    rebind(request)
    old, _ = p1_oracles(request)
    result, report = run_native(binary, tmp_path, request)
    assert result.returncode == 0, result.stderr
    native = report["expressions"][0]["costs"]
    assert any(cost["side"] == "SELL" for cost in native)
    for cost, oracle in zip(native, old):
        assert Decimal(cost["premium_native"]) == oracle.premium_native
        assert Decimal(cost["fee_native"]) == oracle.fee_native
        assert Decimal(cost["total_report_currency"]) == oracle.total_report_currency
    assert report["execution_authority"] == "NONE"


def test_native_overprecision_is_invalid_without_publication(binary, tmp_path):
    request = fixture()
    request["candidates"][0]["legs"][0]["quantity_native"] = "0." + "1" * 29
    result, report = run_native(binary, tmp_path, request)
    assert result.returncode == 2
    assert report is None and result.stderr.strip() == "INVALID_INPUT"


def test_exporter_rejects_unbound_projection(tmp_path, monkeypatch):
    from dataclasses import replace

    from zenquant.research.options_platform.panel import load_panel

    from .test_panel import write_minimal_panel

    root = write_minimal_panel(tmp_path, monkeypatch)
    panel = load_panel(root)
    with pytest.raises(ValueError):
        exporter().project_panel(replace(panel, verification=None), [])
