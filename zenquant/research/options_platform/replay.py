"""Deterministic receipt replay and strict safe-data checkpoints.

Restore re-reduces every knowledge-time receipt. Pass raw JSON text to check
original duplicate object keys; an already-decoded dict has lost that evidence.
There is no file/network access, executable serialization, or current-time read.
"""

from dataclasses import fields, replace
from decimal import Decimal
from json import loads

from zenquant.options.contracts import (
    EventKey,
    InstrumentSpec,
    WalletKey,
    _decimal,
    _text,
    _tuple,
    canonical_payload,
    fingerprint,
)
from zenquant.options.ledger import LedgerEvent, LedgerState, WalletBalance, apply_event, initial_state
from zenquant.options.payoffs import SettlementPlan


def replay(*, initial: LedgerState, events: tuple[LedgerEvent, ...]) -> tuple[LedgerState, ...]:
    if not isinstance(initial, LedgerState):
        raise TypeError("initial must be LedgerState")
    _tuple(events, "events", LedgerEvent)
    states, state = [], initial
    for event in events:
        state = apply_event(state, event)
        states.append(state)
    return tuple(states)


def settlement_to_ledger_events(
    *, plan: SettlementPlan, key: EventKey, wallet: WalletKey, intent_id: str, fee_amount: Decimal
) -> tuple[LedgerEvent, ...]:
    """Adapt retained mathematical fixing provenance into linked receipts.

    Plans do not grant official fixing authenticity or trading permission. The
    allocation link includes wallet/intent: equal lots can share a model plan.
    """
    if not isinstance(plan, SettlementPlan) or not isinstance(key, EventKey) or not isinstance(wallet, WalletKey):
        raise TypeError("settlement adapter types mismatch")
    _text(intent_id, "intent_id")
    fee = _decimal(fee_amount, "fee_amount", nonnegative=True)
    if plan.status.status != "VALID" or plan.delivery is None or not plan.actions:
        raise ValueError("INVALID_SETTLEMENT_PLAN")
    if key.available_ns < plan.delivery.available_ns:
        raise ValueError("SETTLEMENT_NOT_YET_AVAILABLE")
    actions = plan.actions
    kinds = tuple(a.kind for a in actions)
    if kinds not in (("EXPIRY",), ("CASH_SETTLEMENT",), ("EXERCISE_TO_FUTURE", "FUTURE_DELIVERY")):
        raise ValueError("INVALID_SETTLEMENT_PLAN")
    first = actions[0]
    if any(
        (a.option_id, a.currency, a.link_id, a.quantity_native)
        != (first.option_id, first.currency, first.link_id, first.quantity_native)
        for a in actions
    ):
        raise ValueError("INVALID_SETTLEMENT_PLAN")
    if first.currency != wallet.currency:
        raise ValueError("SETTLEMENT_CURRENCY_MISMATCH")
    if kinds == ("EXERCISE_TO_FUTURE", "FUTURE_DELIVERY"):
        if (
            first.amount != 0
            or first.future_quantity_base == 0
            or actions[1].future_quantity_base != first.future_quantity_base
        ):
            raise ValueError("INVALID_SETTLEMENT_PLAN")
    elif first.future_quantity_base != 0 or (first.kind == "EXPIRY" and first.amount != 0):
        raise ValueError("INVALID_SETTLEMENT_PLAN")
    link = fingerprint((first.link_id, wallet, intent_id))
    events = []
    for index, action in enumerate(actions):
        event_key = replace(
            key, event_id=f"{key.event_id}:{index}:{action.kind}", journal_sequence=key.journal_sequence + index
        )
        events.append(
            LedgerEvent(
                key=event_key,
                kind=action.kind,
                wallet=wallet,
                instrument_id=action.option_id,
                intent_id=intent_id,
                order_id=None,
                sleeve=None,
                quantity_native=action.quantity_native,
                price=plan.delivery.value if action.kind == "EXERCISE_TO_FUTURE" else None,
                amount=actions[1].amount if action.kind == "EXERCISE_TO_FUTURE" else action.amount,
                fee=Decimal("0"),
                fee_wallet=None,
                parent_id=link,
                raw_hash=fingerprint((plan, action, wallet, intent_id)),
            )
        )
    if fee > 0:
        index = len(actions)
        events.append(
            LedgerEvent(
                key=replace(key, event_id=f"{key.event_id}:{index}:FEE", journal_sequence=key.journal_sequence + index),
                kind="FEE",
                wallet=wallet,
                instrument_id=first.option_id,
                intent_id=intent_id,
                order_id=None,
                sleeve=None,
                quantity_native=None,
                price=None,
                amount=None,
                fee=fee,
                fee_wallet=wallet,
                parent_id=link,
                raw_hash=fingerprint((plan, fee, wallet, intent_id)),
            )
        )
    return tuple(events)


def checkpoint_payload(*, initial: LedgerState, events: tuple[LedgerEvent, ...], expected_state: LedgerState) -> dict:
    """Emit JSON-safe source data, never a deserialized final state shortcut."""
    if not isinstance(initial, LedgerState) or not isinstance(expected_state, LedgerState):
        raise ValueError("INVALID_CHECKPOINT")
    _tuple(events, "events", LedgerEvent)
    seed = initial_state(specs=initial.specs, balances=initial.initial_balances)
    prefix = replay(initial=seed, events=initial.receipts)
    actual_initial = prefix[-1] if prefix else seed
    if actual_initial != initial:
        raise ValueError("INVALID_CHECKPOINT")
    actual = replay(initial=initial, events=events)
    final = actual[-1] if actual else initial
    if final != expected_state:
        raise ValueError("INVALID_CHECKPOINT")
    payload = {
        "schema_version": 1,
        "initial": {"specs": canonical_payload(initial.specs), "balances": canonical_payload(initial.initial_balances)},
        "events": canonical_payload((*initial.receipts, *events)),
        "expected_state_hash": fingerprint(expected_state),
    }
    # The writer guarantees its own output conforms to the same strict schema.
    restore_checkpoint(payload)
    return payload


def _object(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError("INVALID_CHECKPOINT")
    return dict(value)


def _record(value, kind):
    return _object(value, (f.name for f in fields(kind)))


def _array(value):
    if type(value) is not list:
        raise ValueError("INVALID_CHECKPOINT")
    return value


def _amount(value):
    # Canonical monetary data are strings, never JSON numbers or booleans.
    if type(value) is not str:
        raise ValueError("INVALID_CHECKPOINT")
    return _decimal(value, "checkpoint amount")


def _wallet(value):
    return WalletKey(**_record(value, WalletKey))


def _balance(value):
    data = _record(value, WalletBalance)
    data["wallet"], data["cash"] = _wallet(data["wallet"]), _amount(data["cash"])
    return WalletBalance(**data)


def _spec(value):
    data = _record(value, InstrumentSpec)
    for name in ("strike", "multiplier", "min_quantity_native", "quantity_step_native"):
        data[name] = _amount(data[name])
    if data["max_quantity_native"] is not None:
        data["max_quantity_native"] = _amount(data["max_quantity_native"])
    bands = []
    for band in _array(data["tick_bands"]):
        if len(_array(band)) != 2:
            raise ValueError("INVALID_CHECKPOINT")
        bands.append(tuple(_amount(v) for v in band))
    data["tick_bands"] = tuple(bands)
    return InstrumentSpec(**data)


def _event(value):
    data = _record(value, LedgerEvent)
    data["key"] = EventKey(**_record(data["key"], EventKey))
    data["wallet"] = _wallet(data["wallet"])
    if data["fee_wallet"] is not None:
        data["fee_wallet"] = _wallet(data["fee_wallet"])
    for name in ("quantity_native", "price", "amount", "fee"):
        if data[name] is not None:
            data[name] = _amount(data[name])
    return LedgerEvent(**data)


def _unique_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("INVALID_CHECKPOINT")
        result[name] = value
    return result


def _reject_json_number(value):
    raise ValueError("INVALID_CHECKPOINT")


def restore_checkpoint(payload: dict | str) -> LedgerState:
    """Rebuild or raise INVALID_CHECKPOINT; malformed input is never a state."""
    try:
        if type(payload) is str:
            payload = loads(
                payload,
                object_pairs_hook=_unique_pairs,
                parse_float=_reject_json_number,
                parse_constant=_reject_json_number,
            )
        data = _object(payload, ("schema_version", "initial", "events", "expected_state_hash"))
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("INVALID_CHECKPOINT")
        _text(data["expected_state_hash"], "expected_state_hash")
        seed = _object(data["initial"], ("specs", "balances"))
        specs = tuple(_spec(s) for s in _array(seed["specs"]))
        if len({fingerprint(s) for s in specs}) != len(specs):
            raise ValueError("INVALID_CHECKPOINT")
        initial = initial_state(specs=specs, balances=tuple(_balance(b) for b in _array(seed["balances"])))
        states = replay(initial=initial, events=tuple(_event(e) for e in _array(data["events"])))
        final = states[-1] if states else initial
        if fingerprint(final) != data["expected_state_hash"]:
            raise ValueError("INVALID_CHECKPOINT")
        return final
    except (TypeError, ValueError, KeyError, ArithmeticError, RecursionError) as error:
        raise ValueError("INVALID_CHECKPOINT") from error
