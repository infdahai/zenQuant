"""Hand reconciliations for actual funded-long option inventory."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal as D
from decimal import Inexact, localcontext

import pytest

from zenquant.options.ledger import (
    EVENT_SCHEMAS,
    LedgerEvent,
    WalletBalance,
    active_risk_native,
    apply_event,
    initial_state,
    wallet_cash,
)

from .factories import fill_event, key, linear_spec, wallet


def funded(*, spec=None, cash="1000", extra=()):
    spec = spec if spec is not None else linear_spec()
    return initial_state(
        specs=(spec,), balances=(WalletBalance(wallet=wallet(spec.quote_currency, spec.venue), cash=cash),) + extra
    )


def reserve(n=1, *, amount="22", quantity="1", intent_id="i1", order_id="o1"):
    return LedgerEvent(
        key=key(n),
        kind="RESERVE",
        wallet=wallet(),
        instrument_id=None,
        intent_id=intent_id,
        order_id=order_id,
        sleeve="tail",
        quantity_native=quantity,
        price=None,
        amount=amount,
        fee="0",
        fee_wallet=None,
        parent_id=None,
        raw_hash=f"reserve-{n}",
    )


def test_partial_close_keeps_exact_remaining_basis():
    spec, w = linear_spec(), wallet()
    state = apply_event(funded(), fill_event(1, spec=spec, quantity="1", price="20", fee="2"))
    assert wallet_cash(state, w) == D("978")
    state = apply_event(state, fill_event(2, spec=spec, quantity="-.4", price="30", fee=".4"))
    assert wallet_cash(state, w) == D("989.6")
    assert state.positions[0].quantity_native == D(".6")
    assert state.positions[0].premium_basis == D("12")
    assert state.positions[0].opening_fees_basis == ((w, D("1.2")),)
    assert active_risk_native(state, w) == D("13.2")


def test_reservation_is_not_cash_and_partial_fill_releases_only_actual_spend():
    spec = linear_spec()
    state = apply_event(funded(), reserve(amount="22", quantity="1"))
    assert wallet_cash(state, wallet()) == D("1000")
    state = apply_event(state, fill_event(2, spec=spec, quantity=".4", price="20", fee=".8"))
    assert wallet_cash(state, wallet()) == D("991.2")
    assert state.reservations[0].remaining_cash == D("13.2")
    assert state.reservations[0].remaining_quantity_native == D(".6")
    assert active_risk_native(state, wallet()) == D("8.8")
    assert not state.frozen_reasons


def test_two_intents_same_contract_have_separate_basis():
    spec = linear_spec()
    state = apply_event(funded(), fill_event(1, spec=spec, quantity="1", price="20"))
    state = apply_event(state, fill_event(2, spec=spec, quantity="2", price="30", intent_id="i2", order_id="o2"))
    state = apply_event(state, fill_event(3, spec=spec, quantity="-.4", price="40"))
    assert [(p.intent_id, p.quantity_native, p.premium_basis) for p in state.positions] == [
        ("i1", D(".6"), D("12")),
        ("i2", D("2"), D("60")),
    ]


def test_fee_currency_remains_in_original_wallet_basis():
    spec, fw = linear_spec(), wallet("BTC")
    state = funded(extra=(WalletBalance(wallet=fw, cash="1"),))
    state = apply_event(state, fill_event(1, spec=spec, quantity="1", price="20", fee=".01", fee_wallet=fw))
    assert wallet_cash(state, wallet()) == D("980")
    assert wallet_cash(state, fw) == D(".99")
    assert state.positions[0].opening_fees_basis == ((fw, D(".01")),)
    assert active_risk_native(state, wallet()) == D("20")
    assert active_risk_native(state, fw) == D(".01")
    state = apply_event(state, fill_event(2, spec=spec, quantity="-.5", price="30", fee=".001", fee_wallet=fw))
    assert wallet_cash(state, wallet()) == D("995")
    assert wallet_cash(state, fw) == D(".989")
    assert active_risk_native(state, fw) == D(".005")


def test_negative_actual_cash_is_recorded_and_freezes():
    spec = linear_spec()
    state = apply_event(funded(cash="1"), fill_event(1, spec=spec, quantity="1", price="20", fee="2"))
    assert wallet_cash(state, wallet()) == D("-21")
    assert state.positions[0].quantity_native == D("1")
    assert "NEGATIVE_CASH" in state.frozen_reasons
    assert not state.unapplied


def test_budget_overrun_records_actual_fill_and_freezes():
    spec = linear_spec()
    state = apply_event(funded(), reserve(amount="10", quantity="1"))
    state = apply_event(state, fill_event(2, spec=spec, quantity="1", price="20", fee="2"))
    assert wallet_cash(state, wallet()) == D("978")
    assert not state.reservations
    assert "RESERVATION_OVERRUN" in state.frozen_reasons


def test_cancel_confirmation_and_late_fill_are_accounted():
    spec = linear_spec()
    state = apply_event(funded(), reserve())
    cancel = replace(reserve(2), kind="CANCEL_CONFIRMED", quantity_native=None, amount=None, sleeve=None)
    state = apply_event(state, cancel)
    assert not state.reservations
    state = apply_event(state, fill_event(3, spec=spec, quantity="1", price="20", fee="2", event_ns=1))
    assert wallet_cash(state, wallet()) == D("978")
    assert "UNKNOWN_ORDER" in state.frozen_reasons
    assert state.receipts[-1].key.event_ns == 1


@pytest.mark.parametrize(
    "change",
    [
        {"instrument_id": "UNKNOWN"},
        {"quantity_native": D("-2")},
        {"quantity_native": D("0")},
        {"price": D("-1")},
        {"fee": D("-1")},
        {"fee_wallet": None},
        {"amount": D("1")},
    ],
)
def test_unresolved_actual_receipt_is_retained_without_inventing_inventory(change):
    spec = linear_spec()
    event = replace(fill_event(1, spec=spec, quantity="1", price="20", fee="2"), **change)
    state = apply_event(funded(), event)
    assert state.receipts == (event,)
    assert state.unapplied == (event,)
    assert state.frozen_reasons
    assert wallet_cash(state, wallet()) == D("1000")
    assert not state.positions


@pytest.mark.parametrize(
    "changes",
    [
        {"exercise_style": "american"},
        {"settlement_currency": "USDC"},
        {"product": "inverse_option", "settlement_rule": "inverse_via_future"},
    ],
)
def test_unsupported_actual_instrument_is_not_treated_as_zero_liability(changes):
    spec = replace(linear_spec(), **changes)
    event = fill_event(1, spec=spec, quantity="1", price="20")
    state = apply_event(funded(spec=spec), event)
    assert state.unapplied == (event,)
    assert "UNSUPPORTED_INSTRUMENT" in state.frozen_reasons


def test_same_native_id_on_two_venues_resolves_by_wallet_venue():
    a, b = linear_spec(), linear_spec(venue="OTHER")
    state = initial_state(specs=(a, b), balances=(WalletBalance(wallet=wallet(), cash="1000"),))
    state = apply_event(state, fill_event(1, spec=a, quantity="1", price="20"))
    assert wallet_cash(state, wallet()) == D("980")
    assert not state.unapplied


def test_ambiguous_same_venue_spec_is_not_resolved_by_latest_version():
    spec = linear_spec()
    state = initial_state(
        specs=(spec, replace(spec, rule_version="v2")), balances=(WalletBalance(wallet=wallet(), cash="1000"),)
    )
    event = fill_event(1, spec=spec, quantity="1", price="20")
    state = apply_event(state, event)
    assert state.unapplied == (event,)
    assert "AMBIGUOUS_INSTRUMENT" in state.frozen_reasons


def test_contract_quantity_is_used_for_premium_not_option_notional():
    spec = linear_spec(quantity_unit="contracts", multiplier=D(".01"), price_unit="quote_per_base")
    state = apply_event(funded(spec=spec), fill_event(1, spec=spec, quantity="2", price="20"))
    assert wallet_cash(state, wallet()) == D("999.6")
    assert state.positions[0].premium_basis == D(".4")


def test_full_close_removes_position_without_charging_opening_fee_again():
    spec = linear_spec()
    state = apply_event(funded(), fill_event(1, spec=spec, quantity="1", price="20", fee="2"))
    state = apply_event(state, fill_event(2, spec=spec, quantity="-1", price="30", fee="1"))
    assert wallet_cash(state, wallet()) == D("1007")
    assert not state.positions
    assert active_risk_native(state, wallet()) == 0


def test_fixed_decimal_context_ignores_caller_precision_and_traps():
    spec = linear_spec()
    state = apply_event(funded(), fill_event(1, spec=spec, quantity="3", price=".1", fee=".1"))
    event = fill_event(2, spec=spec, quantity="-1", price=".2")
    expected = apply_event(state, event)
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        actual = apply_event(state, event)
    assert actual == expected


def test_unknown_wallet_raises_and_immutable_records_reject_invalid_scalars():
    state = funded()
    with pytest.raises(ValueError, match="UNKNOWN_WALLET"):
        wallet_cash(state, wallet("BTC"))
    with pytest.raises(FrozenInstanceError):
        state.last_sequence = 4
    with pytest.raises((ValueError, TypeError)):
        WalletBalance(wallet=wallet(), cash=float("nan"))
    with pytest.raises(TypeError):
        initial_state(specs=[linear_spec()], balances=state.balances)


def test_schema_registers_all_lifecycle_events_without_claiming_their_cash():
    assert set(EVENT_SCHEMAS) == {
        "RESERVE",
        "CANCEL_CONFIRMED",
        "FILL",
        "FEE",
        "MARK",
        "EXPIRY",
        "EXERCISE_TO_FUTURE",
        "FUTURE_DELIVERY",
        "CASH_SETTLEMENT",
        "CORRECTION",
    }
    event = replace(fill_event(1, spec=linear_spec(), quantity="1", price="20"), kind="CORRECTION")
    state = apply_event(funded(), event)
    assert state.unapplied == (event,)
    assert "CORRECTION_REQUIRES_RECONCILIATION" in state.frozen_reasons


def test_below_order_min_actual_fill_records_cash_and_basis_with_anomaly():
    spec = linear_spec(min_quantity_native=D("1"))
    state = apply_event(funded(spec=spec), fill_event(1, spec=spec, quantity=".4", price="20", fee=".8"))
    assert wallet_cash(state, wallet()) == D("991.2")
    assert state.positions[0].quantity_native == D(".4")
    assert state.positions[0].premium_basis == D("8")
    assert active_risk_native(state, wallet()) == D("8.8")
    assert "INVALID_FILL_QUANTITY" in state.frozen_reasons
    assert not state.unapplied


@pytest.mark.parametrize("sleeve", [None, "unknown"])
def test_missing_sleeve_records_actual_cash_and_freezes_budget_attribution(sleeve):
    event = replace(fill_event(1, spec=linear_spec(), quantity="1", price="20"), sleeve=sleeve)
    state = apply_event(funded(), event)
    assert wallet_cash(state, wallet()) == D("980")
    assert state.positions[0].premium_basis == D("20")
    assert "UNRESOLVED_SLEEVE_ATTRIBUTION" in state.frozen_reasons


def test_explicit_fee_debits_original_wallet_and_allocates_only_proven_opening_basis():
    spec = linear_spec()
    opening = fill_event(1, spec=spec, quantity="1", price="20")
    state = apply_event(funded(), opening)
    fee = replace(
        opening, key=key(2), kind="FEE", quantity_native=None, price=None, fee=D("2"), parent_id=opening.key.event_id
    )
    state = apply_event(state, fee)
    assert wallet_cash(state, wallet()) == D("978")
    assert active_risk_native(state, wallet()) == D("22")
    assert not state.unapplied


def test_unallocated_fee_records_cash_without_inventing_opening_basis():
    opening = fill_event(1, spec=linear_spec(), quantity="1", price="20")
    state = apply_event(funded(), opening)
    fee = replace(
        opening,
        key=key(2),
        kind="FEE",
        instrument_id=None,
        intent_id=None,
        order_id=None,
        sleeve=None,
        quantity_native=None,
        price=None,
        fee=D("2"),
    )
    state = apply_event(state, fee)
    assert wallet_cash(state, wallet()) == D("978")
    assert active_risk_native(state, wallet()) == D("20")
    assert state.unapplied == (fee,)
    assert "UNRESOLVED_FEE_BASIS_ATTRIBUTION" in state.frozen_reasons


def test_mark_retains_receipt_and_does_not_make_model_value_spendable():
    mark = replace(
        fill_event(1, spec=linear_spec(), quantity="1", price="25"),
        kind="MARK",
        quantity_native=None,
        intent_id=None,
        order_id=None,
        sleeve=None,
        fee_wallet=None,
    )
    state = apply_event(funded(), mark)
    assert wallet_cash(state, wallet()) == D("1000")
    assert state.receipts == (mark,)
    assert not state.frozen_reasons


def test_premature_settlement_is_preserved_frozen_without_cash():
    event = replace(
        fill_event(1, spec=linear_spec(), quantity="1", price="20"),
        kind="CASH_SETTLEMENT",
        order_id=None,
        sleeve=None,
        price=None,
        amount=D("30"),
        parent_id="expiry-link",
    )
    state = apply_event(funded(), event)
    assert state.unapplied == (event,)
    assert wallet_cash(state, wallet()) == D("1000")
    assert "SETTLEMENT_BEFORE_EXPIRY" in state.frozen_reasons


def test_fee_inventory_identity_alone_is_not_proof_of_opening_fee():
    opening = fill_event(1, spec=linear_spec(), quantity="1", price="20")
    state = apply_event(funded(), opening)
    fee = replace(opening, key=key(2), kind="FEE", order_id=None, quantity_native=None, price=None, fee=D("2"))
    state = apply_event(state, fee)
    assert wallet_cash(state, wallet()) == D("978")
    assert active_risk_native(state, wallet()) == D("20")
    assert state.unapplied == (fee,)


def test_late_opening_fee_after_partial_close_does_not_invent_remaining_basis():
    spec = linear_spec()
    opening = fill_event(1, spec=spec, quantity="1", price="20")
    state = apply_event(funded(), opening)
    state = apply_event(state, fill_event(2, spec=spec, quantity="-.5", price="30", order_id="close"))
    fee = replace(
        opening, key=key(3), kind="FEE", quantity_native=None, price=None, fee=D("2"), parent_id=opening.key.event_id
    )
    state = apply_event(state, fee)
    assert wallet_cash(state, wallet()) == D("993")
    assert active_risk_native(state, wallet()) == D("10")
    assert state.unapplied == (fee,)


def test_sleeve_conflict_records_actual_fill_but_freezes_attribution():
    spec = linear_spec()
    state = apply_event(funded(), fill_event(1, spec=spec, quantity="1", price="20"))
    state = apply_event(state, fill_event(2, spec=spec, quantity="1", price="20", sleeve="directional"))
    assert wallet_cash(state, wallet()) == D("960")
    assert state.positions[0].quantity_native == D("2")
    assert "SLEEVE_ATTRIBUTION_CONFLICT" in state.frozen_reasons


def test_reused_order_across_intents_cannot_prove_fee_basis():
    spec = linear_spec()
    opening = fill_event(1, spec=spec, quantity="1", price="20")
    state = apply_event(funded(), opening)
    state = apply_event(state, fill_event(2, spec=spec, quantity="1", price="20", intent_id="i2"))
    fee = replace(opening, key=key(3), kind="FEE", quantity_native=None, price=None, fee=D("2"))
    state = apply_event(state, fee)
    assert wallet_cash(state, wallet()) == D("958")
    assert active_risk_native(state, wallet()) == D("40")
    assert state.unapplied == (fee,)


def test_known_fee_wallet_is_debited_even_when_attribution_wallet_is_unknown():
    opening = fill_event(1, spec=linear_spec(), quantity="1", price="20")
    state = apply_event(funded(), opening)
    fee = replace(
        opening,
        key=key(2),
        kind="FEE",
        wallet=replace(wallet(), account="unregistered"),
        quantity_native=None,
        price=None,
        fee=D("2"),
        parent_id=opening.key.event_id,
    )
    state = apply_event(state, fee)
    assert wallet_cash(state, wallet()) == D("978")
    assert active_risk_native(state, wallet()) == D("20")
    assert state.unapplied == (fee,)
    assert state.receipts[-1] == fee
    assert "UNRESOLVED_FEE_BASIS_ATTRIBUTION" in state.frozen_reasons


@pytest.mark.parametrize("reserve_amount", ["22", "8.4"])
@pytest.mark.parametrize("reference", ["order", "parent_only"])
@pytest.mark.parametrize("quantity", [".4", "1"])
def test_inline_and_proven_standalone_fee_have_equivalent_cash_basis_and_reserve(reserve_amount, reference, quantity):
    spec = linear_spec()
    reserved = apply_event(funded(), reserve(amount=reserve_amount, quantity="1"))
    inline = apply_event(reserved, fill_event(2, spec=spec, quantity=quantity, price="20", fee=".8"))
    opening = fill_event(2, spec=spec, quantity=quantity, price="20")
    standalone = apply_event(reserved, opening)
    fee = replace(
        opening,
        key=key(3),
        kind="FEE",
        quantity_native=None,
        price=None,
        fee=D(".8"),
        parent_id=opening.key.event_id if reference == "parent_only" else None,
        order_id=None if reference == "parent_only" else opening.order_id,
    )
    standalone = apply_event(standalone, fee)
    assert standalone.balances == inline.balances
    assert standalone.positions == inline.positions
    assert standalone.reservations == inline.reservations
    assert standalone.frozen_reasons == inline.frozen_reasons
    if quantity == ".4":
        assert standalone.reservations[0].remaining_quantity_native == D(".6")
        assert standalone.reservations[0].remaining_cash == max(D("0"), D(reserve_amount) - D("8.8"))
        assert active_risk_native(standalone, wallet()) == D("8.8")
    else:
        assert standalone.reservations == ()
        assert active_risk_native(standalone, wallet()) == D("20.8")
    assert not standalone.unapplied


def test_proven_fee_in_different_wallet_does_not_release_premium_wallet_reserve():
    spec, fw = linear_spec(), wallet("BTC")
    reserved = apply_event(funded(extra=(WalletBalance(wallet=fw, cash="1"),)), reserve())
    opening = fill_event(2, spec=spec, quantity=".4", price="20", fee_wallet=fw)
    state = apply_event(reserved, opening)
    fee = replace(
        opening, key=key(3), kind="FEE", quantity_native=None, price=None, fee=D(".01"), parent_id=opening.key.event_id
    )
    state = apply_event(state, fee)
    assert wallet_cash(state, fw) == D(".99")
    assert state.reservations[0].remaining_cash == D("14")
    assert state.reservations[0].remaining_quantity_native == D(".6")
    assert active_risk_native(state, fw) == D(".01")


@pytest.mark.parametrize("amount, reasons", [("13", ()), ("12", ()), ("10", ("RESERVATION_OVERRUN",))])
@pytest.mark.parametrize("reference", ["order", "parent_only"])
def test_full_fill_separate_fee_preserves_original_budget(amount, reasons, reference):
    spec = linear_spec()
    reserved = apply_event(funded(), reserve(amount=amount))
    inline = apply_event(reserved, fill_event(2, spec=spec, quantity="1", price="10", fee="2"))
    opening = fill_event(2, spec=spec, quantity="1", price="10")
    fee = replace(
        opening, key=key(3), kind="FEE", quantity_native=None, price=None, fee=D("2"),
        parent_id=opening.key.event_id if reference == "parent_only" else None,
        order_id=None if reference == "parent_only" else opening.order_id,
    )
    separate = apply_event(apply_event(reserved, opening), fee)
    assert separate.frozen_reasons == inline.frozen_reasons == reasons
    assert separate.balances == inline.balances
    assert wallet_cash(separate, wallet()) == D("988")
    assert separate.positions == inline.positions
    assert separate.positions[0].premium_basis == D("10")
    assert separate.positions[0].opening_fees_basis == ((wallet(), D("2")),)
    assert separate.reservations == inline.reservations == ()
    assert separate.unapplied == ()
    retry = replace(fee, key=replace(fee.key, received_ns=4, available_ns=4, journal_sequence=4), raw_hash="retry")
    repeated = apply_event(apply_event(separate, fee), retry)
    assert repeated.balances == separate.balances
    assert repeated.positions == separate.positions
    assert repeated.frozen_reasons == reasons
    extra = replace(fee, key=key(5), fee=D("1"))
    final = apply_event(repeated, extra)
    assert wallet_cash(final, wallet()) == D("987")
    assert final.positions[0].opening_fees_basis == ((wallet(), D("3")),)
    assert final.frozen_reasons == (() if amount == "13" else ("RESERVATION_OVERRUN",))


def test_reused_completed_order_freezes_ambiguous_fee_budget_after_debit():
    spec = linear_spec()
    state = apply_event(funded(), reserve(amount="10"))
    opening = fill_event(2, spec=spec, quantity="1", price="10")
    state = apply_event(state, opening)
    state = apply_event(state, reserve(3, amount="100"))
    fee = replace(opening, key=key(4), kind="FEE", quantity_native=None, price=None,
                  fee=D("2"), parent_id=opening.key.event_id)
    state = apply_event(state, fee)
    assert wallet_cash(state, wallet()) == D("988")
    assert state.positions[0].opening_fees_basis == ((wallet(), D("2")),)
    assert "ORDER_ATTRIBUTION_MISMATCH" in state.frozen_reasons
    assert state.reservations[0].remaining_cash == D("100")


def test_completed_order_foreign_fee_keeps_native_basis_without_currency_addition():
    spec, fw = linear_spec(), wallet("BTC")
    state = apply_event(funded(extra=(WalletBalance(wallet=fw, cash="1"),)), reserve(amount="10"))
    opening = fill_event(2, spec=spec, quantity="1", price="10", fee_wallet=fw)
    state = apply_event(state, opening)
    fee = replace(opening, key=key(3), kind="FEE", quantity_native=None, price=None,
                  fee=D(".01"), parent_id=opening.key.event_id)
    state = apply_event(state, fee)
    assert wallet_cash(state, wallet()) == D("990")
    assert wallet_cash(state, fw) == D(".99")
    assert state.positions[0].opening_fees_basis == ((fw, D(".01")),)
    assert state.reservations == ()
    assert state.frozen_reasons == ()
