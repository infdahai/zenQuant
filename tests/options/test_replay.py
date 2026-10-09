"""Knowledge-time replay and terminal receipts, with hand-reconciled cash."""

from dataclasses import replace
from decimal import Decimal, Inexact, localcontext

import pytest

from zenquant.options.contracts import canonical_payload, fingerprint
from zenquant.options.costs import TimingPolicy
from zenquant.options.ledger import (
    LedgerEvent,
    WalletBalance,
    active_risk_native,
    apply_event,
    initial_state,
    valuation,
    wallet_cash,
)
from zenquant.options.payoffs import settlement_plan
from zenquant.research.options_platform.replay import (
    checkpoint_payload,
    replay,
    restore_checkpoint,
    settlement_to_ledger_events,
)

from .factories import fill_event, key, linear_spec, observation, wallet

D = Decimal
TIMING = TimingPolicy(max_age_ns=100, minimum_source_age_ns=0)


def initial(spec):
    return initial_state(specs=(spec,), balances=(WalletBalance(wallet=wallet(), cash=D("1000")),))


def terminal(spec, quantity=".6", *, fee=".3", n=1000, intent="i1", delivery="150"):
    plan = settlement_plan(
        spec=spec, quantity_native=D(quantity), delivery=observation(delivery, time_ns=1000), expiry_id="expiry"
    )
    return settlement_to_ledger_events(
        plan=plan, key=key(n, event_ns=1000), wallet=wallet(), intent_id=intent, fee_amount=D(fee)
    )


def fee_event(n=3, **changes):
    event = LedgerEvent(
        key=key(n),
        kind="FEE",
        wallet=wallet(),
        instrument_id=None,
        intent_id=None,
        order_id=None,
        sleeve=None,
        quantity_native=None,
        price=None,
        amount=None,
        fee=D("2"),
        fee_wallet=wallet(),
        parent_id=None,
        raw_hash="actual-fee",
    )
    return replace(event, **changes)


def test_known_nav_is_not_exit_cash():
    spec = linear_spec()
    state = apply_event(initial(spec), fill_event(1, spec=spec, quantity="1", price="20", fee="2"))
    value = valuation(
        state=state,
        marks=((spec.instrument_id, observation("25", time_ns=2)),),
        fx=(),
        asof_ns=2,
        timing=TIMING,
        report_currency="USDT",
    )
    assert value.model_nav == D("1003")
    assert value.cash_by_currency == (("USDT", D("978")),)
    assert value.spendable_by_wallet == ((wallet(), D("978")),)


def test_future_claim_retains_risk_until_cash_delivery_and_fee_once():
    spec = linear_spec(settlement_rule="linear_via_future")
    state = replay(
        initial=initial(spec),
        events=(
            fill_event(1, spec=spec, quantity="1", price="20", fee="2"),
            fill_event(2, spec=spec, quantity="-.4", price="30", fee=".4"),
        ),
    )[-1]
    events = terminal(spec)
    assert [e.kind for e in events] == ["EXERCISE_TO_FUTURE", "FUTURE_DELIVERY", "FEE"]
    claim_state, delivered, final = replay(initial=state, events=events)
    assert not claim_state.positions and len(claim_state.claims) == 1
    assert claim_state.claims[0].settlement_amount == D("30")
    assert claim_state.claims[0].future_quantity_base == D(".6")
    assert wallet_cash(claim_state, wallet()) == D("989.6")
    assert claim_state.claims[0].premium_basis == D("12")
    assert claim_state.claims[0].opening_fees_basis == ((wallet(), D("1.2")),)
    assert active_risk_native(claim_state, wallet()) == D("13.2")
    value = valuation(state=claim_state, marks=(), fx=(), asof_ns=1000, timing=TIMING, report_currency="USDT")
    assert value.model_nav == D("1019.6")
    assert value.spendable_by_wallet == ((wallet(), D("989.6")),)
    assert wallet_cash(delivered, wallet()) == D("1019.6")
    assert wallet_cash(final, wallet()) == D("1019.3")
    assert final.claims == final.positions == ()
    assert final.unapplied == ()
    assert active_risk_native(final, wallet()) == 0
    assert apply_event(final, events[-1]) == final


def test_exact_receipt_reprocessing_is_noop_and_new_receipt_dedups():
    spec = linear_spec()
    event = fill_event(1, spec=spec, quantity="1", price="20", fee="2")
    state = apply_event(initial(spec), event)
    assert apply_event(state, event) == state
    new = replace(event, key=replace(event.key, received_ns=2, available_ns=3, journal_sequence=2), raw_hash="retry")
    dedup = apply_event(state, new)
    assert dedup.balances == state.balances and dedup.positions == state.positions
    assert dedup.receipts == (event, new)
    assert dedup.unapplied == ()


def test_native_identity_payload_conflict_is_retained_without_cash():
    spec = linear_spec()
    first = fill_event(1, spec=spec, quantity="1", price="20")
    state = apply_event(initial(spec), first)
    conflict = replace(first, price=D("21"), key=replace(first.key, journal_sequence=2, available_ns=2, received_ns=2))
    changed = apply_event(state, conflict)
    assert changed.balances == state.balances
    assert changed.unapplied == (conflict,)
    assert "EVENT_IDENTITY_CONFLICT" in changed.frozen_reasons


def test_fee_identity_binds_actual_debit_wallet_even_when_attribution_changes():
    spec = linear_spec()
    first = fee_event(wallet=replace(wallet(), account="unknown"))
    state = apply_event(initial(spec), first)
    assert wallet_cash(state, wallet()) == D("998")
    new = replace(first, wallet=wallet(), key=replace(first.key, received_ns=4, available_ns=4, journal_sequence=4))
    changed = apply_event(state, new)
    assert wallet_cash(changed, wallet()) == D("998")
    assert "EVENT_IDENTITY_CONFLICT" in changed.frozen_reasons
    retry = replace(first, key=replace(first.key, received_ns=5, available_ns=5, journal_sequence=5))
    assert wallet_cash(apply_event(changed, retry), wallet()) == D("998")


@pytest.mark.parametrize("field", ["journal_sequence", "available_ns"])
def test_distinct_regressing_receipt_never_changes_cash(field):
    spec = linear_spec()
    state = apply_event(initial(spec), fill_event(5, spec=spec, quantity="1", price="20"))
    event = fill_event(6, spec=spec, quantity="1", price="20")
    event = replace(
        event, key=replace(event.key, **{field: 4, "received_ns": 4} if field == "available_ns" else {field: 4})
    )
    invalid = apply_event(state, event)
    assert invalid.balances == state.balances
    assert invalid.unapplied == (event,)
    assert "NON_MONOTONIC_KNOWLEDGE" in invalid.frozen_reasons


def test_late_event_is_applied_in_knowledge_order_and_latefill_after_cancel_is_actual():
    spec = linear_spec()
    reserve = LedgerEvent(
        key=key(1),
        kind="RESERVE",
        wallet=wallet(),
        instrument_id=spec.instrument_id,
        intent_id="i1",
        order_id="o1",
        sleeve="tail",
        quantity_native=D("1"),
        price=None,
        amount=D("22"),
        fee=D("0"),
        fee_wallet=None,
        parent_id=None,
        raw_hash="r",
    )
    cancel = replace(reserve, key=key(3), kind="CANCEL_CONFIRMED", sleeve=None, quantity_native=None, amount=None)
    latefill = fill_event(4, spec=spec, quantity="1", price="20", event_ns=2)
    final = replay(initial=initial(spec), events=(reserve, cancel, latefill))[-1]
    assert wallet_cash(final, wallet()) == D("980")
    assert final.reservations == () and final.positions[0].quantity_native == 1
    assert [e.key.event_ns for e in final.receipts] == [1, 3, 2]
    assert "UNKNOWN_ORDER" in final.frozen_reasons


@pytest.mark.parametrize("rule", ["linear_cash", "linear_via_future"])
def test_otm_removes_inventory_without_claim_or_new_cash(rule):
    spec = linear_spec(settlement_rule=rule)
    state = apply_event(initial(spec), fill_event(1, spec=spec, quantity=".6", price="16"))
    final = replay(initial=state, events=terminal(spec, delivery="80", fee="0"))[-1]
    assert final.positions == final.claims == ()
    assert wallet_cash(final, wallet()) == D("990.4")


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"quantity_native": D(".5")}, "SETTLEMENT_QUANTITY_MISMATCH"),
        ({"parent_id": None}, "MISSING_EVENT_PAYLOAD"),
        ({"key": key(999)}, "SETTLEMENT_BEFORE_EXPIRY"),
    ],
)
def test_invalid_exercise_preserves_actual_inventory(change, reason):
    spec = linear_spec(settlement_rule="linear_via_future")
    state = apply_event(initial(spec), fill_event(1, spec=spec, quantity=".6", price="16"))
    bad = replace(terminal(spec)[0], **change)
    result = apply_event(state, bad)
    assert result.balances == state.balances and result.positions == state.positions
    assert result.unapplied == (bad,) and reason in result.frozen_reasons


def test_delivery_requires_exact_link_and_value_and_cannot_form_user_position():
    spec = linear_spec(settlement_rule="linear_via_future")
    state = apply_event(initial(spec), fill_event(1, spec=spec, quantity=".6", price="16"))
    exercise, delivery, _ = terminal(spec)
    missing = apply_event(state, delivery)
    assert missing.claims == () and missing.positions == state.positions
    assert "UNKNOWN_SYSTEM_CLAIM" in missing.frozen_reasons
    claim = apply_event(state, exercise)
    for changes in (
        {"parent_id": "other-expiry"},
        {"intent_id": "i2"},
        {"amount": D("31")},
        {"quantity_native": D(".5")},
    ):
        bad = replace(delivery, **changes)
        result = apply_event(claim, bad)
        assert result.balances == claim.balances and result.claims == claim.claims
        assert result.unapplied
    final = apply_event(claim, delivery)
    retry = replace(delivery, key=key(1002), raw_hash="new-delivery-id")
    assert apply_event(final, retry).balances == final.balances
    assert final.positions == ()


def test_equal_inventory_two_intents_has_independent_claims_and_fees():
    spec = linear_spec(settlement_rule="linear_via_future")
    state = replay(
        initial=initial(spec),
        events=(
            fill_event(1, spec=spec, quantity="1", price="20", fee="2"),
            fill_event(2, spec=spec, quantity="-.4", price="30", fee=".4"),
            fill_event(3, spec=spec, quantity="1", price="20", fee="2", intent_id="i2", order_id="o2"),
            fill_event(4, spec=spec, quantity="-.4", price="30", fee=".4", intent_id="i2", order_id="o2"),
        ),
    )[-1]
    one = terminal(spec, n=1000)
    two = terminal(spec, n=1003, intent="i2")
    assert one[0].parent_id != two[0].parent_id
    state = replay(initial=state, events=(one[0], two[0]))[-1]
    assert len(state.claims) == 2 and active_risk_native(state, wallet()) == D("26.4")
    # Re-sequence the remainder to keep the journal causal.
    remainder = tuple(
        replace(e, key=replace(e.key, journal_sequence=1004 + i, available_ns=1004 + i, received_ns=1004 + i))
        for i, e in enumerate((*one[1:], *two[1:]))
    )
    final = replay(initial=state, events=remainder)[-1]
    assert wallet_cash(final, wallet()) == D("1038.6")
    assert final.positions == final.claims == final.unapplied == ()
    assert active_risk_native(final, wallet()) == 0


def test_partial_close_subminimum_residual_settles_and_preserves_fill_anomaly():
    spec = linear_spec(min_quantity_native=D(".1"))
    state = replay(
        initial=initial(spec),
        events=(
            fill_event(1, spec=spec, quantity=".1", price="20"),
            fill_event(2, spec=spec, quantity="-.09", price="25"),
        ),
    )[-1]
    assert "INVALID_FILL_QUANTITY" in state.frozen_reasons
    final = replay(initial=state, events=terminal(spec, quantity=".01", fee="0"))[-1]
    assert final.positions == final.claims == final.unapplied == ()
    assert wallet_cash(final, wallet()) == D("1000.75")
    assert "INVALID_FILL_QUANTITY" in final.frozen_reasons


def test_adapter_rejects_missing_delivery_and_future_knowledge():
    spec = linear_spec()
    plan = settlement_plan(spec=spec, quantity_native=D("1"), delivery=observation("150", time_ns=1000), expiry_id="e")
    for bad_plan, event_key in (
        (replace(plan, delivery=None), key(1000)),
        (replace(plan, delivery=replace(plan.delivery, available_ns=1005)), key(1000)),
    ):
        with pytest.raises(ValueError):
            settlement_to_ledger_events(
                plan=bad_plan, key=event_key, wallet=wallet(), intent_id="i1", fee_amount=D("0")
            )


@pytest.mark.parametrize("variant", ["missing", "future", "stale"])
def test_unknown_model_mark_is_not_zero_nav(variant):
    spec = linear_spec()
    state = apply_event(initial(spec), fill_event(1, spec=spec, quantity="1", price="20"))
    marks = (
        ()
        if variant == "missing"
        else ((spec.instrument_id, observation("25", time_ns=3 if variant == "future" else 0)),)
    )
    value = valuation(
        state=state, marks=marks, fx=(), asof_ns=2 if variant != "stale" else 101, timing=TIMING, report_currency="USDT"
    )
    assert value.model_nav is None and value.cash_by_currency == (("USDT", D("980")),)


def test_fx_required_and_unapplied_forces_unknown_nav():
    spec = linear_spec()
    state = initial_state(specs=(spec,), balances=(WalletBalance(wallet=wallet("BTC"), cash=D("2")),))
    assert valuation(state=state, marks=(), fx=(), asof_ns=2, timing=TIMING, report_currency="USDT").model_nav is None
    assert valuation(
        state=state,
        marks=(),
        fx=(("BTC", observation("100", time_ns=2)),),
        asof_ns=2,
        timing=TIMING,
        report_currency="USDT",
    ).model_nav == D("200")
    invalid = apply_event(state, fee_event())
    assert (
        valuation(
            state=invalid,
            marks=(),
            fx=(("BTC", observation("100", time_ns=3)),),
            asof_ns=3,
            timing=TIMING,
            report_currency="USDT",
        ).model_nav
        is None
    )


def test_checkpoint_split_replay_hash_matches_every_suffix_state():
    spec = linear_spec(settlement_rule="linear_via_future")
    seed = initial(spec)
    events = (fill_event(1, spec=spec, quantity=".6", price="16", fee=".8"), *terminal(spec))
    states = replay(initial=seed, events=events)
    payload = checkpoint_payload(initial=seed, events=events[:2], expected_state=states[1])
    restored = restore_checkpoint(payload)
    assert fingerprint(restored) == fingerprint(states[1])
    assert tuple(map(fingerprint, replay(initial=restored, events=events[2:]))) == tuple(map(fingerprint, states[2:]))
    assert canonical_payload(restored) == canonical_payload(states[1])


@pytest.mark.parametrize(
    "variant", ["truncated", "hash", "unknown", "float", "bool", "nested_unknown", "duplicate_wallet", "duplicate_spec"]
)
def test_checkpoint_corruption_is_explicitly_invalid(variant):
    spec = linear_spec()
    seed = initial(spec)
    events = (fill_event(1, spec=spec, quantity="1", price="20"),)
    payload = checkpoint_payload(initial=seed, events=events, expected_state=replay(initial=seed, events=events)[-1])
    if variant == "truncated":
        payload["events"] = []
    elif variant == "hash":
        payload["expected_state_hash"] = "wrong"
    elif variant == "unknown":
        payload["execute"] = "anything"
    elif variant == "float":
        payload["events"][0]["price"] = 20.0
    elif variant == "bool":
        payload["events"][0]["key"]["journal_sequence"] = True
    elif variant == "nested_unknown":
        payload["events"][0]["key"]["extra"] = 1
    elif variant == "duplicate_wallet":
        payload["initial"]["balances"].append(payload["initial"]["balances"][0])
    elif variant == "duplicate_spec":
        payload["initial"]["specs"].append(payload["initial"]["specs"][0])
    with pytest.raises(ValueError, match=r"^INVALID_CHECKPOINT$"):
        restore_checkpoint(payload)


def test_fixed_decimal_context_for_replay_valuation_and_checkpoint():
    spec = linear_spec()
    seed = initial(spec)
    event = fill_event(1, spec=spec, quantity=".3", price="12.3456789")
    normal = apply_event(seed, event)
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        state = apply_event(seed, event)
        value = valuation(
            state=state,
            marks=((spec.instrument_id, observation("25.1234567", time_ns=2)),),
            fx=(),
            asof_ns=2,
            timing=TIMING,
            report_currency="USDT",
        )
        assert restore_checkpoint(checkpoint_payload(initial=seed, events=(event,), expected_state=state)) == normal
    assert state == normal and value.model_nav == D("1003.83333334")


def test_raw_json_duplicate_object_keys_and_nonfinite_are_invalid():
    from json import dumps

    spec = linear_spec()
    seed = initial(spec)
    payload = checkpoint_payload(initial=seed, events=(), expected_state=seed)
    raw = dumps(payload)
    assert restore_checkpoint(raw) == seed
    duplicate = raw.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    with pytest.raises(ValueError, match=r"^INVALID_CHECKPOINT$"):
        restore_checkpoint(duplicate)
    with pytest.raises(ValueError, match=r"^INVALID_CHECKPOINT$"):
        restore_checkpoint(raw.replace('"cash": "1000"', '"cash": NaN'))


def test_retransmission_does_not_break_parent_opening_fee_provenance():
    spec = linear_spec()
    fill = fill_event(1, spec=spec, quantity="1", price="20")
    retry = replace(fill, key=replace(fill.key, received_ns=2, available_ns=2, journal_sequence=2), raw_hash="retry")
    fee = fee_event(instrument_id=spec.instrument_id, intent_id="i1", parent_id=fill.key.event_id)
    state = replay(initial=initial(spec), events=(fill, retry, fee))[-1]
    assert wallet_cash(state, wallet()) == D("978")
    assert active_risk_native(state, wallet()) == D("22")
    assert state.unapplied == ()


def test_same_link_fee_with_changed_attribution_cannot_repeat_debit():
    spec = linear_spec()
    state = apply_event(initial(spec), fill_event(1, spec=spec, quantity=".6", price="16"))
    events = terminal(spec)
    final = replay(initial=state, events=events)[-1]
    fee = replace(events[-1], key=key(1005), instrument_id="wrong-instrument", raw_hash="retry")
    changed = apply_event(final, fee)
    assert wallet_cash(changed, wallet()) == wallet_cash(final, wallet())
    assert changed.unapplied == (fee,)


def test_cross_kind_settlement_cannot_reuse_completed_link_for_late_inventory():
    spec = linear_spec()
    state = apply_event(initial(spec), fill_event(1, spec=spec, quantity=".6", price="16"))
    expiry = terminal(spec, delivery="80", fee="0")[0]
    state = apply_event(state, expiry)
    latefill = fill_event(1001, spec=spec, quantity=".6", price="16", event_ns=999)
    state = apply_event(state, latefill)
    fake = replace(terminal(spec, n=1002, fee="0")[0], parent_id=expiry.parent_id)
    result = apply_event(state, fake)
    assert result.balances == state.balances and result.positions == state.positions
    assert result.unapplied == (fake,)


def test_distinct_opening_fee_receipts_share_parent_without_becoming_duplicates():
    spec = linear_spec()
    fill = fill_event(1, spec=spec, quantity="1", price="20")
    one = fee_event(n=2, instrument_id=spec.instrument_id, intent_id="i1", parent_id=fill.key.event_id)
    two = fee_event(n=3, instrument_id=spec.instrument_id, intent_id="i1", parent_id=fill.key.event_id)
    state = replay(initial=initial(spec), events=(fill, one, two))[-1]
    assert wallet_cash(state, wallet()) == D("976")
    assert active_risk_native(state, wallet()) == D("24")
    assert state.unapplied == ()


def test_different_native_fee_id_for_same_settlement_is_unresolved_evidence():
    spec = linear_spec()
    state = apply_event(initial(spec), fill_event(1, spec=spec, quantity=".6", price="16"))
    events = terminal(spec)
    final = replay(initial=state, events=events)[-1]
    fee = replace(events[-1], key=replace(events[-1].key, event_id="other-fee", journal_sequence=1002))
    changed = apply_event(final, fee)
    assert changed.balances == final.balances
    assert changed.unapplied == (fee,)
    assert "SETTLEMENT_LINK_CONFLICT" in changed.frozen_reasons


def test_zero_delivery_put_has_signed_system_claim_and_positive_cash():
    spec = linear_spec(option_type="P", settlement_rule="linear_via_future")
    state = apply_event(initial(spec), fill_event(1, spec=spec, quantity=".6", price="16"))
    exercise, delivery = terminal(spec, delivery="0", fee="0")
    claim = apply_event(state, exercise)
    assert claim.positions == () and claim.unapplied == ()
    assert claim.claims[0].future_quantity_base == D("-.6")
    assert claim.claims[0].settlement_amount == D("60")
    assert active_risk_native(claim, wallet()) == D("9.6")
    final = apply_event(claim, delivery)
    assert wallet_cash(final, wallet()) == D("1050.4")
    assert final.positions == final.claims == final.unapplied == ()


def test_failed_delivery_retransmission_cannot_block_valid_exercise_and_new_delivery():
    spec = linear_spec(settlement_rule="linear_via_future")
    state = replay(
        initial=initial(spec),
        events=(
            fill_event(1, spec=spec, quantity="1", price="20", fee="2"),
            fill_event(2, spec=spec, quantity="-.4", price="30", fee=".4"),
        ),
    )[-1]
    exercise, delivery = terminal(spec, fee="0")
    failed = apply_event(state, delivery)
    retry = replace(
        delivery,
        key=replace(delivery.key, journal_sequence=1002, received_ns=1002, available_ns=1002),
        raw_hash="failed-retry",
    )
    duplicate = apply_event(failed, retry)
    assert duplicate.balances == failed.balances and duplicate.unapplied == (delivery,)
    exercise = replace(exercise, key=replace(exercise.key, journal_sequence=1003, received_ns=1003, available_ns=1003))
    claim = apply_event(duplicate, exercise)
    assert claim.positions == () and len(claim.claims) == 1
    assert active_risk_native(claim, wallet()) == D("13.2")
    assert wallet_cash(claim, wallet()) == D("989.6")
    confirmed = replace(
        delivery,
        key=replace(
            delivery.key, event_id="confirmed-delivery", journal_sequence=1004, received_ns=1004, available_ns=1004
        ),
    )
    final = apply_event(claim, confirmed)
    assert final.positions == final.claims == ()
    assert wallet_cash(final, wallet()) == D("1019.6")
    assert final.unapplied == (delivery,)
    assert "UNKNOWN_SYSTEM_CLAIM" in final.frozen_reasons
    assert (
        restore_checkpoint(
            checkpoint_payload(initial=state, events=(delivery, retry, exercise, confirmed), expected_state=final)
        )
        == final
    )


def test_fee_after_failed_settlement_retransmission_remains_unresolved_and_debits_once():
    spec = linear_spec(settlement_rule="linear_via_future")
    state = replay(
        initial=initial(spec),
        events=(
            fill_event(1, spec=spec, quantity="1", price="20", fee="2"),
            fill_event(2, spec=spec, quantity="-.4", price="30", fee=".4"),
        ),
    )[-1]
    _, delivery, fee = terminal(spec)
    failed = apply_event(state, delivery)
    retry = replace(
        delivery,
        key=replace(delivery.key, journal_sequence=1002, received_ns=1002, available_ns=1002),
        raw_hash="failed-retry",
    )
    duplicate = apply_event(failed, retry)
    fee = replace(fee, key=replace(fee.key, journal_sequence=1003, received_ns=1003, available_ns=1003))
    charged = apply_event(duplicate, fee)
    assert wallet_cash(charged, wallet()) == D("989.3")
    assert charged.unapplied == (delivery, fee)
    assert "UNRESOLVED_FEE_BASIS_ATTRIBUTION" in charged.frozen_reasons
    assert active_risk_native(charged, wallet()) == D("13.2")
    fee_retry = replace(
        fee, key=replace(fee.key, journal_sequence=1004, received_ns=1004, available_ns=1004), raw_hash="fee-retry"
    )
    retransmitted = apply_event(charged, fee_retry)
    assert wallet_cash(retransmitted, wallet()) == D("989.3")
    assert retransmitted.unapplied == (delivery, fee)
    assert retransmitted.positions == charged.positions


def test_failed_reduction_retransmission_cannot_invent_lot_reduction_provenance():
    spec = linear_spec()
    fill = fill_event(1, spec=spec, quantity="1", price="20")
    oversell = fill_event(2, spec=spec, quantity="-2", price="30")
    retry = replace(
        oversell,
        key=replace(oversell.key, journal_sequence=3, received_ns=3, available_ns=3),
        raw_hash="oversell-retry",
    )
    fee = fee_event(n=4, instrument_id=spec.instrument_id, intent_id="i1", parent_id=fill.key.event_id)
    final = replay(initial=initial(spec), events=(fill, oversell, retry, fee))[-1]
    assert wallet_cash(final, wallet()) == D("978")
    assert active_risk_native(final, wallet()) == D("22")
    assert final.positions[0].quantity_native == D("1")
    assert final.unapplied == (oversell,)
    assert "EXIT_EXCEEDS_LONG" in final.frozen_reasons
