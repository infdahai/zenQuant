"""Offline cost/bias reports with no rankable return or execution authority."""

from collections import defaultdict
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from hashlib import sha256
from itertools import pairwise, permutations
from pathlib import Path

from zenquant.options import contracts, costs, ledger, risk
from zenquant.options.contracts import Observation, canonical_payload, fingerprint, validate_quantity
from zenquant.options.costs import FeePolicy, TimingPolicy, quote_cost, vwap
from zenquant.options.ledger import LedgerEvent, LedgerState, active_risk_native, apply_event, wallet_cash
from zenquant.options.risk import RiskPolicy

from . import panel as panel_module
from .panel import PanelInput, _json, _ns, _payload, canonical_input_hash, input_integrity

_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)
_ZERO, _ONE = Decimal(0), Decimal(1)
_DEFAULT_QUANTITIES = (Decimal(".01"), Decimal(".03"), Decimal(".1"))


def _authority():
    return {
        "schema": "options-p1-diagnostic.v1",
        "diagnostic_only": True,
        "rankable": False,
        "live_eligible": False,
        "execution_authority": False,
        "actual_pnl": None,
        "paper_pnl": None,
        "net_strategy_pnl": None,
        "sharpe": None,
        "capacity": None,
    }


def _decimal_text(value):
    # Conventional leading dot for sub-unit quantity labels; monetary fields are
    # canonical Decimal strings so no binary floats appear in reports.
    text = format(value, "f")
    return text[1:] if text.startswith("0.") else text


def _timing(panel):
    protocol = _json(panel.protocol_json)
    return TimingPolicy(
        max_age_ns=protocol["maximum_pair_quote_age_at_receipt_ms"] * 10**6,
        minimum_source_age_ns=protocol["minimum_allowed_quote_age_at_receipt_ms"] * 10**6,
    )


def _fresh(value, asof, timing):
    return (
        value is not None
        and value.available_ns <= asof
        and value.received_ns <= asof
        and timing.minimum_source_age_ns <= asof - value.source_ns <= timing.max_age_ns
    )


def _groups(panel):
    groups = defaultdict(list)
    for record in panel.records:
        groups[(record.round, record.cell_id)].append(record)
    return groups


def _conversion(group, timing, *, ignore_age=False):
    for family in ("Deribit_inverse", "Deribit_USDC"):
        for record in group:
            if record.family == family and record.index is not None and record.index.value > 0:
                if ignore_age or _fresh(record.index, record.index.available_ns, timing):
                    return record.index
    return None


def _proxy(value, source, name):
    return Observation(
        value=value,
        source_ns=source.source_ns,
        received_ns=source.received_ns,
        available_ns=source.available_ns,
        source_id=name,
        provenance="proxy",
    )


def _fee_policy(panel, record):
    protocol = _json(panel.protocol_json)
    return FeePolicy(
        rate=Decimal(protocol["taker_fee_rate_proxy"]),
        premium_cap=Decimal(protocol["premium_fee_caps"][record.book.spec.venue]),
        fee_currency=record.book.spec.quote_currency,
        provenance="proxy",
    )


def _native_cost(panel, record, q, side, conversion, asof):
    book, spec = record.book, record.book.spec
    if spec.quote_currency == "BTC":
        index = _proxy(_ONE, book, f"BTC/BTC:identity:{book.raw_hash}")
    elif record.index is not None:
        index = record.index
    elif conversion is not None:
        index = _proxy(conversion.value, conversion, f"BTC/{spec.quote_currency}:parity:{conversion.source_id}")
    else:
        index = None
    identity_fx = _proxy(_ONE, book, f"{spec.quote_currency}/{spec.quote_currency}:identity:{book.raw_hash}")
    evidence = quote_cost(
        book=book,
        side=side,
        quantity_native=q / spec.multiplier,
        index_in_quote=index,
        quote_to_report=identity_fx,
        fee_policy=_fee_policy(panel, record),
        timing=_timing(panel),
        asof_ns=asof,
        report_currency=spec.quote_currency,
    )
    return {
        "round": record.round,
        "cell_id": record.cell_id,
        "family": record.family,
        "quantity_base": _decimal_text(q),
        "side": side,
        "index_in_quote": str(index.value) if index is not None else None,
        "index_source": canonical_payload(index),
        "metadata_reference": record.metadata_reference,
        "spec_hash": fingerprint(spec),
        **canonical_payload(evidence),
    }


def _valid_book(book):
    if not book.asks or not book.bids or book.bids[0].price >= book.asks[0].price:
        return False
    for levels, ascending in ((book.asks, True), (book.bids, False)):
        if any(level.price <= 0 for level in levels):
            return False
        if any((b.price <= a.price if ascending else b.price >= a.price) for a, b in pairwise(levels)):
            return False
    return costs._book_status(book) is None


def _price(record, q, side, *, variant=None):
    book, spec = record.book, record.book.spec
    if not _valid_book(book):
        return None
    quantity_native = q / (spec.multiplier if variant != "wrong_min_ctmult" else _ONE)
    if variant != "wrong_min_ctmult" and validate_quantity(spec, quantity_native).status != "VALID":
        return None
    if variant == "mid":
        bid, ask = vwap(book.bids, quantity_native), vwap(book.asks, quantity_native)
        return None if bid is None or ask is None else (bid + ask) / 2
    return vwap(book.asks if side == "BUY" else book.bids, quantity_native)


def _frozen_leg_fee(panel, record, q, premium_usd, conversion, *, variant=None):
    policy = _fee_policy(panel, record)
    fee_index = record.index.value if record.index is not None else conversion.value
    return _ZERO if variant == "ignore_fee" else q * min(policy.rate * fee_index, policy.premium_cap * premium_usd)


def _crosses(panel, quantities, *, variant=None):
    timing, protocol = _timing(panel), _json(panel.protocol_json)
    rows = []
    counts = {
        "cross_size_cases": 0,
        "legal_depth_complete": 0,
        "pair_timing_valid": 0,
        "timing_excluded": 0,
        "gross_positive": 0,
        "fee_proxy_positive": 0,
    }
    valid_pairs = set()
    for identity, group in _groups(panel).items():
        conversion = _conversion(group, timing, ignore_age=variant == "ignore_index_age")
        for buy, sell in permutations(group, 2):
            if buy.book.spec.venue == sell.book.spec.venue:
                continue
            for q in quantities:
                counts["cross_size_cases"] += 1
                key = {
                    "round": identity[0],
                    "cell_id": identity[1],
                    "buy": buy.family,
                    "sell": sell.family,
                    "quantity_base": _decimal_text(q),
                }
                ask, bid = _price(buy, q, "BUY", variant=variant), _price(sell, q, "SELL", variant=variant)
                if conversion is None or ask is None or bid is None:
                    rows.append(
                        {
                            **key,
                            "status": "NOT_EVALUABLE",
                            "reason": "LEGAL_QUANTITY_DEPTH_OR_CONVERSION_MISSING",
                            "gross_usd": None,
                            "fee_proxy_usd": None,
                            "after_fee_proxy_usd": None,
                            "rankable": False,
                        }
                    )
                    continue
                ask_usd = ask * (conversion.value if buy.book.spec.quote_currency == "BTC" else _ONE)
                bid_usd = bid * (conversion.value if sell.book.spec.quote_currency == "BTC" else _ONE)
                gross = (bid_usd - ask_usd) * q
                fees = _frozen_leg_fee(panel, buy, q, ask_usd, conversion, variant=variant) + _frozen_leg_fee(
                    panel, sell, q, bid_usd, conversion, variant=variant
                )
                counts["legal_depth_complete"] += 1
                asof = max(buy.book.available_ns, sell.book.available_ns, conversion.available_ns)
                dependencies = (
                    (buy.book, sell.book) if variant == "ignore_index_age" else (buy.book, sell.book, conversion)
                )
                fresh = all(_fresh(dep, asof, timing) for dep in dependencies)
                pair_gap = (
                    abs(buy.book.source_ns - sell.book.source_ns)
                    <= protocol["maximum_pair_server_timestamp_gap_ms"] * 10**6
                    and abs(buy.book.received_ns - sell.book.received_ns)
                    <= protocol["maximum_pair_local_receive_gap_ms"] * 10**6
                )
                valid = fresh and pair_gap
                counts["pair_timing_valid"] += int(valid)
                counts["timing_excluded"] += int(not valid)
                counts["gross_positive"] += int(valid and gross > 0)
                counts["fee_proxy_positive"] += int(valid and gross - fees > 0)
                if valid:
                    valid_pairs.add((*identity, buy.family, sell.family))
                rows.append(
                    {
                        **key,
                        "status": "VALID" if valid else "NOT_EVALUABLE",
                        "reason": "DISPLAYED_PRICE_DIAGNOSTIC_ONLY" if valid else "JOINT_TIMING_EXCLUDED",
                        "asof_ns": asof,
                        "buy_vwap_usd": ask_usd,
                        "sell_vwap_usd": bid_usd,
                        "gross_usd": gross,
                        "fee_proxy_usd": fees,
                        "after_fee_proxy_usd": gross - fees,
                        "conversion_source": canonical_payload(conversion),
                        "buy_raw_hash": buy.book.raw_hash,
                        "sell_raw_hash": sell.book.raw_hash,
                        "rankable": False,
                    }
                )
    counts["independent_pair_observations"] = len(valid_pairs)
    return counts, rows


def _routing(panel, quantities):
    rows = []
    timing = _timing(panel)
    for (number, cell_id), group in _groups(panel).items():
        conversion = _conversion(group, timing)
        asof = max([r.book.available_ns for r in group] + ([conversion.available_ns] if conversion is not None else []))
        for q in quantities:
            candidates, exclusions = [], []
            for record in group:
                native = _native_cost(panel, record, q, "BUY", conversion, asof)
                if native["status"]["status"] != "VALID" or conversion is None or not _fresh(conversion, asof, timing):
                    exclusions.append(
                        {
                            "family": record.family,
                            "reason": native["status"]["reason"]
                            if native["status"]["status"] != "VALID"
                            else "CONVERSION_AGE",
                        }
                    )
                    continue
                fx = conversion.value if record.book.spec.quote_currency == "BTC" else _ONE
                candidates.append(
                    {
                        "family": record.family,
                        "displayed_buy_cost_usd_proxy": Decimal(native["total_report_currency"]) * fx,
                        "native_input_hash": native["input_hash"],
                    }
                )
            candidates.sort(key=lambda row: (row["displayed_buy_cost_usd_proxy"], row["family"]))
            rows.append(
                {
                    "round": number,
                    "cell_id": cell_id,
                    "quantity_base": _decimal_text(q),
                    "common_asof_ns": asof,
                    "display_cost_order": candidates,
                    "exclusions": exclusions,
                    "rankable": False,
                }
            )
    return {
        "name": "A01_static_display_cost_baseline",
        "deployed": False,
        "execution_savings": None,
        "terminal_payoff_equivalence": False,
        "rows": rows,
    }


def _hashes(panel):
    def code_hash(module):
        return sha256(Path(module.__file__).read_bytes()).hexdigest()

    return {
        "code": fingerprint((code_hash(panel_module), sha256(Path(__file__).read_bytes()).hexdigest())),
        "data": canonical_input_hash(panel),
        "source_references": fingerprint(panel.identities),
        "contracts": code_hash(contracts),
        "ledger": code_hash(ledger),
        "cost": code_hash(costs),
        "risk": code_hash(risk),
        "protocol": sha256(panel.protocol_json.encode("utf-8")).hexdigest(),
        "sealed_entries": panel.sealed_entries,
    }


def diagnostic_panel_report(*, panel: PanelInput, quantities_base: tuple[Decimal, ...]) -> dict:
    if not isinstance(panel, PanelInput) or type(quantities_base) is not tuple or not quantities_base:
        raise TypeError("PanelInput and nonempty quantity tuple required")
    if len(set(quantities_base)) != len(quantities_base) or any(
        not isinstance(q, Decimal) or not q.is_finite() or q <= 0 for q in quantities_base
    ):
        raise ValueError("UNIQUE_POSITIVE_FINITE_DECIMAL_QUANTITIES_REQUIRED")
    with localcontext(_CONTEXT):
        counts, cross_rows = _crosses(panel, quantities_base)
        native_costs, legal_cost_cases, fee_deltas = [], 0, []
        for group in _groups(panel).values():
            conversion = _conversion(group, _timing(panel))
            for record in group:
                for q in quantities_base:
                    # Source-level costs use their own known dependencies; routing
                    # recomputes with the full comparison cohort cutoff below.
                    asof = max(
                        record.book.available_ns,
                        conversion.available_ns if conversion is not None and record.family == "Bybit" else 0,
                    )
                    for side in ("BUY", "SELL"):
                        native = _native_cost(panel, record, q, side, conversion, asof)
                        native_costs.append(native)
                        if native["status"]["status"] == "VALID" and conversion is not None:
                            fx = conversion.value if record.book.spec.quote_currency == "BTC" else _ONE
                            premium_usd = Decimal(native["premium_native"]) * fx / q
                            frozen_fee = _frozen_leg_fee(panel, record, q, premium_usd, conversion)
                            fee_deltas.append(abs(Decimal(native["fee_native"]) * fx - frozen_fee))
                    legal_cost_cases += int(
                        _price(record, q, "BUY") is not None and _price(record, q, "SELL") is not None
                    )
        counts.update(
            requests=panel.requests,
            books=len(panel.books),
            legal_cost_cases=legal_cost_cases,
            source_round_cells=len(_groups(panel)),
            quantity_cases=len(panel.books) * len(quantities_base),
        )
        selection = _json(panel.selection_json)
        cells = [
            {
                "cell_id": cell["id"],
                "selection_dte_label": cell.get("target_DTE"),
                "actual_dte": (Decimal(cell["expiry_ms"]) * 10**6 - _ns(selection["selected_at"]))
                / Decimal(86400 * 10**9),
                "expiry_ns": cell["expiry_ms"] * 10**6,
            }
            for cell in selection.get("cells", [])
        ]
        report = {
            **_authority(),
            "hashes": _hashes(panel),
            "sealed_input_identities": panel.identities,
            "assumptions": panel.assumptions,
            "gaps": panel.gaps,
            "quantity_filter": quantities_base,
            "timing_filter": _timing(panel),
            "counts": counts,
            "cells": cells,
            "native_costs": native_costs,
            "frozen_usd_proxy_cross_diagnostics": cross_rows,
            "native_frozen_fee_comparison": {
                "compared_leg_sides": len(fee_deltas),
                "different_fee_sides": sum(delta != 0 for delta in fee_deltas),
                "maximum_absolute_fee_difference_usd": max(fee_deltas, default=_ZERO),
                "sample_specific": True,
            },
            "cross_domain_note": "Native BTC/BTC fee index=1. Frozen USD proxy fees use own Deribit index/common FX. Agreement here is sample-specific, never general equivalence.",
            "counting_note": "Directed pairs, quantities and rounds correlated; independent_pair_observations removes repeated quantities only, not statistical independence. Cohort routing has separate timing/counting.",
            "static_routing_baseline": _routing(panel, quantities_base),
            "engineering_bias": engineering_bias_report(panel=panel),
            "math_scenarios": [],
            "status": "DIAGNOSTIC_ONLY",
            "input_integrity": input_integrity(panel),
            "verification_binding": panel.verification,
            "verification_note": "Local loader content binding; not exchange-signed authenticity or protection against malicious Python callers.",
            "account_eligibility": "NOT_EVALUABLE",
            "inverse_execution": "UNSUPPORTED",
        }
        return _payload(report)


def engineering_bias_report(*, panel: PanelInput) -> dict:
    with localcontext(_CONTEXT):
        baseline_counts, baseline = _crosses(panel, _DEFAULT_QUANTITIES)
        baseline_map = {
            (r["round"], r["cell_id"], r["buy"], r["sell"], r["quantity_base"]): r
            for r in baseline
            if r["status"] == "VALID"
        }
        variants = []
        for variant in ("mid", "ignore_fee", "wrong_min_ctmult", "ignore_index_age"):
            counts, rows = _crosses(panel, _DEFAULT_QUANTITIES, variant=variant)
            deviations = []
            false_labels = 0
            for row in rows:
                if row["status"] != "VALID":
                    continue
                key = (row["round"], row["cell_id"], row["buy"], row["sell"], row["quantity_base"])
                reference = baseline_map.get(key)
                false_labels += int(
                    row["after_fee_proxy_usd"] > 0 and (reference is None or reference["after_fee_proxy_usd"] <= 0)
                )
                if reference is not None:
                    deviations.append(abs(row["after_fee_proxy_usd"] - reference["after_fee_proxy_usd"]))
            variants.append(
                {
                    "variant": variant,
                    "rankable": False,
                    "diagnostic_only": True,
                    "counts": counts,
                    "false_positive_labels": false_labels,
                    "maximum_absolute_net_cost_bias_usd": max(deviations, default=_ZERO),
                    "execution_authority": False,
                }
            )
        return _payload(
            {
                **_authority(),
                "baseline_counts": baseline_counts,
                "input_integrity": input_integrity(panel),
                "data_hash": canonical_input_hash(panel),
                "variants": variants,
                "scope": "offline assumption transformations only; no opportunity ranking",
            }
        )


def manual_scenario_report(*, initial: LedgerState, events: tuple[LedgerEvent, ...], policy: RiskPolicy) -> dict:
    if (
        not isinstance(initial, LedgerState)
        or type(events) is not tuple
        or not all(isinstance(event, LedgerEvent) for event in events)
        or not isinstance(policy, RiskPolicy)
    ):
        raise TypeError("explicit LedgerState, LedgerEvent tuple and RiskPolicy required")
    with localcontext(_CONTEXT):
        state = initial
        for event in events:
            state = apply_event(state, event)
        wallets = tuple(dict.fromkeys(balance.wallet for balance in initial.balances + state.balances))
        scenario = {
            "initial_hash": fingerprint(initial),
            "event_hash": fingerprint(events),
            "policy_hash": fingerprint(policy),
            "final_hash": fingerprint(state),
            "wallet_cash_delta": [
                {"wallet": wallet, "delta": wallet_cash(state, wallet) - wallet_cash(initial, wallet)}
                for wallet in wallets
            ],
            "active_risk_native": [
                {"wallet": wallet, "amount": active_risk_native(state, wallet)} for wallet in wallets
            ],
            "pending_cash_reserve": [
                {"wallet": reservation.wallet, "amount": reservation.remaining_cash}
                for reservation in state.reservations
            ],
            "frozen_reasons": state.frozen_reasons,
            "unapplied_event_ids": tuple(e.key.event_id for e in state.unapplied),
            "policy": policy,
            "policy_enforced": False,
            "note": "Supplied ledger facts are manual mathematics; policy fingerprint is context, not inferred risk approval or strategy performance. Native wallets not summed across currencies.",
        }
        return _payload({**_authority(), "math_scenarios": [scenario], "status": "MANUAL_MATH_ONLY"})
