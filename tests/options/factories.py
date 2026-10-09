"""Explicit, immutable options fixtures shared by focused kernel tests."""

from dataclasses import replace
from decimal import Decimal
from typing import Any

from zenquant.options.contracts import Book, BookLevel, EventKey, InstrumentSpec, Observation, WalletKey


def linear_spec(
    *,
    underlying: str = "BTC",
    option_type: str = "C",
    strike: str = "100",
    expiry_ns: int = 1000,
    settlement_rule: str = "linear_cash",
    **changes: Any,
) -> InstrumentSpec:
    spec = InstrumentSpec(
        venue="FIXTURE",
        instrument_id=f"{underlying}-{expiry_ns}-{strike}-{option_type}",
        product="linear_option",
        underlying=underlying,
        quote_currency="USDT",
        strike_currency="USDT",
        settlement_currency="USDT",
        margin_currency="USDT",
        expiry_ns=expiry_ns,
        strike=Decimal(strike) if isinstance(strike, str) else strike,
        option_type=option_type,
        exercise_style="european",
        quantity_unit="base",
        multiplier=Decimal("1"),
        price_unit="quote_per_base",
        min_quantity_native=Decimal(".01"),
        quantity_step_native=Decimal(".01"),
        max_quantity_native=None,
        tick_bands=((Decimal("0"), Decimal(".01")),),
        settlement_index=f"{underlying}-USDT",
        settlement_window_ns=1,
        settlement_rule=settlement_rule,
        rule_version="fixture.v1",
        metadata_hash="fixture-metadata",
        valid_from_ns=0,
        valid_until_ns=None,
        provenance="fixture",
    )
    return replace(spec, **changes)


def wallet(currency: str = "USDT", venue: str = "FIXTURE") -> WalletKey:
    return WalletKey(venue=venue, account="fixture-account", account_mode="standard", currency=currency)


def key(n: int, *, event_ns: int | None = None, event_id: str | None = None) -> EventKey:
    return EventKey(
        event_id=event_id if event_id is not None else f"event-{n}",
        source="fixture",
        event_ns=n if event_ns is None else event_ns,
        received_ns=n,
        available_ns=n,
        journal_sequence=n,
    )


def book_for(
    spec: InstrumentSpec,
    *,
    asks: tuple[tuple[str, str], ...],
    bids: tuple[tuple[str, str], ...],
    time_ns: int = 10,
) -> Book:
    return Book(
        spec=spec,
        asks=tuple(BookLevel(price=Decimal(price), quantity_native=Decimal(qty)) for price, qty in asks),
        bids=tuple(BookLevel(price=Decimal(price), quantity_native=Decimal(qty)) for price, qty in bids),
        source_ns=time_ns,
        received_ns=time_ns,
        available_ns=time_ns,
        raw_hash="fixture-book",
    )


def observation(value: Decimal | str, *, time_ns: int = 10, provenance: str = "fixture") -> Observation:
    return Observation(
        value=value,
        source_ns=time_ns,
        received_ns=time_ns,
        available_ns=time_ns,
        source_id="fixture-observation",
        provenance=provenance,
    )
