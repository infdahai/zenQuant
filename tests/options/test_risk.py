"""Budget admission uses actual wallet basis, never profitable marks or proceeds."""

from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_UP, Inexact, localcontext
from decimal import Decimal as D

import pytest

from zenquant.options.contracts import CombinationIntent, LegIntent, fingerprint
from zenquant.options.costs import FeePolicy, TimingPolicy, quote_cost
from zenquant.options.ledger import (
    LedgerEvent,
    Reservation,
    SystemClaim,
    WalletBalance,
    apply_event,
    initial_state,
)
from zenquant.options.risk import RiskPolicy, approve_intent, long_expiry_scenarios

from .factories import book_for, fill_event, key, linear_spec, observation, wallet


def policy(**changes):
    return RiskPolicy(
        **dict(
            per_intent_report=D("10"),
            active_and_pending_report=D("50"),
            rolling_tail_paid_report=D("20"),
            rolling_window_ns=100,
            report_currency="USDT",
        )
        | changes
    )


def intent_for(
    *, spec=None, w=None, budget=None, amount="1", identity="i1", sleeve="directional", template="long_call", legs=None
):
    spec = spec or linear_spec()
    budget = budget or policy()
    return CombinationIntent(
        intent_id=identity,
        legs=legs
        or (LegIntent(spec=spec, wallet=w or wallet(spec.quote_currency, spec.venue), quantity_native=D(amount)),),
        template=template,
        sleeve=sleeve,
        decision_cutoff_ns=10,
        valid_until_ns=100,
        policy_hash=fingerprint(budget),
    )


def funded(*, specs=None, balances=None):
    return initial_state(
        specs=specs or (linear_spec(),),
        balances=balances or (WalletBalance(wallet=wallet(), cash=D("1000")),),
    )


def cost_for(leg, *, paid="5", fee_rate="0", asof=10, report="USDT", fx="1"):
    return quote_cost(
        book=book_for(
            leg.spec, asks=((paid, str(leg.quantity_native)),), bids=(("0.01", str(leg.quantity_native)),), time_ns=asof
        ),
        side="BUY",
        quantity_native=leg.quantity_native,
        index_in_quote=observation("80000", time_ns=asof),
        quote_to_report=observation(fx, time_ns=asof),
        fee_policy=FeePolicy(
            rate=D(fee_rate), premium_cap=D(".07"), fee_currency=leg.spec.quote_currency, provenance="fixture"
        ),
        timing=TimingPolicy(max_age_ns=10, minimum_source_age_ns=0),
        asof_ns=asof,
        report_currency=report,
    )


def approve(*, intent=None, state=None, budget=None, costs=None, fx=(), asof=10):
    budget = budget or policy()
    intent = intent or intent_for(budget=budget)
    return approve_intent(
        intent=intent,
        state=state or funded(),
        costs=costs if costs is not None else tuple(cost_for(leg) for leg in intent.legs),
        wallet_fx=fx,
        policy=budget,
        asof_ns=asof,
    )


def reserve(n, *, intent, amount, quantity="1", order="o1", sleeve=None):
    return LedgerEvent(
        key=key(n),
        kind="RESERVE",
        wallet=intent.legs[0].wallet,
        instrument_id=None,
        intent_id=intent.intent_id,
        order_id=order,
        sleeve=intent.sleeve if sleeve is None else sleeve,
        quantity_native=D(quantity),
        price=None,
        amount=D(amount),
        fee=D("0"),
        fee_wallet=None,
        parent_id=None,
        raw_hash=f"r{n}",
    )


def assert_blocked(result, reason, status="REJECTED"):
    assert result.status.status == status
    assert reason in result.reasons
    assert result.approved_intent is None
    assert result.cash_reserve == ()
    assert result.execution_authority == "NONE"


def test_legal_point_zero_one_buy_rejects_1624_against_ten_without_resizing():
    i = intent_for(amount=".01")
    cost = cost_for(i.legs[0], paid="1600", fee_rate=".0003")
    assert cost.total_report_currency == D("16.24")
    assert_blocked(approve(intent=i, costs=(cost,)), "PER_INTENT_LIMIT")


def test_active_capital_survives_real_reservation_release():
    p = policy(per_intent_report=D("30"))
    first = intent_for(budget=p)
    approval = approve(intent=first, budget=p, costs=(cost_for(first.legs[0], paid="30"),))
    assert approval.status.status == "VALID"
    assert approval.cash_reserve == ((wallet(), D("30")),)
    state = apply_event(funded(), reserve(1, intent=first, amount="30"))
    second = intent_for(budget=p, identity="i2")
    second_cost = (cost_for(second.legs[0], paid="25"),)
    assert_blocked(approve(intent=second, budget=p, state=state, costs=second_cost), "ACTIVE_AND_PENDING_LIMIT")
    state = apply_event(state, fill_event(2, spec=linear_spec(), quantity="1", price="30", sleeve="directional"))
    assert not state.reservations and not state.frozen_reasons
    assert state.positions[0].premium_basis == D("30")
    assert_blocked(approve(intent=second, budget=p, state=state, costs=second_cost), "ACTIVE_AND_PENDING_LIMIT")


def test_partial_fill_preserves_pending_plus_active_and_cancel_releases_only_pending():
    p = policy(per_intent_report=D("30"))
    first = intent_for(budget=p)
    state = apply_event(funded(), reserve(1, intent=first, amount="30"))
    state = apply_event(state, fill_event(2, spec=linear_spec(), quantity=".4", price="30", sleeve="directional"))
    second = intent_for(budget=p, identity="i2")
    cost = (cost_for(second.legs[0], paid="25"),)
    assert_blocked(approve(intent=second, state=state, budget=p, costs=cost), "ACTIVE_AND_PENDING_LIMIT")
    cancel = replace(
        reserve(3, intent=first, amount="30"), kind="CANCEL_CONFIRMED", amount=None, quantity_native=None, sleeve=None
    )
    state = apply_event(state, cancel)
    assert approve(intent=second, state=state, budget=p, costs=cost).status.status == "VALID"
    state = apply_event(state, fill_event(4, spec=linear_spec(), quantity=".6", price="30", sleeve="directional"))
    assert_blocked(approve(intent=second, state=state, budget=p, costs=cost), "STATE_FROZEN")


def test_cash_is_not_pooled_across_venues():
    w2 = wallet(venue="OTHER")
    state = funded(balances=(WalletBalance(wallet=wallet(), cash=D("4")), WalletBalance(wallet=w2, cash=D("1000"))))
    assert_blocked(approve(state=state), "INSUFFICIENT_WALLET_CASH")


@pytest.mark.parametrize("paid,valid", [("10", True), ("10.01", False)])
def test_exact_budget_boundary(paid, valid):
    i = intent_for()
    result = approve(intent=i, costs=(cost_for(i.legs[0], paid=paid),))
    assert result.status.status == ("VALID" if valid else "REJECTED")
    assert result.execution_authority == "NONE"
    if valid:
        assert result.approved_intent == i
        assert "ACCOUNT_ELIGIBILITY_NOT_EVALUABLE" in result.reasons
        assert "INTERIM_VALUATION_NOT_EVALUABLE" in result.reasons
        assert "REAL_MAXIMUM_RISK_NOT_EVALUABLE" in result.reasons


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"instrument_hash": "other"}, "COST_BINDING_MISMATCH"),
        ({"side": "SELL"}, "COST_BINDING_MISMATCH"),
        ({"quantity_native": D(".5")}, "COST_BINDING_MISMATCH"),
        ({"quote_currency": "USD"}, "COST_BINDING_MISMATCH"),
        ({"report_currency": "USD"}, "COST_BINDING_MISMATCH"),
        ({"asof_ns": 9}, "COST_BINDING_MISMATCH"),
        ({"total_report_currency": D("0")}, "COST_REPORT_MISMATCH"),
    ],
)
def test_cost_is_exactly_bound_to_proposed_buy(change, reason):
    i = intent_for()
    assert_blocked(approve(intent=i, costs=(replace(cost_for(i.legs[0]), **change),)), reason)


@pytest.mark.parametrize("count", [0, 2])
def test_costs_cover_legs_once(count):
    i = intent_for()
    assert_blocked(approve(intent=i, costs=(cost_for(i.legs[0]),) * count), "COST_COVERAGE_MISMATCH")


@pytest.mark.parametrize("asof,reason", [(9, "INTENT_NOT_YET_AVAILABLE"), (100, "INTENT_EXPIRED")])
def test_intent_cutoff_and_expiry(asof, reason):
    assert_blocked(approve(asof=asof), reason)


def test_intent_policy_must_match():
    assert_blocked(approve(intent=replace(intent_for(), policy_hash="other")), "POLICY_HASH_MISMATCH")


def test_existing_intent_id_cannot_double_reserve():
    i = intent_for()
    state = apply_event(funded(), reserve(1, intent=i, amount="5"))
    assert_blocked(approve(intent=i, state=state), "INTENT_ID_ALREADY_USED")


@pytest.mark.parametrize(
    "change",
    [
        {"exercise_style": "american"},
        {"product": "inverse_option", "settlement_rule": "inverse_via_future"},
        {"settlement_currency": "USD"},
    ],
)
def test_representable_unsupported_economics_fail_operationally(change):
    spec = replace(linear_spec(), **change)
    assert_blocked(
        approve(intent=intent_for(spec=spec), state=funded(specs=(spec,))), "UNSUPPORTED_INSTRUMENT", "UNSUPPORTED"
    )


@pytest.mark.parametrize("change", [{"account_mode": "portfolio"}, {"currency": "USD"}, {"venue": "OTHER"}])
def test_wallet_model_admission_is_explicit(change):
    i = intent_for(w=replace(wallet(), **change))
    assert_blocked(approve(intent=i), "UNSUPPORTED_WALLET", "UNSUPPORTED")


def test_below_minimum_is_no_trade_without_upsize():
    with pytest.raises(ValueError, match="below minimum"):
        intent_for(amount=".001")


@pytest.mark.parametrize(
    "template,put_strike,call_strike,valid",
    [
        ("long_straddle", "100", "100", True),
        ("long_straddle", "90", "100", False),
        ("long_strangle", "90", "110", True),
        ("long_strangle", "110", "90", False),
    ],
)
def test_two_leg_template_relations(template, put_strike, call_strike, valid):
    put, call = linear_spec(option_type="P", strike=put_strike), linear_spec(strike=call_strike)
    legs = tuple(LegIntent(spec=s, wallet=wallet(), quantity_native=D("1")) for s in (put, call))
    i = intent_for(legs=legs, template=template)
    result = approve(intent=i, state=funded(specs=(put, call)), costs=tuple(cost_for(leg, paid="4") for leg in legs))
    assert result.status.status == ("VALID" if valid else "REJECTED")


@pytest.mark.parametrize(
    "change",
    [
        {"underlying": "ETH"},
        {"expiry_ns": 1001},
        {"quote_currency": "USD", "strike_currency": "USD", "settlement_currency": "USD", "margin_currency": "USD"},
    ],
)
def test_two_leg_template_does_not_mix_terminal_identity(change):
    put, call = linear_spec(option_type="P"), replace(linear_spec(), **change)
    legs = (
        LegIntent(spec=put, wallet=wallet(), quantity_native=D("1")),
        LegIntent(spec=call, wallet=wallet(call.quote_currency), quantity_native=D("1")),
    )
    i = intent_for(legs=legs, template="long_straddle")
    assert_blocked(approve(intent=i, state=funded(specs=(put, call))), "TEMPLATE_MISMATCH")


def fx_setup():
    spec = linear_spec(quote_currency="USD", strike_currency="USD", settlement_currency="USD", margin_currency="USD")
    p = policy(fx_timing=TimingPolicy(max_age_ns=2, minimum_source_age_ns=0))
    i = intent_for(spec=spec, budget=p)
    state = funded(specs=(spec,), balances=(WalletBalance(wallet=wallet("USD"), cash=D("100")),))
    return p, i, state, (cost_for(i.legs[0], fx="2"),)


@pytest.mark.parametrize(
    "fx,reason",
    [
        ((), "WALLET_FX_MISSING"),
        (((wallet("USD"), observation("2", time_ns=7)),), "WALLET_FX_STALE"),
        (((wallet("USD"), observation("2", time_ns=11)),), "WALLET_FX_NOT_YET_AVAILABLE"),
    ],
)
def test_nonreport_fx_fails_closed_with_fresh_other_dependencies(fx, reason):
    p, i, state, costs = fx_setup()
    assert_blocked(approve(intent=i, state=state, budget=p, costs=costs, fx=fx), reason, "NOT_EVALUABLE")


def test_fx_requires_explicit_timing_and_proxy_does_not_grant_authority():
    p, i, state, costs = fx_setup()
    p = replace(p, fx_timing=None)
    i = replace(i, policy_hash=fingerprint(p))
    fx = ((wallet("USD"), observation("2", provenance="proxy")),)
    assert_blocked(
        approve(intent=i, state=state, budget=p, costs=costs, fx=fx), "WALLET_FX_TIMING_POLICY_MISSING", "NOT_EVALUABLE"
    )
    p, i, state, costs = fx_setup()
    result = approve(intent=i, state=state, budget=p, costs=costs, fx=fx)
    assert result.status.status == "VALID" and result.execution_authority == "NONE"
    assert "PROXY_WALLET_FX" in result.reasons


def test_same_currency_needs_no_fx_but_supplied_future_is_rejected():
    assert approve().status.status == "VALID"
    assert_blocked(
        approve(fx=((wallet(), observation("1", time_ns=11)),)), "WALLET_FX_NOT_YET_AVAILABLE", "NOT_EVALUABLE"
    )


def test_tail_budget_paid_close_proceeds_never_refund_and_receipts_deduplicate():
    first = intent_for(sleeve="tail")
    state = apply_event(funded(), reserve(1, intent=first, amount="12"))
    receipt = fill_event(2, spec=linear_spec(), quantity="1", price="10", fee="2")
    state = apply_event(state, receipt)
    close_reserve = reserve(3, intent=first, amount="1", order="close")
    state = apply_event(state, close_reserve)
    state = apply_event(
        state, replace(fill_event(4, spec=linear_spec(), quantity="-1", price="100", fee="1"), order_id="close")
    )
    assert not state.frozen_reasons and not state.positions
    i = intent_for(identity="i2", sleeve="tail")
    assert_blocked(approve(intent=i, state=state, costs=(cost_for(i.legs[0], paid="8"),)), "ROLLING_TAIL_PAID_LIMIT")
    result = approve(intent=i, state=state, costs=(cost_for(i.legs[0], paid="7"),))
    assert result.status.status == "VALID"


def test_tail_pending_and_paid_share_budget_after_partial_fill():
    first = intent_for(sleeve="tail")
    state = apply_event(funded(), reserve(1, intent=first, amount="12"))
    state = apply_event(state, fill_event(2, spec=linear_spec(), quantity=".5", price="20", fee="1"))
    i = intent_for(identity="i2", sleeve="tail")
    assert_blocked(approve(intent=i, state=state, costs=(cost_for(i.legs[0], paid="9"),)), "ROLLING_TAIL_PAID_LIMIT")


def test_unknown_pending_or_fill_attribution_is_not_zero():
    i = intent_for(identity="i2")
    state = replace(
        funded(),
        reservations=(
            Reservation(
                wallet=wallet(),
                intent_id="mystery",
                order_id="o",
                remaining_cash=D("1"),
                remaining_quantity_native=D("1"),
            ),
        ),
    )
    assert_blocked(approve(intent=i, state=state), "RESERVATION_ATTRIBUTION_UNKNOWN", "NOT_EVALUABLE")
    first = intent_for()
    state = apply_event(funded(), reserve(1, intent=first, amount="5"))
    state = apply_event(state, replace(fill_event(2, spec=linear_spec(), quantity="1", price="5"), sleeve=None))
    assert state.balances[0].cash == D("995")  # Actual debit is retained.
    assert_blocked(approve(intent=i, state=state), "STATE_FROZEN")
    state = replace(state, frozen_reasons=(), receipts=(replace(state.receipts[0], sleeve=None), state.receipts[1]))
    assert_blocked(approve(intent=i, state=state), "ACTUAL_ATTRIBUTION_UNKNOWN", "NOT_EVALUABLE")


@pytest.mark.parametrize("event_ns,consumed", [(1, False), (50, True)])
def test_tail_paid_window_uses_economic_and_knowledge_time(event_ns, consumed):
    spec = linear_spec(expiry_ns=1000)
    i1 = intent_for(spec=spec, sleeve="tail")
    state = apply_event(funded(specs=(spec,)), reserve(1, intent=i1, amount="12"))
    state = apply_event(state, fill_event(2, spec=spec, quantity="1", price="10", fee="2", event_ns=event_ns))
    i = replace(intent_for(spec=spec, identity="i2", sleeve="tail"), decision_cutoff_ns=150, valid_until_ns=160)
    result = approve(intent=i, state=state, costs=(cost_for(i.legs[0], paid="9", asof=150),), asof=150)
    assert result.status.status == ("REJECTED" if consumed else "VALID")


def test_future_state_and_unapplied_receipts_block():
    assert_blocked(approve(state=replace(funded(), last_available_ns=11)), "STATE_NOT_YET_AVAILABLE", "NOT_EVALUABLE")
    state = apply_event(funded(), replace(fill_event(1, spec=linear_spec(), quantity="1", price="5"), intent_id=None))
    assert_blocked(approve(state=state), "STATE_FROZEN")


def test_system_claim_and_original_fee_wallet_remain_active():
    fw = wallet("BTC")
    p = policy(per_intent_report=D("30"), fx_timing=TimingPolicy(max_age_ns=2, minimum_source_age_ns=0))
    claim = SystemClaim(
        wallet=wallet(),
        instrument_id=linear_spec().instrument_id,
        link_id="claim",
        intent_id="previous",
        quantity_native=D("1"),
        future_quantity_base=D("1"),
        strike=D("100"),
        settlement_amount=D("1000"),
        premium_basis=D("25"),
        opening_fees_basis=((fw, D(".0001")),),
    )
    state = replace(
        funded(balances=(WalletBalance(wallet=wallet(), cash=D("1000")), WalletBalance(wallet=fw, cash=D("1")))),
        claims=(claim,),
    )
    i = intent_for(budget=p, identity="i2")
    assert_blocked(
        approve(
            intent=i, state=state, budget=p, costs=(cost_for(i.legs[0], paid="20"),), fx=((fw, observation("80000")),)
        ),
        "ACTIVE_AND_PENDING_LIMIT",
    )


def test_independent_3000_configuration_not_added_to_1000_cash():
    p = policy(per_intent_report=D("30"), active_and_pending_report=D("150"), rolling_tail_paid_report=D("60"))
    i = intent_for(budget=p)
    state = funded(balances=(WalletBalance(wallet=wallet(), cash=D("3000")),))
    assert approve(intent=i, state=state, budget=p, costs=(cost_for(i.legs[0], paid="30"),)).cash_reserve == (
        (wallet(), D("30")),
    )
    assert state.balances[0].cash == D("3000")


def test_scenarios_are_native_terminal_net_cash_and_full_loss_is_paid_only():
    i = intent_for()
    rows = long_expiry_scenarios(
        intent=i,
        deliveries=(((i.legs[0].spec.instrument_id, D("90")),), ((i.legs[0].spec.instrument_id, D("120")),)),
        paid_by_currency=(("USDT", D("5")), ("BTC", D(".001"))),
    )
    assert rows == ((("BTC", D("-.001")), ("USDT", D("-5"))), (("BTC", D("-.001")), ("USDT", D("15"))))


@pytest.mark.parametrize(
    "change", [{"per_intent_report": D("-1")}, {"rolling_window_ns": True}, {"fx_timing": "hidden"}]
)
def test_policy_strict_validators(change):
    with pytest.raises((TypeError, ValueError)):
        policy(**change)


def test_policy_and_approval_are_immutable_and_decimal_context_independent():
    baseline = approve()
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = ROUND_UP
        ctx.traps[Inexact] = True
        assert approve() == baseline
    with pytest.raises(FrozenInstanceError):
        baseline.execution_authority = "LIVE"
    with pytest.raises(FrozenInstanceError):
        policy().per_intent_report = D("100")


def test_tail_receipt_retransmission_does_not_double_paid_cost():
    first = intent_for(sleeve="tail")
    state = apply_event(funded(), reserve(1, intent=first, amount="10"))
    receipt = fill_event(2, spec=linear_spec(), quantity="1", price="10")
    state = apply_event(state, receipt)
    state = apply_event(
        state,
        replace(receipt, key=replace(receipt.key, received_ns=3, available_ns=3, journal_sequence=3), raw_hash="copy"),
    )
    i = intent_for(identity="i2", sleeve="tail")
    assert approve(intent=i, state=state, costs=(cost_for(i.legs[0], paid="10"),)).status.status == "VALID"


def test_historical_foreign_tail_fee_cannot_use_current_lower_fx():
    first = intent_for(sleeve="tail")
    fw = wallet("BTC")
    state = funded(balances=(WalletBalance(wallet=wallet(), cash=D("1000")), WalletBalance(wallet=fw, cash=D("1"))))
    state = apply_event(state, reserve(1, intent=first, amount="10"))
    state = apply_event(state, fill_event(2, spec=linear_spec(), quantity="1", price="10", fee=".0001", fee_wallet=fw))
    p = policy(fx_timing=TimingPolicy(max_age_ns=2, minimum_source_age_ns=0))
    i = intent_for(identity="i2", budget=p, sleeve="tail")
    assert_blocked(
        approve(intent=i, state=state, budget=p, fx=((fw, observation("1")),)),
        "HISTORICAL_PAID_FX_MISSING",
        "NOT_EVALUABLE",
    )


def test_unrelated_foreign_cash_does_not_require_conversion_to_approve_native_cash():
    state = funded(
        balances=(WalletBalance(wallet=wallet(), cash=D("1000")), WalletBalance(wallet=wallet("BTC"), cash=D("1")))
    )
    assert approve(state=state).status.status == "VALID"


def test_unequal_straddle_base_quantities_are_not_same_template():
    put, call = linear_spec(option_type="P"), linear_spec()
    legs = (
        LegIntent(spec=put, wallet=wallet(), quantity_native=D("1")),
        LegIntent(spec=call, wallet=wallet(), quantity_native=D("2")),
    )
    assert_blocked(
        approve(intent=intent_for(legs=legs, template="long_straddle"), state=funded(specs=(put, call))),
        "TEMPLATE_MISMATCH",
    )


def test_unknown_actual_fee_cost_is_not_zero():
    i = intent_for()
    cost = replace(cost_for(i.legs[0]), fee_native=None)
    assert_blocked(approve(intent=i, costs=(cost,)), "COST_AMOUNT_UNKNOWN", "NOT_EVALUABLE")


def test_actual_exercise_keeps_active_basis_until_linked_delivery():
    old, new = linear_spec(settlement_rule="linear_via_future"), linear_spec(expiry_ns=3000)
    p = policy(per_intent_report=D("30"))
    first = intent_for(spec=old, budget=p)
    state = funded(specs=(old, new))
    state = apply_event(state, reserve(1, intent=first, amount="30"))
    state = apply_event(state, fill_event(2, spec=old, quantity="1", price="30", sleeve="directional"))
    exercise = LedgerEvent(
        key=key(1001),
        kind="EXERCISE_TO_FUTURE",
        wallet=wallet(),
        instrument_id=old.instrument_id,
        intent_id=first.intent_id,
        order_id=None,
        sleeve=None,
        quantity_native=D("1"),
        price=D("120"),
        amount=D("20"),
        fee=D("0"),
        fee_wallet=None,
        parent_id="expiry-link",
        raw_hash="exercise",
    )
    state = apply_event(state, exercise)
    assert not state.frozen_reasons and not state.positions and len(state.claims) == 1
    second = replace(intent_for(spec=new, budget=p, identity="i2"), decision_cutoff_ns=1001, valid_until_ns=2000)
    assert_blocked(
        approve(
            intent=second, state=state, budget=p, asof=1001, costs=(cost_for(second.legs[0], paid="25", asof=1001),)
        ),
        "ACTIVE_AND_PENDING_LIMIT",
    )
    delivery = replace(exercise, key=key(1002), kind="FUTURE_DELIVERY", price=None, raw_hash="delivery")
    state = apply_event(state, delivery)
    assert not state.claims and not state.frozen_reasons
    assert (
        approve(
            intent=second, state=state, budget=p, asof=1002, costs=(cost_for(second.legs[0], paid="25", asof=1002),)
        ).status.status
        == "VALID"
    )


def test_pending_unsupported_account_is_not_admitted_from_representable_state():
    w = replace(wallet(), account_mode="portfolio")
    state = funded(balances=(WalletBalance(wallet=wallet(), cash=D("1000")), WalletBalance(wallet=w, cash=D("1000"))))
    pending = Reservation(
        wallet=w, intent_id="older", order_id="o", remaining_cash=D("1"), remaining_quantity_native=D("1")
    )
    state = replace(state, reservations=(pending,))
    assert_blocked(approve(state=state), "RESERVATION_ACCOUNT_UNSUPPORTED", "NOT_EVALUABLE")


def test_late_actual_opening_fee_counts_tail_outlay():
    first = intent_for(sleeve="tail")
    state = apply_event(funded(), reserve(1, intent=first, amount="12"))
    state = apply_event(state, fill_event(2, spec=linear_spec(), quantity="1", price="10"))
    fee = LedgerEvent(
        key=key(3),
        kind="FEE",
        wallet=wallet(),
        instrument_id=linear_spec().instrument_id,
        intent_id="i1",
        order_id="o1",
        sleeve=None,
        quantity_native=None,
        price=None,
        amount=None,
        fee=D("2"),
        fee_wallet=wallet(),
        parent_id="event-2",
        raw_hash="fee",
    )
    state = apply_event(state, fee)
    assert not state.frozen_reasons
    second = intent_for(identity="i2", sleeve="tail")
    assert_blocked(
        approve(intent=second, state=state, costs=(cost_for(second.legs[0], paid="9"),)), "ROLLING_TAIL_PAID_LIMIT"
    )


def test_fx_policy_changes_fingerprint():
    assert fingerprint(policy()) != fingerprint(policy(fx_timing=TimingPolicy(max_age_ns=2, minimum_source_age_ns=0)))


def test_unavailable_fee_policy_in_cost_is_not_zero():
    i = intent_for()
    cost = replace(
        cost_for(i.legs[0]),
        status=replace(cost_for(i.legs[0]).status, status="NOT_EVALUABLE", reason="fee tier unknown"),
        fee_native=None,
        total_report_currency=None,
    )
    assert_blocked(approve(intent=i, costs=(cost,)), "COST_NOT_VALID", "NOT_EVALUABLE")


@pytest.mark.parametrize("amount, reason", [("12", "MODELED_FUNDED_LONG_FEASIBLE"), ("10", "STATE_FROZEN")])
@pytest.mark.parametrize("standalone", [False, True])
def test_full_fill_opening_fee_transport_has_same_next_approval(amount, reason, standalone):
    first = intent_for()
    state = apply_event(funded(), reserve(1, intent=first, amount=amount))
    opening = fill_event(
        2, spec=linear_spec(), quantity="1", price="10", fee="0" if standalone else "2", sleeve=first.sleeve
    )
    state = apply_event(state, opening)
    if standalone:
        fee = replace(
            opening,
            key=key(3),
            kind="FEE",
            quantity_native=None,
            price=None,
            fee=D("2"),
            parent_id=opening.key.event_id,
        )
        state = apply_event(state, fee)
    second = intent_for(identity="i2")
    result = approve(intent=second, state=state)
    assert result.status.reason == reason
    assert result.status.status == ("VALID" if amount == "12" else "REJECTED")
