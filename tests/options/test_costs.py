"""Displayed costs are cash diagnostics backed by causal, complete inputs."""

from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_UP, Inexact, localcontext
from decimal import Decimal as D

import pytest

from zenquant.options.contracts import BookLevel, fingerprint
from zenquant.options.costs import FeePolicy, TimingPolicy, quote_cost, vwap

from .factories import book_for, linear_spec, observation


def cost_inputs(**changes):
    inputs = dict(
        book=book_for(linear_spec(), asks=(("1000", ".01"), ("1100", ".01")), bids=(("900", ".02"),)),
        side="BUY",
        quantity_native=D(".02"),
        index_in_quote=observation("80000"),
        quote_to_report=observation("1"),
        fee_policy=FeePolicy(rate=D(".0003"), premium_cap=D(".07"), fee_currency="USDT", provenance="fixture"),
        timing=TimingPolicy(max_age_ns=10, minimum_source_age_ns=0),
        asof_ns=10,
        report_currency="USDT",
    )
    return inputs | changes


def test_vwap_and_incomplete_depth():
    levels = (
        BookLevel(price=D("1000"), quantity_native=D(".01")),
        BookLevel(price=D("1100"), quantity_native=D(".01")),
    )
    assert vwap(levels, D(".02")) == D("1050")
    assert vwap(levels, D(".03")) is None
    assert vwap((), D(".01")) is None


@pytest.mark.parametrize("quantity", [D("0"), D("-.01"), D("NaN"), D("Infinity"), 0.01, True])
def test_vwap_rejects_nonpositive_or_inexact_quantity_input(quantity):
    with pytest.raises(ValueError):
        vwap((), quantity)


def test_multilevel_buy_uses_all_premium_and_not_double_fee():
    result = quote_cost(**cost_inputs())
    assert result.status.status == "VALID"
    assert result.premium_native == D("21")
    assert result.fee_native == D(".48")
    assert result.total_report_currency == D("21.48")
    assert result.instrument_hash == fingerprint(cost_inputs()["book"].spec)
    assert result.book_hash == fingerprint(cost_inputs()["book"])
    assert result.asof_ns == 10
    assert result.quote_currency == result.report_currency == "USDT"
    assert "displayed_depth_only" in result.assumptions
    assert "no_execution_permission" in result.assumptions


def test_fee_cap_is_not_notional_fee_added_twice():
    book = book_for(linear_spec(), asks=(("100", ".01"),), bids=(("90", ".01"),))
    result = quote_cost(**cost_inputs(book=book, quantity_native=D(".01")))
    assert result.premium_native == D("1")
    assert result.fee_native == D(".07")
    assert result.total_report_currency == D("1.07")


def test_sell_reports_net_exit_proceeds_without_open_short_permission():
    result = quote_cost(**cost_inputs(side="SELL"))
    assert result.premium_native == D("18")
    assert result.fee_native == D(".48")
    assert result.total_report_currency == D("17.52")
    assert "sell_exit_or_price_diagnostic_only" in result.assumptions


def test_contract_units_convert_premium_and_fee_separately():
    spec = linear_spec(quantity_unit="contracts", multiplier=D(".01"), price_unit="quote_per_contract")
    book = book_for(spec, asks=(("10", "1"), ("11", "1")), bids=(("9", "2"),))
    result = quote_cost(**cost_inputs(book=book, quantity_native=D("2")))
    assert result.premium_native == D("21")
    assert result.fee_native == D(".48")


@pytest.mark.parametrize("dependency", ["index_in_quote", "quote_to_report", "fee_policy"])
def test_missing_required_dependency_is_unknown_not_zero(dependency):
    result = quote_cost(**cost_inputs(**{dependency: None}))
    assert result.status.status == "NOT_EVALUABLE"
    assert result.fee_native is result.total_report_currency is None


@pytest.mark.parametrize("dependency", ["book", "index_in_quote", "quote_to_report"])
def test_stale_dependency_blocks_cost(dependency):
    inputs = cost_inputs(asof_ns=21)
    result = quote_cost(**inputs)
    assert result.status.status == "NOT_EVALUABLE"
    inputs[dependency] = replace(inputs[dependency], source_ns=11)
    # Making just one source fresh cannot rescue the other stale dependencies.
    assert quote_cost(**inputs).total_report_currency is None


@pytest.mark.parametrize("dependency", ["book", "index_in_quote", "quote_to_report"])
def test_future_available_dependency_cannot_be_used(dependency):
    inputs = cost_inputs()
    inputs[dependency] = replace(inputs[dependency], available_ns=11)
    result = quote_cost(**inputs)
    assert result.status.status == "NOT_EVALUABLE"
    assert result.total_report_currency is None
    assert result.asof_ns == 10


def test_joint_decision_cutoff_covers_all_dependencies():
    result = quote_cost(**cost_inputs(index_in_quote=observation("80000", time_ns=12), asof_ns=12))
    assert result.status.status == "VALID"
    assert result.asof_ns == 12


def test_source_clock_lead_requires_frozen_tolerance_and_marks_quality():
    index = replace(observation("80000"), source_ns=12)
    assert quote_cost(**cost_inputs(index_in_quote=index)).status.status == "NOT_EVALUABLE"
    result = quote_cost(
        **cost_inputs(index_in_quote=index, timing=TimingPolicy(max_age_ns=10, minimum_source_age_ns=-2))
    )
    assert result.status.status == "VALID"
    assert "source_clock_lead:index_in_quote" in result.assumptions
    future = replace(index, available_ns=11)
    assert (
        quote_cost(
            **cost_inputs(index_in_quote=future, timing=TimingPolicy(max_age_ns=10, minimum_source_age_ns=-2))
        ).status.status
        == "NOT_EVALUABLE"
    )


@pytest.mark.parametrize("quantity", [D(".001"), D(".015"), D(".03")])
def test_illegal_lot_or_missing_depth_produces_no_cost(quantity):
    result = quote_cost(**cost_inputs(quantity_native=quantity))
    assert result.status.status == ("NOT_EVALUABLE" if quantity == D(".03") else "INVALID")
    assert result.premium_native is result.fee_native is result.total_report_currency is None


@pytest.mark.parametrize("asks,bids", [((), ()), ((("1000", ".01"),), ()), ((), (("900", ".01"),))])
def test_empty_or_one_sided_book_is_not_evaluable(asks, bids):
    result = quote_cost(**cost_inputs(book=book_for(linear_spec(), asks=asks, bids=bids)))
    assert result.status.status == "NOT_EVALUABLE"
    assert result.total_report_currency is None


@pytest.mark.parametrize(
    "asks,bids",
    [
        ((("900", ".02"),), (("900", ".02"),)),
        ((("1100", ".01"), ("1000", ".01")), (("900", ".02"),)),
        ((("1000", ".02"),), (("800", ".01"), ("900", ".01"))),
        ((("1000.001", ".02"),), (("900", ".02"),)),
    ],
)
def test_crossed_unsorted_and_off_tick_books_are_invalid(asks, bids):
    result = quote_cost(**cost_inputs(book=book_for(linear_spec(), asks=asks, bids=bids)))
    assert result.status.status == "INVALID"
    assert result.total_report_currency is None


def test_tick_band_uses_each_level_native_price():
    spec = linear_spec(tick_bands=((D("0"), D(".01")), (D("1000"), D("1"))))
    book = book_for(spec, asks=(("999.99", ".01"), ("1001", ".01")), bids=(("999", ".02"),))
    assert quote_cost(**cost_inputs(book=book)).premium_native == D("20.0099")
    invalid = book_for(spec, asks=(("999.99", ".01"), ("1000.01", ".01")), bids=(("999", ".02"),))
    assert quote_cost(**cost_inputs(book=invalid)).status.status == "INVALID"


@pytest.mark.parametrize("dependency", ["index_in_quote", "quote_to_report"])
def test_nonpositive_price_conversion_is_invalid(dependency):
    result = quote_cost(**cost_inputs(**{dependency: observation("0")}))
    assert result.status.status == "INVALID"
    assert result.total_report_currency is None


def test_identity_fx_is_explicit_and_exact():
    assert quote_cost(**cost_inputs(quote_to_report=None)).status.status == "NOT_EVALUABLE"
    assert quote_cost(**cost_inputs(quote_to_report=observation("1.001"))).status.status == "INVALID"


def test_proxy_fx_is_visible_and_not_promoted_to_observed():
    result = quote_cost(**cost_inputs(report_currency="USD", quote_to_report=observation(".99", provenance="proxy")))
    assert result.total_report_currency == D("21.2652")
    assert "proxy:quote_to_report" in result.assumptions


def test_proxy_spec_book_index_and_fee_remain_visible():
    inputs = cost_inputs(index_in_quote=observation("80000", provenance="proxy"))
    inputs["book"] = replace(inputs["book"], spec=replace(inputs["book"].spec, provenance="proxy"))
    inputs["fee_policy"] = replace(inputs["fee_policy"], provenance="proxy")
    result = quote_cost(**inputs)
    assert {"proxy:instrument", "proxy:index_in_quote", "proxy:fee_policy"} <= set(result.assumptions)


def test_unsupported_fee_denomination_fails_closed():
    policy = replace(cost_inputs()["fee_policy"], fee_currency="BTC")
    result = quote_cost(**cost_inputs(fee_policy=policy))
    assert result.status.status == "NOT_EVALUABLE"
    assert result.fee_native is result.total_report_currency is None


def test_explicit_zero_fee_is_known():
    policy = replace(cost_inputs()["fee_policy"], rate=D("0"))
    result = quote_cost(**cost_inputs(fee_policy=policy))
    assert result.fee_native == D("0")
    assert result.total_report_currency == D("21")


def test_native_inverse_and_currency_variants_are_only_price_diagnostics():
    spec = linear_spec(
        product="inverse_option",
        quote_currency="BTC",
        settlement_currency="BTC",
        margin_currency="BTC",
        settlement_rule="inverse_via_future",
    )
    book = book_for(spec, asks=((".02", ".02"),), bids=((".01", ".02"),))
    policy = FeePolicy(rate=D(".0003"), premium_cap=D(".07"), fee_currency="BTC", provenance="fixture")
    result = quote_cost(
        **cost_inputs(
            book=book,
            index_in_quote=observation("1"),
            quote_to_report=observation("80000"),
            report_currency="USD",
            fee_policy=policy,
        )
    )
    assert result.premium_native == D(".0004")
    assert result.fee_native == D(".000006")
    assert result.total_report_currency == D("32.48")
    assert "no_execution_permission" in result.assumptions
    variant = replace(
        cost_inputs()["book"],
        spec=linear_spec(strike_currency="USD", settlement_currency="BTC", margin_currency="USDC"),
    )
    assert quote_cost(**cost_inputs(book=variant)).total_report_currency == D("21.48")


def test_caller_decimal_precision_rounding_and_traps_do_not_change_cash_or_hash():
    spec = linear_spec(
        min_quantity_native=D("1"), quantity_step_native=D("1"), tick_bands=((D("0"), D(".00000000000000000001")),)
    )
    book = book_for(spec, asks=((".1", "1"), (".2", "2")), bids=((".09", "3"),))
    inputs = cost_inputs(book=book, quantity_native=D("3"))
    normal = quote_cost(**inputs)
    assert normal.premium_native == D(".5")
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.rounding = ROUND_UP
        ctx.traps[Inexact] = True
        assert quote_cost(**inputs) == normal
        assert vwap(book.asks, D("3")) == D(".16666666666666666666666666666666666666666666666667")


def test_input_hash_binds_fx_timing_fee_side_quantity_and_cutoff():
    inputs = cost_inputs()
    baseline = quote_cost(**inputs).input_hash
    variants = [
        {"side": "SELL"},
        {"quantity_native": D(".01")},
        {"asof_ns": 11},
        {"report_currency": "USD", "quote_to_report": observation(".99")},
        {"timing": TimingPolicy(max_age_ns=11, minimum_source_age_ns=0)},
        {"fee_policy": replace(inputs["fee_policy"], rate=D(".0002"))},
    ]
    assert all(quote_cost(**(inputs | change)).input_hash != baseline for change in variants)


@pytest.mark.parametrize(
    "changes",
    [
        {"rate": D("NaN")},
        {"rate": D("-.1")},
        {"premium_cap": D("-.1")},
        {"fee_currency": ""},
        {"provenance": "verified"},
        {"rate": 0.001},
    ],
)
def test_fee_policy_rejects_invalid_contract_inputs(changes):
    with pytest.raises((ValueError, TypeError)):
        replace(cost_inputs()["fee_policy"], **changes)


@pytest.mark.parametrize(
    "changes", [{"max_age_ns": True}, {"max_age_ns": -1}, {"minimum_source_age_ns": 1.0}, {"minimum_source_age_ns": 11}]
)
def test_timing_policy_rejects_ambiguous_or_empty_window(changes):
    with pytest.raises((ValueError, TypeError)):
        TimingPolicy(**({"max_age_ns": 10, "minimum_source_age_ns": 0} | changes))


def test_cost_evidence_is_immutable():
    result = quote_cost(**cost_inputs())
    with pytest.raises(FrozenInstanceError):
        result.fee_native = D("0")
