"""Contract boundaries reject ambiguity before arithmetic or replay."""

from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_UP, Inexact, localcontext
from decimal import Decimal as D
from hashlib import sha256

import pytest

from zenquant.options.contracts import (
    Book,
    BookLevel,
    CombinationIntent,
    EventKey,
    LegIntent,
    Observation,
    PayoffLeg,
    StatusResult,
    base_quantity,
    canonical_payload,
    fingerprint,
    premium_cash,
    validate_quantity,
)

from .factories import key, linear_spec, wallet


def test_contract_quantity_is_not_underlying_quantity():
    spec = linear_spec(quantity_unit="contracts", multiplier=D("1000"))
    assert base_quantity(spec, D("2")) == D("2000")
    assert premium_cash(spec, D(".2"), D("2")) == D("400")


def test_price_per_contract_uses_native_quantity():
    spec = linear_spec(quantity_unit="contracts", multiplier=D("1000"), price_unit="quote_per_contract")
    assert premium_cash(spec, D(".2"), D("2")) == D(".4")


def test_family_and_rule_change_identity():
    spec = linear_spec()
    inverse = replace(
        spec,
        product="inverse_option",
        quote_currency="BTC",
        settlement_currency="BTC",
        margin_currency="BTC",
        settlement_rule="inverse_via_future",
    )
    assert fingerprint(spec) != fingerprint(inverse)
    assert fingerprint(spec) != fingerprint(replace(spec, rule_version="fixture.v2"))
    other_strike_currency = replace(
        spec,
        quote_currency="USDC",
        strike_currency="USDC",
        settlement_currency="USDC",
        margin_currency="USDC",
    )
    assert fingerprint(spec) != fingerprint(other_strike_currency)


@pytest.mark.parametrize(
    "changes",
    [
        {"product": "inverse_option"},
        {"settlement_rule": "inverse_via_future"},
        {"multiplier": D("2")},
        {"product": "spot"},
        {"option_type": "call"},
        {"quantity_unit": "lots"},
        {"price_unit": "float"},
        {"exercise_style": ""},
        {"venue": " "},
        {"provenance": "verified"},
        {"expiry_ns": True},
        {"expiry_ns": 1.0},
        {"expiry_ns": 0},
        {"valid_from_ns": -1},
        {"valid_until_ns": 0},
        {"valid_from_ns": 1000},
        {"settlement_window_ns": 0},
        {"strike": D("0")},
        {"strike": D("NaN")},
        {"multiplier": D("Infinity")},
        {"strike": 100.0},
        {"min_quantity_native": D("0")},
        {"quantity_step_native": D("-1")},
        {"min_quantity_native": D(".015")},
        {"max_quantity_native": D(".005")},
        {"max_quantity_native": D(".015")},
        {"tick_bands": []},
        {"tick_bands": ()},
        {"tick_bands": ((D("1"), D(".01")),)},
        {"tick_bands": ((D("0"), D("0")),)},
        {"tick_bands": ((D("0"), D(".01")), (D("0"), D(".02")))},
        {"tick_bands": ((D("0"), D(".01")), (D("-.1"), D(".02")))},
        {"tick_bands": ([D("0"), D(".01")],)},
    ],
)
def test_instrument_rejects_invalid_metadata(changes):
    with pytest.raises((ValueError, TypeError)):
        linear_spec(**changes)


def test_decimal_strings_are_exact_and_unsupported_style_is_representable():
    spec = linear_spec(multiplier="1", exercise_style="american")
    assert spec.multiplier == D("1")
    assert spec.exercise_style == "american"


def test_currency_variants_are_representable_before_operational_admission():
    spec = linear_spec(strike_currency="EUR", margin_currency="BTC")
    assert spec.strike_currency == "EUR"
    assert spec.margin_currency == "BTC"


@pytest.mark.parametrize("quantity", [D("0"), D("-.01"), D(".015"), D(".04")])
def test_invalid_native_quantity_is_never_rounded(quantity):
    spec = linear_spec(max_quantity_native=D(".03"))
    assert validate_quantity(spec, quantity).status == "INVALID"
    with pytest.raises(ValueError):
        base_quantity(spec, quantity)
    with pytest.raises(ValueError):
        premium_cash(spec, D("1"), quantity)


@pytest.mark.parametrize("quantity", [D("NaN"), D("Infinity"), 0.01, True])
def test_invalid_quantity_representation_cannot_enter_arithmetic(quantity):
    assert validate_quantity(linear_spec(), quantity).status == "INVALID"
    with pytest.raises((ValueError, TypeError)):
        base_quantity(linear_spec(), quantity)


def test_min_step_and_max_boundaries_are_accepted():
    spec = linear_spec(max_quantity_native=D(".03"))
    for quantity in (D(".01"), D(".02"), D(".03")):
        assert validate_quantity(spec, quantity).status == "VALID"
    assert base_quantity(spec, ".02") == D(".02")


def test_arithmetic_ignores_caller_decimal_precision_and_traps():
    spec = linear_spec(quantity_unit="contracts", multiplier=D("123456789.123456789"))
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        assert base_quantity(spec, D("2")) == D("246913578.246913578")
        assert premium_cash(spec, D(".2"), D("2")) == D("49382715.6493827156")
        assert validate_quantity(spec, D("123456789123456789.01")).status == "VALID"


@pytest.mark.parametrize("price", [D("-.01"), D("NaN"), D("Infinity"), 0.1])
def test_premium_rejects_invalid_price(price):
    with pytest.raises((ValueError, TypeError)):
        premium_cash(linear_spec(), price, D("1"))


def test_zero_premium_can_describe_worthless_option():
    assert premium_cash(linear_spec(), D("0"), D("1")) == D("0")


def test_tick_bands_retain_thresholds_as_immutable_values():
    spec = linear_spec(tick_bands=((D("0"), D(".01")), (D("1"), D(".05"))))
    assert spec.tick_bands[1] == (D("1"), D(".05"))
    with pytest.raises(FrozenInstanceError):
        spec.strike = D("200")
    with pytest.raises(TypeError):
        spec.tick_bands[0] = (D("0"), D("1"))


def test_wallet_identity_includes_account_mode_and_currency():
    original = wallet()
    assert len({original, wallet()}) == 1
    assert original != wallet(currency="USDC")
    assert original != replace(original, account="another-account")
    assert original != replace(original, account_mode="portfolio")
    with pytest.raises(ValueError):
        replace(original, currency="")


def test_economic_time_and_knowledge_time_are_independent():
    event = key(10, event_ns=100)
    assert event.event_ns == 100
    assert event.received_ns == event.available_ns == event.journal_sequence == 10
    with pytest.raises(ValueError):
        replace(event, available_ns=9)
    for field in ("event_ns", "received_ns", "available_ns", "journal_sequence"):
        with pytest.raises((ValueError, TypeError)):
            replace(event, **{field: True})
    with pytest.raises(ValueError):
        replace(event, event_id="")


def test_observation_preserves_finite_negative_value_and_provenance():
    observation = Observation(
        value=D("-.001"),
        source_ns=3,
        received_ns=5,
        available_ns=6,
        source_id="rate",
        provenance="observed",
    )
    assert observation.value == D("-.001")
    with pytest.raises(ValueError):
        replace(observation, provenance="verified")
    with pytest.raises(ValueError):
        replace(observation, value=D("NaN"))
    with pytest.raises(ValueError):
        replace(observation, available_ns=4)


def test_book_requires_immutable_levels_and_separate_knowledge_time():
    level = BookLevel(price=D("1"), quantity_native=D(".01"))
    book = Book(
        spec=linear_spec(),
        bids=(level,),
        asks=(replace(level, price=D("2")),),
        source_ns=10,
        received_ns=11,
        available_ns=12,
        raw_hash="raw",
    )
    with pytest.raises(TypeError):
        replace(book, asks=[level])
    with pytest.raises(ValueError):
        replace(book, available_ns=10)
    with pytest.raises(ValueError):
        replace(level, quantity_native=D("0"))
    with pytest.raises(ValueError):
        replace(level, price=D("-1"))


def test_leg_intent_cannot_smuggle_short_quantity():
    leg = LegIntent(spec=linear_spec(), wallet=wallet(), quantity_native=D(".01"))
    with pytest.raises(ValueError):
        replace(leg, quantity_native=D("-.01"))
    with pytest.raises(ValueError):
        replace(leg, quantity_native=D(".015"))
    assert PayoffLeg(spec=linear_spec(), signed_quantity_native=D("-.01")).signed_quantity_native < 0
    with pytest.raises(ValueError):
        PayoffLeg(spec=linear_spec(), signed_quantity_native=D("-.015"))


def test_wallet_mismatch_is_representable_before_operational_admission():
    leg = LegIntent(spec=linear_spec(), wallet=wallet(), quantity_native=D(".01"))
    assert replace(leg, wallet=wallet(currency="BTC")).wallet.currency == "BTC"
    assert replace(leg, wallet=wallet(venue="OTHER")).wallet.venue == "OTHER"


def test_combination_requires_valid_times_and_immutable_leg_collection():
    leg = LegIntent(spec=linear_spec(), wallet=wallet(), quantity_native=D(".01"))
    intent = CombinationIntent(
        intent_id="intent-1",
        legs=(leg,),
        template="long_call",
        sleeve="directional",
        decision_cutoff_ns=1,
        valid_until_ns=2,
        policy_hash="policy",
    )
    for changes in (
        {"legs": []},
        {"legs": ()},
        {"template": "short_call"},
        {"sleeve": "arbitrage"},
        {"valid_until_ns": 1},
        {"decision_cutoff_ns": True},
        {"policy_hash": ""},
    ):
        with pytest.raises((ValueError, TypeError)):
            replace(intent, **changes)


def test_status_validates_vocabulary_without_granting_permission():
    assert StatusResult(status="VALID", reason="input checked").status == "VALID"
    with pytest.raises(ValueError):
        StatusResult(status="APPROVED", reason="trade")
    with pytest.raises(ValueError):
        StatusResult(status="INVALID", reason="")


def test_canonical_payload_has_context_independent_decimal_identity():
    value = {"z": (D("-0.00"), D("1.2300")), "a": D("1E+3")}
    with localcontext() as context:
        context.prec = 2
        assert canonical_payload(value) == {"z": ["0", "1.23"], "a": "1000"}
        assert fingerprint(value) == sha256(b'{"a":"1000","z":["0","1.23"]}').hexdigest()
    assert fingerprint({"a": D("1.23")}) == fingerprint({"a": D("1.230")})
    assert canonical_payload(key(1))["journal_sequence"] == 1
    assert canonical_payload((None, True, 3, "x")) == [None, True, 3, "x"]


@pytest.mark.parametrize("value", [1.0, D("NaN"), D("Infinity"), {1: "x"}, object(), [1]])
def test_canonical_payload_rejects_ambiguous_inputs(value):
    with pytest.raises((ValueError, TypeError)):
        canonical_payload(value)


def test_event_constructor_is_keyword_only():
    with pytest.raises(TypeError):
        EventKey("id", "fixture", 1, 1, 1, 1)
