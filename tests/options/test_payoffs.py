"""Hand-calculated terminal cashflows, separate from costs and admission."""

from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_UP, Decimal, Inexact, Rounded, localcontext

import pytest

from zenquant.options.contracts import CombinationIntent, LegIntent, PayoffLeg
from zenquant.options.payoffs import SettlementAction, SettlementPlan, expiry_payoff, intrinsic_value, settlement_plan

from .factories import linear_spec, observation, wallet

D = Decimal


@pytest.mark.parametrize(
    ("option_type", "delivery_price", "expected"),
    [("C", "150", "50"), ("P", "80", "20"), ("C", "70", "0"), ("P", "140", "0"), ("C", "100", "0")],
)
def test_per_base_intrinsic_is_hand_calculated(option_type, delivery_price, expected):
    assert intrinsic_value(spec=linear_spec(option_type=option_type), delivery_price=D(delivery_price)) == D(expected)


def test_call_cash_and_future_transition_have_equal_final_value():
    call = linear_spec(settlement_rule="linear_via_future")
    plan = settlement_plan(
        spec=call, quantity_native=D("1"), delivery=observation("150", time_ns=1000), expiry_id="expiry-1"
    )
    cash = settlement_plan(
        spec=replace(call, settlement_rule="linear_cash"),
        quantity_native=D("1"),
        delivery=observation("150", time_ns=1000),
        expiry_id="expiry-1",
    )
    assert plan.status.status == cash.status.status == "VALID"
    assert [a.kind for a in plan.actions] == ["EXERCISE_TO_FUTURE", "FUTURE_DELIVERY"]
    assert plan.actions[0].future_quantity_base == D("1")
    assert plan.actions[0].amount == D("0")
    assert plan.actions[1].amount == cash.actions[0].amount == D("50")
    assert plan.actions[1].future_quantity_base == D("1")
    assert plan.actions[0].link_id == plan.actions[1].link_id
    assert all(a.currency == "USDT" and a.option_id == call.instrument_id for a in plan.actions)
    assert cash.actions[0].kind == "CASH_SETTLEMENT"
    assert cash.actions[0].future_quantity_base == D("0")


def test_put_system_future_is_short_and_delivers_positive_cash():
    spec = linear_spec(option_type="P", settlement_rule="linear_via_future")
    plan = settlement_plan(
        spec=spec, quantity_native=D(".6"), delivery=observation("80", time_ns=1000), expiry_id="put-expiry"
    )
    assert plan.status.status == "VALID"
    assert [a.future_quantity_base for a in plan.actions] == [D("-.6"), D("-.6")]
    assert [a.amount for a in plan.actions] == [D("0"), D("12")]


@pytest.mark.parametrize("option_type, price", [("C", "80"), ("P", "140"), ("C", "100")])
def test_otm_and_atm_expire_without_system_future(option_type, price):
    plan = settlement_plan(
        spec=linear_spec(option_type=option_type, settlement_rule="linear_via_future"),
        quantity_native=D("1"),
        delivery=observation(price, time_ns=1000),
        expiry_id="otm",
    )
    assert plan.status.status == "VALID"
    assert [a.kind for a in plan.actions] == ["EXPIRY"]
    assert plan.actions[0].future_quantity_base == plan.actions[0].amount == D("0")


def test_contract_multiplier_changes_cash_and_system_quantity():
    spec = linear_spec(
        quantity_unit="contracts",
        multiplier=D(".1"),
        min_quantity_native=D("1"),
        quantity_step_native=D("1"),
        settlement_rule="linear_via_future",
    )
    plan = settlement_plan(
        spec=spec, quantity_native=D("3"), delivery=observation("150", time_ns=1000), expiry_id="contracts"
    )
    assert plan.actions[0].quantity_native == D("3")
    assert plan.actions[0].future_quantity_base == D(".3")
    assert plan.actions[1].amount == D("15")


@pytest.fixture
def straddle_paid_premium():
    # Independent cost receipt fixture; no model or cost function computes this expectation.
    return D("25")


@pytest.mark.parametrize("price, gross, net", [("70", "30", "5"), ("100", "0", "-25"), ("140", "40", "15")])
def test_straddle_gross_and_net_from_independent_premium(price, gross, net, straddle_paid_premium):
    call, put = linear_spec(), linear_spec(option_type="P")
    flows = expiry_payoff(
        legs=(PayoffLeg(spec=call, signed_quantity_native=D("1")), PayoffLeg(spec=put, signed_quantity_native=D("1"))),
        delivery_prices=((call.instrument_id, D(price)), (put.instrument_id, D(price))),
    )
    assert flows == (("USDT", D(gross)),)
    assert flows[0][1] - straddle_paid_premium == D(net)


@pytest.fixture
def spread_paid_premium():
    return D("5")


@pytest.mark.parametrize("price, gross, net", [("70", "0", "-5"), ("110", "10", "5"), ("140", "20", "15")])
def test_static_debit_call_spread_is_signed_math_only(price, gross, net, spread_paid_premium):
    long, short = linear_spec(), linear_spec(strike="120")
    flows = expiry_payoff(
        legs=(
            PayoffLeg(spec=long, signed_quantity_native=D("1")),
            PayoffLeg(spec=short, signed_quantity_native=D("-1")),
        ),
        delivery_prices=((long.instrument_id, D(price)), (short.instrument_id, D(price))),
    )
    assert flows == (("USDT", D(gross)),)
    assert flows[0][1] - spread_paid_premium == D(net)


def test_signed_payoff_leg_is_not_an_executable_leg_or_combination():
    short = PayoffLeg(spec=linear_spec(), signed_quantity_native=D("-1"))
    with pytest.raises(ValueError, match="positive"):
        LegIntent(spec=short.spec, wallet=wallet(), quantity_native=short.signed_quantity_native)
    with pytest.raises(TypeError, match="invalid element"):
        CombinationIntent(
            intent_id="math-only",
            legs=(short,),
            template="long_call",
            sleeve="directional",
            decision_cutoff_ns=10,
            valid_until_ns=20,
            policy_hash="fixture-policy",
        )
    with pytest.raises(TypeError):
        expiry_payoff(legs=(LegIntent(spec=short.spec, wallet=wallet(), quantity_native=D("1")),), delivery_prices=())


def test_payoffs_preserve_distinct_settlement_currencies_without_fx():
    usdt = linear_spec()
    usdc = linear_spec(
        underlying="ETH",
        quote_currency="USDC",
        strike_currency="USDC",
        settlement_currency="USDC",
        margin_currency="USDC",
    )
    assert expiry_payoff(
        legs=(PayoffLeg(spec=usdt, signed_quantity_native=D("1")), PayoffLeg(spec=usdc, signed_quantity_native=D("2"))),
        delivery_prices=((usdt.instrument_id, D("150")), (usdc.instrument_id, D("120"))),
    ) == (("USDC", D("40")), ("USDT", D("50")))


@pytest.mark.parametrize(
    "changes",
    [
        {"product": "inverse_option", "settlement_rule": "inverse_via_future"},
        {"exercise_style": "american"},
        {"quote_currency": "USD"},
        {"strike_currency": "USD"},
        {"settlement_currency": "BTC"},
        {"margin_currency": "BTC"},
    ],
)
def test_unsupported_economics_are_not_approximated(changes):
    spec = replace(linear_spec(), **changes)
    plan = settlement_plan(
        spec=spec, quantity_native=D("1"), delivery=observation("150", time_ns=1000), expiry_id="unsupported"
    )
    assert plan.status.status == "UNSUPPORTED"
    assert plan.actions == ()
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        intrinsic_value(spec=spec, delivery_price=D("150"))
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        expiry_payoff(
            legs=(PayoffLeg(spec=spec, signed_quantity_native=D("1")),),
            delivery_prices=((spec.instrument_id, D("150")),),
        )


def test_rule_identity_binds_same_time_settlement_to_version():
    spec = linear_spec(settlement_rule="linear_via_future")
    kwargs = {"quantity_native": D("1"), "delivery": observation("150", time_ns=1000), "expiry_id": "same-expiry"}
    old = settlement_plan(spec=spec, **kwargs)
    new = settlement_plan(spec=replace(spec, rule_version="fixture.v2"), **kwargs)
    assert old.actions[1].amount == new.actions[1].amount == D("50")
    assert old.actions[0].link_id != new.actions[0].link_id
    assert old == settlement_plan(spec=spec, **kwargs)


@pytest.mark.parametrize("valid_until_ns", [999, 1000])
def test_rule_must_still_be_effective_at_expiry(valid_until_ns):
    plan = settlement_plan(
        spec=linear_spec(valid_until_ns=valid_until_ns),
        quantity_native=D("1"),
        delivery=observation("150", time_ns=1000),
        expiry_id="expired-rule",
    )
    assert plan.status.status == "NOT_EVALUABLE"
    assert plan.actions == ()


def test_pre_expiry_delivery_cannot_settle():
    plan = settlement_plan(
        spec=linear_spec(),
        quantity_native=D("1"),
        delivery=observation("150", time_ns=999),
        expiry_id="early",
    )
    assert plan.status.status == "NOT_EVALUABLE"
    assert plan.actions == ()


def test_delayed_delivery_retains_original_evidence_and_rule_at_contractual_expiry():
    spec = linear_spec(valid_until_ns=1001)
    delivery = replace(observation("150", time_ns=1000), received_ns=1005, available_ns=1010)
    plan = settlement_plan(spec=spec, quantity_native=D("1"), delivery=delivery, expiry_id="delayed")
    assert plan.status.status == "VALID"
    assert plan.delivery is delivery
    assert plan.actions[0].amount == D("50")


def test_future_source_fixing_is_not_evaluable():
    delivery = replace(observation("150", time_ns=999), source_ns=1000)
    plan = settlement_plan(spec=linear_spec(), quantity_native=D("1"), delivery=delivery, expiry_id="future-source")
    assert plan.status.status == "NOT_EVALUABLE"
    assert plan.actions == ()


def test_missing_delivery_cannot_generate_settlement():
    plan = settlement_plan(spec=linear_spec(), quantity_native=D("1"), delivery=None, expiry_id="missing")
    assert plan.status.status == "NOT_EVALUABLE"
    assert plan.actions == ()


def test_same_native_id_with_different_economics_is_ambiguous():
    first = linear_spec()
    other = replace(first, venue="OTHER")
    with pytest.raises(ValueError, match="ambiguous"):
        expiry_payoff(
            legs=(
                PayoffLeg(spec=first, signed_quantity_native=D("1")),
                PayoffLeg(spec=other, signed_quantity_native=D("1")),
            ),
            delivery_prices=((first.instrument_id, D("150")),),
        )


@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity", "sNaN"])
def test_invalid_delivery_values_never_generate_cash(price):
    with pytest.raises(ValueError):
        intrinsic_value(spec=linear_spec(), delivery_price=D(price))
    if D(price).is_finite():
        plan = settlement_plan(
            spec=linear_spec(),
            quantity_native=D("1"),
            delivery=observation(price, time_ns=1000),
            expiry_id="bad-price",
        )
        assert plan.status.status == "INVALID"
        assert plan.actions == ()
    else:
        with pytest.raises(ValueError, match="finite"):
            observation(price, time_ns=1000)


@pytest.mark.parametrize("quantity", [D("0"), D("-1"), D("NaN"), True, 1.0])
def test_settlement_requires_positive_valid_native_quantity(quantity):
    plan = settlement_plan(
        spec=linear_spec(),
        quantity_native=quantity,
        delivery=observation("150", time_ns=1000),
        expiry_id="bad-quantity",
    )
    assert plan.status.status == "INVALID"
    assert plan.actions == ()


@pytest.mark.parametrize("provenance", ["fixture", "proxy", "observed"])
def test_delivery_provenance_remains_visible_and_binds_identity(provenance):
    spec = linear_spec()
    delivery = observation("150", time_ns=1000, provenance=provenance)
    plan = settlement_plan(spec=spec, quantity_native=D("1"), delivery=delivery, expiry_id="provenance")
    assert f"delivery_provenance={provenance}" in plan.status.reason
    if provenance != "observed":
        assert "delivery_provenance=observed" not in plan.status.reason
    changed = settlement_plan(
        spec=spec,
        quantity_native=D("1"),
        delivery=replace(delivery, source_id="other-source"),
        expiry_id="provenance",
    )
    assert changed.actions[0].link_id != plan.actions[0].link_id


def test_decimal_calculation_ignores_caller_precision_rounding_and_traps():
    spec = linear_spec(strike="1", min_quantity_native=D(".000001"), quantity_step_native=D(".000001"))
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        context.traps[Inexact] = context.traps[Rounded] = True
        plan = settlement_plan(
            spec=spec,
            quantity_native=D("1.234567"),
            delivery=observation("2.23456789", time_ns=1000),
            expiry_id="precision",
        )
        assert intrinsic_value(spec=spec, delivery_price=D("2.23456789")) == D("1.23456789")
        assert plan.actions[0].amount == D("1.52415677625363")
        assert expiry_payoff(
            legs=(PayoffLeg(spec=spec, signed_quantity_native=D("-1.234567")),),
            delivery_prices=((spec.instrument_id, D("2.23456789")),),
        ) == (("USDT", D("-1.52415677625363")),)
        assert context.prec == 2 and context.traps[Inexact]


def test_intrinsic_rounds_a_fifty_digit_tie_with_half_even():
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        context.traps[Inexact] = context.traps[Rounded] = True
        assert intrinsic_value(
            spec=linear_spec(strike="1"),
            delivery_price=D("1.123456789012345678901234567890123456789012345678905"),
        ) == D(".12345678901234567890123456789012345678901234567890")


@pytest.mark.parametrize(
    "prices", [(), (("unknown", D("150")),), (("BTC-1000-100-C", D("150")), ("BTC-1000-100-C", D("150")))]
)
def test_delivery_price_identity_is_complete_and_unambiguous(prices):
    with pytest.raises(ValueError):
        expiry_payoff(legs=(PayoffLeg(spec=linear_spec(), signed_quantity_native=D("1")),), delivery_prices=prices)


def test_empty_static_portfolio_has_no_currency_cashflow():
    assert expiry_payoff(legs=(), delivery_prices=()) == ()


def test_settlement_outputs_are_frozen_validated_records():
    plan = settlement_plan(
        spec=linear_spec(),
        quantity_native=D("1"),
        delivery=observation("150", time_ns=1000),
        expiry_id="frozen",
    )
    with pytest.raises(FrozenInstanceError):
        plan.actions[0].amount = D("999")
    with pytest.raises(TypeError, match="tuple"):
        SettlementPlan(status=plan.status, actions=list(plan.actions))
    with pytest.raises(ValueError):
        SettlementAction(
            kind="USER_FUTURES_ORDER",
            option_id="fixture",
            quantity_native=D("1"),
            future_quantity_base=D("1"),
            amount=D("0"),
            currency="USDT",
            link_id="fixture",
        )


@pytest.mark.parametrize("quantity", [D(".001"), D(".015"), D("2")])
def test_actual_settlement_residual_is_not_a_new_order(quantity):
    spec = linear_spec(min_quantity_native=D(".02"), max_quantity_native=D("1"))
    plan = settlement_plan(
        spec=spec, quantity_native=quantity, delivery=observation("150", time_ns=1000), expiry_id="residual"
    )
    assert plan.status.status == "VALID"
    assert plan.actions[0].amount == quantity * D("50")
    with pytest.raises(ValueError):
        LegIntent(spec=spec, wallet=wallet(), quantity_native=quantity)
