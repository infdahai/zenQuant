"""Independent raw fixtures; no collector/analyzer imports or writes to sealed data."""

import json
from decimal import Decimal as D
from hashlib import sha256
from pathlib import Path

import pytest

from zenquant.options.ledger import WalletBalance, initial_state
from zenquant.options.risk import RiskPolicy
from zenquant.research.options_platform import panel as adapter
from zenquant.research.options_platform.panel import load_panel
from zenquant.research.options_platform.reporting import (
    diagnostic_panel_report,
    engineering_bias_report,
    manual_scenario_report,
)

from .factories import fill_event, linear_spec, wallet

FAMILIES = ("Deribit_inverse", "Deribit_USDC", "Bybit", "OKX_coin")


def write_minimal_panel(tmp_path, monkeypatch, *, fault=None):
    root = tmp_path / "panel"
    root.mkdir()
    manifest, requests = {}, []

    def write(name, value):
        path = root / name
        path.parent.mkdir(exist_ok=True)
        data = json.dumps(value, separators=(",", ":")).encode()
        path.write_bytes(data)
        manifest[name] = {"bytes": len(data), "sha256": sha256(data).hexdigest()}
        return manifest[name]

    symbols = dict(
        zip(
            FAMILIES,
            ("BTC-16OCT26-81000-C", "BTC_USDC-16OCT26-81000-C", "BTC-16OCT26-81000-C-USDT", "BTC-USD-261016-81000-C"),
        )
    )
    metadata = {}
    for family in FAMILIES:
        symbol = symbols[family]
        if family.startswith("Deribit"):
            inverse = family == "Deribit_inverse"
            m = {
                "instrument_name": symbol,
                "expiration_timestamp": 1792137600000,
                "strike": "81000",
                "option_type": "call",
                "base_currency": "BTC",
                "quote_currency": "BTC" if inverse else "USDC",
                "settlement_currency": "BTC" if inverse else "USDC",
                "counter_currency": "USD" if inverse else "USDC",
                "instrument_type": "reversed" if inverse else "linear",
                "contract_size": "1",
                "min_trade_amount": ".1" if inverse else ".01",
                "tick_size": ".0001" if inverse else "5",
                "tick_size_steps": [
                    {"above_price": ".005" if inverse else "1000", "tick_size": ".0005" if inverse else "20"}
                ],
                "price_index": "btc_usd" if inverse else "btc_usdc",
            }
        elif family == "Bybit":
            m = {
                "symbol": symbol,
                "deliveryTime": "1792137600000",
                "optionsType": "Call",
                "baseCoin": "BTC",
                "quoteCoin": "USDT",
                "settleCoin": "USDT",
                "lotSizeFilter": {"minOrderQty": ".01", "qtyStep": ".01", "maxOrderQty": "500"},
                "priceFilter": {"tickSize": "5"},
            }
        else:
            m = {
                "instId": symbol,
                "expTime": "1792137600000",
                "stk": "81000",
                "optType": "C",
                "instType": "OPTION",
                "uly": "BTC-USD",
                "ctType": "inverse",
                "settleCcy": "BTC",
                "ctValCcy": "BTC",
                "ctVal": "1",
                "ctMult": ".01",
                "minSz": "1",
                "lotSz": "1",
                "tickSz": ".0001",
                "maxLmtSz": "1000000",
            }
        metadata[family] = m
    sources = {}
    for family in FAMILIES:
        name = f"raw/metadata_{family}.json"
        m = metadata[family]
        raw = (
            {"result": [m]}
            if family.startswith("Deribit")
            else {"result": {"list": [m]}}
            if family == "Bybit"
            else {"data": [m]}
        )
        write(name, raw)
        sources[family] = name
    cell = {
        "id": "D30_ATM_C",
        "expiry_ms": 1792137600000,
        "strike": "81000",
        "side": "C",
        "target_DTE": 30,
        "symbols": symbols,
        "metadata": metadata,
    }
    if fault == "missing_family":
        del cell["symbols"]["Deribit_USDC"]
        del cell["metadata"]["Deribit_USDC"]
    write(
        "selection.json",
        {"selected_at": "2026-10-08T17:27:11.248437+00:00", "cells": [cell], "local_metadata_sources": sources},
    )
    protocol = {
        "families": list(FAMILIES),
        "taker_fee_rate_proxy": ".0003",
        "premium_fee_caps": {"Deribit": ".125", "Bybit": ".07", "OKX": ".07"},
        "maximum_pair_server_timestamp_gap_ms": 2000,
        "maximum_pair_local_receive_gap_ms": 2000,
        "maximum_pair_quote_age_at_receipt_ms": 3000,
        "minimum_allowed_quote_age_at_receipt_ms": -1000,
    }
    ph = write("preregistration.json", protocol)["sha256"]
    records = []
    for family in FAMILIES:
        if family not in cell["symbols"]:
            continue
        symbol = symbols[family]
        venue = "Deribit" if family.startswith("Deribit") else "Bybit" if family == "Bybit" else "OKX"
        if venue == "Deribit":
            inverse = family == "Deribit_inverse"
            raw = {
                "testnet": False,
                "result": {
                    "instrument_name": symbol,
                    "state": "open",
                    "bids": [[".03" if inverse else "2400", "1"]],
                    "asks": [[".031" if inverse else "2480", "1"]],
                    "timestamp": 1791480500000,
                    "change_id": 12,
                    "index_price": "80000",
                },
            }
            url = f"https://www.deribit.com/api/v2/public/get_order_book?instrument_name={symbol}&depth=5"
        elif venue == "Bybit":
            raw = {
                "retCode": 1 if fault == "api" else 0,
                "result": {
                    "s": "WRONG" if fault == "symbol" else symbol,
                    "b": [["2410", "1"]],
                    "a": [["2490", "1"]],
                    "ts": 1791480500000,
                    "seq": 999 if fault == "futureseq" else 12,
                    "u": 1,
                    "cts": 1791480500000,
                },
            }
            url = f"https://api.bybit.com/v5/market/orderbook?category=option&symbol={symbol}&limit=5"
        else:
            raw = {
                "code": "0",
                "data": [
                    {
                        "bids": [[".03", "100", "0", "1"]],
                        "asks": [[".031", "100", "0", "1"]],
                        "ts": "1791480500000",
                        "seqId": 12,
                    }
                ],
            }
            url = f"https://www.okx.com/api/v5/market/books?instId={symbol}&sz=5"
        name = f"raw/{'bybit' if venue == 'Bybit' else family}.json"
        info = write(name, raw)
        record = {
            "name": family,
            "raw_path": name,
            "venue": venue,
            "url": url,
            "http_status": 200,
            "completed_at": "2026-10-08T17:28:20.100001+00:00",
            **info,
        }
        requests.append(record)
        records.append({**record, "family": family, "cell_id": cell["id"], "round": 0})
    write("request_ledger.json", {"requests": requests, "preregistration_sha256": ph})
    write("rounds.json", [{"round": 0, "records": records}])
    (root / "seal.json").write_text(json.dumps({"files": manifest}))
    monkeypatch.setitem(adapter._SEAL_PINS, root.resolve(), sha256((root / "seal.json").read_bytes()).hexdigest())
    return root


def test_tampered_raw_is_not_valid_panel(tmp_path, monkeypatch):
    root = write_minimal_panel(tmp_path, monkeypatch)
    raw = root / "raw/bybit.json"
    raw.write_text(raw.read_text() + " ")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        load_panel(root)


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("api", "API_STATUS"),
        ("symbol", "SYMBOL_MISMATCH"),
        ("futureseq", "SEQUENCE_CONTINUITY_UNPROVEN"),
        ("missing_family", "MISSING_FAMILY"),
    ],
)
def test_source_gaps_are_structured(tmp_path, monkeypatch, fault, reason):
    panel = load_panel(write_minimal_panel(tmp_path, monkeypatch, fault=fault))
    assert any(gap.reason == reason for gap in panel.gaps)
    assert not diagnostic_panel_report(panel=panel, quantities_base=(D(".1"),))["live_eligible"]


def test_four_families_native_units_and_precision(tmp_path, monkeypatch):
    panel = load_panel(write_minimal_panel(tmp_path, monkeypatch))
    assert len(panel.books) == 4
    specs = {r.family: r.book.spec for r in panel.records}
    assert specs["OKX_coin"].multiplier == D(".01")
    assert specs["OKX_coin"].quantity_unit == "contracts"
    assert specs["Deribit_inverse"].tick_bands == ((D(0), D(".0001")), (D(".005"), D(".0005")))
    assert panel.books[0].received_ns % 1_000_000 == 1000
    report = diagnostic_panel_report(panel=panel, quantities_base=(D(".01"), D(".1")))
    assert report["actual_pnl"] is report["paper_pnl"] is report["net_strategy_pnl"] is None
    assert report["sharpe"] is report["capacity"] is None
    inverse = next(
        r
        for r in report["native_costs"]
        if r["family"] == "Deribit_inverse" and r["quantity_base"] == ".1" and r["side"] == "BUY"
    )
    assert D(inverse["fee_native"]) == D(".00003")
    assert inverse["index_in_quote"] == "1"
    assert all(D(row["actual_dte"]) < 8 for row in report["cells"])
    assert report["static_routing_baseline"]["deployed"] is False
    bias = engineering_bias_report(panel=panel)
    assert all(not row["rankable"] for row in bias["variants"])


@pytest.mark.parametrize("path", ["/tmp/arbitrary.json", "../outside.json"])
def test_path_in_seal_is_never_authority(tmp_path, monkeypatch, path):
    root = write_minimal_panel(tmp_path, monkeypatch)
    seal = json.loads((root / "seal.json").read_text())
    seal["files"][path] = {"bytes": 0, "sha256": "0" * 64}
    (root / "seal.json").write_text(json.dumps(seal))
    monkeypatch.setitem(adapter._SEAL_PINS, root.resolve(), sha256((root / "seal.json").read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="UNSAFE_PATH"):
        load_panel(root)


def test_seal_cannot_self_overwrite(tmp_path, monkeypatch):
    root = write_minimal_panel(tmp_path, monkeypatch)
    (root / "seal.json").write_text('{"files":{}}')
    with pytest.raises(ValueError, match="SEAL_HASH_MISMATCH"):
        load_panel(root)


def test_manual_cash_delta_only_in_math_scenarios():
    spec = linear_spec()
    state = initial_state(specs=(spec,), balances=(WalletBalance(wallet=wallet(), cash="1000"),))
    policy = RiskPolicy(
        per_intent_report="10",
        active_and_pending_report="50",
        rolling_tail_paid_report="20",
        rolling_window_ns=30 * 86400 * 10**9,
        report_currency="USDT",
    )
    report = manual_scenario_report(
        initial=state, events=(fill_event(1, spec=spec, quantity=".1", price="20", fee=".2"),), policy=policy
    )
    assert report["actual_pnl"] is None
    assert D(report["math_scenarios"][0]["wallet_cash_delta"][0]["delta"]) == D("-2.2")
    assert "cash_delta" not in report


def test_sealed_input_integration(local_options_panel_root):
    root = local_options_panel_root
    panel = load_panel(root)
    report = diagnostic_panel_report(panel=panel, quantities_base=(D(".01"), D(".03"), D(".1")))
    summary = json.loads((root / "summary.json").read_text())
    counts = report["counts"]
    assert counts["requests"] == summary["network_requests"] == 493
    assert counts["books"] == summary["quote_records"] == 480
    assert counts["cross_size_cases"] == summary["cross_scenarios"] == 3600
    assert counts["legal_depth_complete"] == summary["legal_depth_complete_cross_count"] == 2640
    assert counts["pair_timing_valid"] == summary["legal_timing_filtered_cross_count"] == 2586
    assert counts["gross_positive"] == 186
    assert counts["fee_proxy_positive"] == summary["positive_after_opening_fees_count"] == 0
    assert counts["timing_excluded"] == 54
    assert counts["legal_cost_cases"] == 1200
    assert counts["independent_pair_observations"] < counts["pair_timing_valid"]
    assert report["hashes"]["sealed_entries"] == 510
    assert report["native_frozen_fee_comparison"]["different_fee_sides"] == 0
    assert D(report["native_frozen_fee_comparison"]["maximum_absolute_fee_difference_usd"]) == 0
    ignore_fee = next(r for r in report["engineering_bias"]["variants"] if r["variant"] == "ignore_fee")
    assert ignore_fee["false_positive_labels"] == 186
    valid = [r for r in report["frozen_usd_proxy_cross_diagnostics"] if r["status"] == "VALID"]
    best = max(valid, key=lambda row: D(row["after_fee_proxy_usd"]))
    assert (best["round"], best["cell_id"], best["buy"], best["sell"], best["quantity_base"]) == (
        2,
        "D30_ATM_C",
        "OKX_coin",
        "Bybit",
        ".01",
    )
    assert D(best["after_fee_proxy_usd"]) == D("-.06217292")
    assert all(spec.valid_from_ns > 0 and spec.exercise_style == "UNKNOWN" for spec in panel.specs)
    assert any("underlyingPrice" in observation.source_id for observation in panel.observations)
    assert any(observation.value < 0 and observation.source_id.endswith(":delta") for observation in panel.observations)


def reseal_fixture(root, monkeypatch, name, value):
    """Test-only trusted controller pin update, never a loader self-seal."""
    data = json.dumps(value).encode()
    (root / name).write_bytes(data)
    seal = json.loads((root / "seal.json").read_text())
    seal["files"][name] = {"bytes": len(data), "sha256": sha256(data).hexdigest()}
    (root / "seal.json").write_text(json.dumps(seal))
    monkeypatch.setitem(adapter._SEAL_PINS, root.resolve(), sha256((root / "seal.json").read_bytes()).hexdigest())


def test_symlink_raw_rejected_even_when_target_bytes_match(tmp_path, monkeypatch):
    root = write_minimal_panel(tmp_path, monkeypatch)
    raw = root / "raw/bybit.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(raw.read_bytes())
    raw.unlink()
    raw.symlink_to(outside)
    with pytest.raises(ValueError, match="UNSAFE_PATH"):
        load_panel(root)


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("unsealed", "UNSEALED_INPUT"),
        ("join", "LEDGER_ROUND_MISMATCH"),
        ("absolute_metadata", "UNAPPROVED_METADATA_ROOT"),
    ],
)
def test_manifest_and_identity_authority(tmp_path, monkeypatch, fault, reason):
    root = write_minimal_panel(tmp_path, monkeypatch)
    if fault == "unsealed":
        ledger = json.loads((root / "request_ledger.json").read_text())
        ledger["requests"][0]["raw_path"] = "raw/unlisted.json"
        (root / "raw/unlisted.json").write_text("{}")
        reseal_fixture(root, monkeypatch, "request_ledger.json", ledger)
    elif fault == "join":
        rounds = json.loads((root / "rounds.json").read_text())
        rounds[0]["records"][0]["url"] += "&changed=true"
        reseal_fixture(root, monkeypatch, "rounds.json", rounds)
    else:
        selection = json.loads((root / "selection.json").read_text())
        selection["base_metadata_source"] = "/tmp/untrusted_metadata"
        reseal_fixture(root, monkeypatch, "selection.json", selection)
    with pytest.raises(ValueError, match=reason):
        load_panel(root)


def test_joint_asof_includes_conversion_index_not_just_each_leg(tmp_path, monkeypatch):
    root = write_minimal_panel(tmp_path, monkeypatch)
    raw_name = "raw/Deribit_inverse.json"
    raw = json.loads((root / raw_name).read_text())
    raw["result"]["timestamp"] -= 2500
    reseal_fixture(root, monkeypatch, raw_name, raw)
    raw_info = json.loads((root / "seal.json").read_text())["files"][raw_name]
    ledger = json.loads((root / "request_ledger.json").read_text())
    for request in ledger["requests"]:
        if request["raw_path"] == raw_name:
            request.update(raw_info)
        if request["venue"] == "Bybit":
            request["completed_at"] = "2026-10-08T17:28:21.700001+00:00"
    reseal_fixture(root, monkeypatch, "request_ledger.json", ledger)
    rounds = json.loads((root / "rounds.json").read_text())
    for record, request in zip(rounds[0]["records"], ledger["requests"]):
        record.update(request)
    reseal_fixture(root, monkeypatch, "rounds.json", rounds)
    panel = load_panel(root)
    report = diagnostic_panel_report(panel=panel, quantities_base=(D(".01"),))
    pair = next(
        r for r in report["frozen_usd_proxy_cross_diagnostics"] if r["buy"] == "OKX_coin" and r["sell"] == "Bybit"
    )
    assert pair["reason"] == "JOINT_TIMING_EXCLUDED"
    variant = next(r for r in report["engineering_bias"]["variants"] if r["variant"] == "ignore_index_age")
    assert variant["counts"]["pair_timing_valid"] > report["engineering_bias"]["baseline_counts"]["pair_timing_valid"]


def test_decimal_context_does_not_change_diagnostic(tmp_path, monkeypatch):
    from decimal import localcontext

    panel = load_panel(write_minimal_panel(tmp_path, monkeypatch))
    baseline = diagnostic_panel_report(panel=panel, quantities_base=(D(".1"),))
    with localcontext() as context:
        context.prec = 3
        actual = diagnostic_panel_report(panel=panel, quantities_base=(D(".1"),))
    assert actual == baseline


def test_manual_panel_does_not_inherit_verified_seal_references(tmp_path, monkeypatch):
    from dataclasses import fields

    loaded = load_panel(write_minimal_panel(tmp_path, monkeypatch))
    manual = adapter.PanelInput(
        **{field.name: getattr(loaded, field.name) for field in fields(loaded) if field.name != "verification"}
    )
    report = diagnostic_panel_report(panel=manual, quantities_base=(D(".1"),))
    assert report["sealed_input_identities"]
    assert report["input_integrity"] == "UNVERIFIED_INPUT"
    assert report["engineering_bias"]["input_integrity"] == "UNVERIFIED_INPUT"
    assert not report["execution_authority"]


@pytest.mark.parametrize("changed_field", ["records", "books", "protocol_json", "observations", "assumptions"])
def test_replaced_panel_cannot_keep_verified_input_binding(tmp_path, monkeypatch, changed_field):
    from dataclasses import replace

    loaded = load_panel(write_minimal_panel(tmp_path, monkeypatch))
    original = diagnostic_panel_report(panel=loaded, quantities_base=(D(".1"),))
    assert original["input_integrity"] == "PINNED_BYTES_VERIFIED"
    if changed_field in ("records", "books"):
        book = replace(loaded.books[0], asks=(replace(loaded.books[0].asks[0], price=D(".032")),))
        update = (
            (replace(loaded.records[0], book=book),) + loaded.records[1:]
            if changed_field == "records"
            else (book,) + loaded.books[1:]
        )
    elif changed_field == "protocol_json":
        protocol = json.loads(loaded.protocol_json)
        protocol["maximum_pair_quote_age_at_receipt_ms"] = 4000
        update = json.dumps(protocol)
    elif changed_field == "observations":
        update = (replace(loaded.observations[0], value=D("90000")),) + loaded.observations[1:]
    else:
        update = loaded.assumptions + ("caller changed assumption",)
    changed = replace(loaded, **{changed_field: update})
    report = diagnostic_panel_report(panel=changed, quantities_base=(D(".1"),))
    assert report["sealed_input_identities"] == original["sealed_input_identities"]
    assert report["input_integrity"] == "VERIFICATION_BINDING_MISMATCH"
    assert report["hashes"]["data"] != original["hashes"]["data"]
    assert report["hashes"]["source_references"] == original["hashes"]["source_references"]
    assert report["engineering_bias"]["input_integrity"] == "VERIFICATION_BINDING_MISMATCH"
    assert engineering_bias_report(panel=changed)["input_integrity"] == "VERIFICATION_BINDING_MISMATCH"
    if changed_field == "protocol_json":
        assert report["hashes"]["protocol"] != original["hashes"]["protocol"]
    assert not report["execution_authority"]


def test_bias_reports_expose_their_own_quantity_and_timing_scope(tmp_path, monkeypatch):
    loaded = load_panel(write_minimal_panel(tmp_path, monkeypatch))
    report = diagnostic_panel_report(panel=loaded, quantities_base=(D(".1"),))
    assert report["quantity_filter"] == ["0.1"]
    nested = report["engineering_bias"]
    standalone = engineering_bias_report(panel=loaded)
    assert nested["quantity_filter"] == standalone["quantity_filter"] == ["0.01", "0.03", "0.1"]
    assert nested["timing_filter"] == standalone["timing_filter"] == report["timing_filter"]


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("missing", ["panel", "scout", "both"])
def test_local_panel_absence_is_skip_only_in_portable_mode(tmp_path, monkeypatch, strict, missing):
    from .conftest import require_local_options_panel

    # Independently pinned deterministic fixture bytes, never a real source copy.
    for name, attr in (("panel", "_PANEL"), ("scout", "_SCOUT")):
        root = tmp_path / name
        monkeypatch.setattr(adapter, attr, root)
        if missing not in (name, "both"):
            root.mkdir()
            data = b'{"files":{}}'
            (root / "seal.json").write_bytes(data)
            monkeypatch.setitem(adapter._SEAL_PINS, root, sha256(data).hexdigest())
    outcome = pytest.fail.Exception if strict else pytest.skip.Exception
    with pytest.raises(outcome, match="LOCAL_OPTIONS_PANEL_ABSENT.*verification NOT performed"):
        require_local_options_panel(strict=strict)


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("invalid", ["panel", "scout"])
@pytest.mark.parametrize("fault", ["missing_seal", "tampered_seal", "missing_entry", "tampered_entry"])
def test_present_invalid_local_source_never_becomes_availability_skip(tmp_path, monkeypatch, strict, invalid, fault):
    from .conftest import require_local_options_panel

    # The other root is absent: that must not hide present corrupt evidence.
    for name, attr in (("panel", "_PANEL"), ("scout", "_SCOUT")):
        monkeypatch.setattr(adapter, attr, tmp_path / name)
    root = tmp_path / invalid
    root.mkdir()
    payload = b'{"source":"fixture"}'
    seal = json.dumps({"files": {"source.raw": {"bytes": len(payload), "sha256": sha256(payload).hexdigest()}}}).encode()
    monkeypatch.setitem(adapter._SEAL_PINS, root, sha256(seal).hexdigest())
    if fault != "missing_seal":
        (root / "seal.json").write_bytes(b'{}' if fault == "tampered_seal" else seal)
    if fault != "missing_entry":
        (root / "source.raw").write_bytes(b'changed' if fault == "tampered_entry" else payload)
    with pytest.raises((FileNotFoundError, adapter.PanelError)):
        require_local_options_panel(strict=strict)
