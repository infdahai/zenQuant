#!/usr/bin/env python3
"""Offline options diagnostics; fixture mathematics grants no trading authority."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import sys
import tempfile
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from hashlib import sha256
from pathlib import Path

from zenquant.options import contracts, costs, ledger, payoffs, risk
from zenquant.options.contracts import (
    Book,
    BookLevel,
    CombinationIntent,
    EventKey,
    InstrumentSpec,
    LegIntent,
    Observation,
    WalletKey,
    canonical_payload,
    fingerprint,
)
from zenquant.options.costs import FeePolicy, TimingPolicy, quote_cost
from zenquant.options.ledger import LedgerEvent, WalletBalance, initial_state, wallet_cash
from zenquant.options.payoffs import settlement_plan
from zenquant.options.risk import RiskPolicy, approve_intent
from zenquant.research.options_platform import panel as panel_module
from zenquant.research.options_platform import replay as replay_module
from zenquant.research.options_platform import reporting
from zenquant.research.options_platform.panel import PanelError, load_panel
from zenquant.research.options_platform.replay import replay, settlement_to_ledger_events
from zenquant.research.options_platform.reporting import diagnostic_panel_report, manual_scenario_report

_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)
D = Decimal


def _key(n: int) -> EventKey:
    return EventKey(event_id=f"fixture-{n}", source="fixture", event_ns=n, received_ns=n, available_ns=n, journal_sequence=n)


def _observation(value: str, n: int) -> Observation:
    return Observation(value=value, source_ns=n, received_ns=n, available_ns=n, source_id=f"fixture-{n}", provenance="fixture")


def _spec(option_type: str) -> InstrumentSpec:
    return InstrumentSpec(
        venue="FIXTURE", instrument_id=f"BTC-1000-100-{option_type}", product="linear_option", underlying="BTC",
        quote_currency="USDT", strike_currency="USDT", settlement_currency="USDT", margin_currency="USDT",
        expiry_ns=1000, strike=D("100"), option_type=option_type, exercise_style="european", quantity_unit="base",
        multiplier=D("1"), price_unit="quote_per_base", min_quantity_native=D(".01"), quantity_step_native=D(".01"),
        max_quantity_native=None, tick_bands=((D("0"), D(".01")),), settlement_index="BTC-USDT",
        settlement_window_ns=1, settlement_rule="linear_cash", rule_version="fixture.v1", metadata_hash="fixture-metadata",
        valid_from_ns=0, valid_until_ns=None, provenance="fixture",
    )


def _fixture(capital: str, scenario: str) -> dict:
    """Explicit independent capital and receipts, separate from risk preflight."""
    specs = (_spec("C"),) if scenario == "long-call" else (_spec("C"), _spec("P"))
    wallet = WalletKey(venue="FIXTURE", account="fixture-account", account_mode="standard", currency="USDT")
    initial = initial_state(specs=specs, balances=(WalletBalance(wallet=wallet, cash=D(capital)),))
    scale = D(capital) / D("1000")
    policy = RiskPolicy(per_intent_report=D("10") * scale, active_and_pending_report=D("50") * scale,
                        rolling_tail_paid_report=D("20") * scale, rolling_window_ns=30 * 86400 * 10**9, report_currency="USDT")
    quantity = D(".01") if scenario == "long-call" else D(".02")
    intent = CombinationIntent(intent_id="fixture-manual", legs=tuple(LegIntent(spec=s, wallet=wallet, quantity_native=quantity) for s in specs),
                               template="long_call" if scenario == "long-call" else "long_straddle", sleeve="tail", decision_cutoff_ns=10,
                               valid_until_ns=20, policy_hash=fingerprint(policy))
    evidence = tuple(quote_cost(
        book=Book(spec=s, bids=(BookLevel(price=D("19"), quantity_native=D("1")),),
                  asks=(BookLevel(price=D("20"), quantity_native=D("1")),), source_ns=10, received_ns=10, available_ns=10, raw_hash="fixture-book"),
        side="BUY", quantity_native=quantity, index_in_quote=_observation("100", 10), quote_to_report=_observation("1", 10),
        fee_policy=FeePolicy(rate=D(".02"), premium_cap=D("1"), fee_currency="USDT", provenance="fixture"),
        timing=TimingPolicy(max_age_ns=100, minimum_source_age_ns=0), asof_ns=10, report_currency="USDT",
    ) for s in specs)
    preflight = approve_intent(intent=intent, state=initial, costs=evidence, wallet_fx=(), policy=policy, asof_ns=10)
    events = []
    for index, spec in enumerate(specs):
        events.append(LedgerEvent(key=_key(11 + index), kind="FILL", wallet=wallet, instrument_id=spec.instrument_id,
                                  intent_id=intent.intent_id, order_id=f"fixture-order-{index}", sleeve="tail", quantity_native=quantity,
                                  price=D("20"), amount=None, fee=D(".02"), fee_wallet=wallet, parent_id=None, raw_hash=f"fixture-fill-{index}"))
    for index, spec in enumerate(specs):
        plan = settlement_plan(spec=spec, quantity_native=quantity, delivery=_observation("150", 1000), expiry_id=f"fixture-expiry-{index}")
        events.extend(settlement_to_ledger_events(plan=plan, key=_key(1000 + index * 10), wallet=wallet, intent_id=intent.intent_id,
                                                 fee_amount=D(".01")))
    event_tuple = tuple(events)
    report = manual_scenario_report(initial=initial, events=event_tuple, policy=policy)
    states = replay(initial=initial, events=event_tuple)
    report.update(canonical_payload(dict(
        provenance="fixture", capital=capital, scenario=scenario,
        model_preflight=preflight,
        manual_inputs={"currency": "USDT", "initial_cash": capital, "option_quantity_native_per_leg": quantity,
                       "premium_price_per_base": D("20"), "opening_fee_per_leg": D(".02"), "settlement_fee_per_leg": D(".01"),
                       "strike": D("100"), "delivery_price": D("150"), "specs": specs, "events": event_tuple},
        cash_trace=tuple({"event_id": e.key.event_id, "kind": e.kind, "cash_USDT": wallet_cash(s, wallet),
                     "positions": len(s.positions), "system_claims": len(s.claims)} for e, s in zip(event_tuple, states, strict=True)),
    )))
    return report


def _panel_budgets(panel_report: dict, policy: dict) -> list[dict]:
    """Price-only budget screen; never an account or complete risk approval."""
    limit = D(policy["per_intent_report"])
    rows = []
    for cost in panel_report.get("native_costs", []):
        if cost["side"] != "BUY":
            continue
        total = cost["total_report_currency"]
        if cost["status"]["status"] != "VALID" or total is None:
            status, reason = "NOT_EVALUABLE", cost["status"]["reason"]
        elif cost["report_currency"] != policy["report_currency"]:
            status, reason = "NOT_EVALUABLE", "EXPLICIT_REPORT_CURRENCY_FX_REQUIRED"
        elif D(total) > limit:
            status, reason = "NO_TRADE", "PER_INTENT_BUDGET_EXCEEDED"
        else:
            status, reason = "NOT_EVALUABLE", "PRICE_WITHIN_BUDGET_ACCOUNT_AND_SETTLEMENT_UNVERIFIED"
        rows.append({"spec_hash": cost["spec_hash"] if "spec_hash" in cost else cost["instrument_hash"],
                     "quantity_native": cost.get("quantity_native"), "native_cost": total,
                     "currency": cost["report_currency"], "limit": str(limit), "status": status, "reason": reason,
                     "execution_authority": "NONE"})
    return rows


def _report(capital: str, scenario: str, panel_root: Path | None) -> dict:
    with localcontext(_CONTEXT):
        report = _fixture(capital, scenario)
        panel_report = None
        if panel_root is not None:
            panel_report = diagnostic_panel_report(panel=load_panel(panel_root), quantities_base=(D(".01"), D(".03"), D(".1")))
            report["panel_diagnostics"] = panel_report
            report["panel_budget_diagnostics"] = _panel_budgets(panel_report, report["math_scenarios"][0]["policy"])
        code_hashes = {m.__name__: sha256(Path(m.__file__).read_bytes()).hexdigest()
                       for m in (contracts, costs, ledger, payoffs, risk, panel_module, replay_module, reporting)}
        code_hashes["offline_cli"] = sha256(Path(__file__).read_bytes()).hexdigest()
        report["identity"] = {
            "schema": "options-p1-offline-identity.v1", "capital": capital, "scenario": scenario,
            "fixture_data": sha256(json.dumps(report["manual_inputs"], sort_keys=True).encode()).hexdigest(), "policy": report["math_scenarios"][0]["policy_hash"],
            "code_hashes": code_hashes, "panel_data": panel_report["hashes"]["data"] if panel_report else None,
            "panel_source_hashes": panel_report.get("hashes") if panel_report else None,
        }
        report["assumptions"] = ["All mathematical receipts and initial capital are fixture data; no real fills or account balance.",
                                  "Policy in manual replay is context only (policy_enforced=false); model preflight is a separate fixture check.",
                                  "Each capital is an independent total balance; platform balances are never combined.",
                                  "Panel displayed prices, fee/FX proxies and local hashes prove neither account eligibility nor reachable execution."]
        report["status"] = "DIAGNOSTIC_ONLY" if panel_report else "MANUAL_MATH_ONLY"
        return report


def _markdown(report: dict) -> str:
    scenario = report["math_scenarios"][0]
    manual = report["manual_inputs"]
    lines = ["# Options P1 offline diagnostics — fixture mathematics", "", *report["assumptions"], "",
             f"Capital: {report['capital']} USDT; scenario: {report['scenario']}; execution authority: NONE.",
             "Actual PnL, paper PnL, Sharpe and capacity: unknown (null). Rankable/live eligible: false.", "",
             f"Manual inputs: strike {manual['strike']}; delivery {manual['delivery_price']}; quantity/leg {manual['option_quantity_native_per_leg']}; premium price {manual['premium_price_per_base']} USDT/base.",
             f"Fees: opening {manual['opening_fee_per_leg']} and settlement {manual['settlement_fee_per_leg']} USDT per leg.",
             f"Mathematical cash delta: {scenario['wallet_cash_delta'][0]['delta']} USDT. Policy enforced in replay: false.",
             f"Separate fixture model preflight: {report['model_preflight']['status']['status']}; execution authority NONE.", "",
             "| Receipt | Kind | Cash USDT | Positions | System claims |", "| --- | --- | ---: | ---: | ---: |"]
    lines.extend(f"| {r['event_id']} | {r['kind']} | {r['cash_USDT']} | {r['positions']} | {r['system_claims']} |" for r in report["cash_trace"])
    panel = report.get("panel_diagnostics")
    if panel:
        lines.extend(["", "Sealed panel assumptions and gaps (price diagnostics only):", f"Input integrity: {panel['input_integrity']}",
                      json.dumps(panel.get("assumptions", []), ensure_ascii=False), json.dumps(panel.get("gaps", []), ensure_ascii=False),
                      f"Panel data hash: {report['identity']['panel_data']}",
                      "NO_TRADE / NOT_EVALUABLE budget rows remain diagnostic; no resizing or account admission."])
    return "\n".join(lines) + "\n"


def _publish(source: Path, destination: Path) -> None:
    """Atomic directory publication with an OS-enforced no-replacement flag."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(destination), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOSYS, "atomic no-replace directory publication unavailable")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline options portfolio diagnostics", allow_abbrev=False)
    parser.add_argument("--panel-root", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--capital", choices=("1000", "3000"), default="1000")
    parser.add_argument("--scenario", choices=("long-call", "straddle"), default="long-call")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    try:
        output = args.output_dir.resolve()
        panel_root = args.panel_root.resolve() if args.panel_root is not None else None
        if panel_root is not None and (output == panel_root or output in panel_root.parents or panel_root in output.parents):
            raise ValueError("SOURCE_OUTPUT_OVERLAP")
        if output.exists() or args.output_dir.is_symlink():
            raise ValueError("OUTPUT_EXISTS")
        report = _report(args.capital, args.scenario, panel_root)
        artifacts = {"report.json": (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
                     "REPORT.md": _markdown(report).encode()}
        identity = {"inputs": report["identity"], "artifacts": {name: {"bytes": len(raw), "sha256": sha256(raw).hexdigest()}
                    for name, raw in artifacts.items()}, "complete": True}
        artifacts["identity.json"] = (json.dumps(identity, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    except (ValueError, TypeError, PanelError, OSError) as error:
        print(f"INVALID_INPUT: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"INTERNAL_FAILURE: {type(error).__name__}", file=sys.stderr)
        return 1
    temporary = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.incomplete-", dir=output.parent))
        for name, raw in artifacts.items():
            with (temporary / name).open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        _publish(temporary, output)
    except Exception as error:
        print(f"INCOMPLETE: {type(error).__name__}; retained={temporary}", file=sys.stderr)
        return 1
    print(f"Offline diagnostic complete: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
