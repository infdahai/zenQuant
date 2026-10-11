"""Causal, provenance-bound displayed option costs; never execution permission."""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from .contracts import (
    Book,
    BookLevel,
    Observation,
    StatusResult,
    _choice,
    _integer,
    _on_step,
    _set_decimal,
    _text,
    _tuple,
    base_quantity,
    fingerprint,
    validate_quantity,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class FeePolicy:
    rate: Decimal
    premium_cap: Decimal
    fee_currency: str
    provenance: str

    def __post_init__(self) -> None:
        _set_decimal(self, "rate", nonnegative=True)
        _set_decimal(self, "premium_cap", nonnegative=True)
        _text(self.fee_currency, "fee_currency")
        _choice(self.provenance, "provenance", ("fixture", "observed", "proxy"))


@dataclass(frozen=True, slots=True, kw_only=True)
class TimingPolicy:
    max_age_ns: int
    minimum_source_age_ns: int

    def __post_init__(self) -> None:
        _integer(self.max_age_ns, "max_age_ns")
        if type(self.minimum_source_age_ns) is not int:
            raise TypeError("minimum_source_age_ns must be a strict integer")
        if self.minimum_source_age_ns > self.max_age_ns:
            raise ValueError("minimum_source_age_ns must not exceed max_age_ns")


@dataclass(frozen=True, slots=True, kw_only=True)
class CostEvidence:
    status: StatusResult
    instrument_hash: str
    book_hash: str
    side: str
    quote_currency: str
    report_currency: str
    asof_ns: int
    quantity_native: Decimal
    premium_native: Decimal | None
    fee_native: Decimal | None
    total_report_currency: Decimal | None
    assumptions: tuple[str, ...]
    input_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, StatusResult):
            raise TypeError("status must be StatusResult")
        for name in ("instrument_hash", "book_hash", "quote_currency", "report_currency", "input_hash"):
            _text(getattr(self, name), name)
        _choice(self.side, "side", ("BUY", "SELL"))
        _integer(self.asof_ns, "asof_ns")
        _set_decimal(self, "quantity_native")
        for name in ("premium_native", "fee_native", "total_report_currency"):
            if getattr(self, name) is not None:
                _set_decimal(self, name, nonnegative=name != "total_report_currency")
        _tuple(self.assumptions, "assumptions", str)
        for assumption in self.assumptions:
            _text(assumption, "assumption")


def _consume(levels: tuple[BookLevel, ...], quantity_native: Decimal) -> Decimal | None:
    """Accumulate native price times native quantity before computing a mean."""
    left, paid = quantity_native, Decimal("0")
    for level in levels:
        taken = min(left, level.quantity_native)
        paid += taken * level.price
        left -= taken
        if left == 0:
            return paid
    return None


def vwap(levels: tuple[BookLevel, ...], quantity_native: Decimal) -> Decimal | None:
    """Consume displayed depth; incomplete depth has no inferred VWAP."""
    if not isinstance(quantity_native, Decimal) or not quantity_native.is_finite() or quantity_native <= 0:
        raise ValueError("POSITIVE_FINITE_QUANTITY_REQUIRED")
    _tuple(levels, "levels", BookLevel)
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        paid = _consume(levels, quantity_native)
        return None if paid is None else paid / quantity_native


def _book_status(book: Book) -> StatusResult | None:
    for levels, ascending in ((book.asks, True), (book.bids, False)):
        for previous, current in zip(levels, levels[1:]):  # noqa: RUF007 - core stdlib import whitelist excludes itertools
            if (ascending and previous.price >= current.price) or (not ascending and previous.price <= current.price):
                return StatusResult(status="INVALID", reason="book prices must be strictly ordered")
        for level in levels:
            tick = book.spec.tick_bands[0][1]
            for lower, band_tick in book.spec.tick_bands:
                if level.price < lower:
                    break
                tick = band_tick
            if not _on_step(level.price, tick):
                return StatusResult(status="INVALID", reason="book price is off native tick")
    if not book.asks or not book.bids:
        return StatusResult(status="NOT_EVALUABLE", reason="two-sided displayed book required")
    if book.bids[0].price >= book.asks[0].price:
        return StatusResult(status="INVALID", reason="crossed or locked book")
    return None


def quote_cost(
    *,
    book: Book,
    side: str,
    quantity_native: Decimal,
    index_in_quote: Observation | None,
    quote_to_report: Observation | None,
    fee_policy: FeePolicy | None,
    timing: TimingPolicy,
    asof_ns: int,
    report_currency: str,
) -> CostEvidence:
    """Diagnose BUY spend or SELL net proceeds at a supplied joint cutoff.

    The index must already be explicitly quote-denominated; unsupported fee
    currencies fail closed. Native prices do not assert that strike, settlement,
    margin, or wallet currencies are interchangeable. Inverse and SELL costs
    remain price diagnostics, with operational admission owned by risk.
    """
    if not isinstance(book, Book) or not isinstance(timing, TimingPolicy):
        raise TypeError("book and timing must be Book and TimingPolicy")
    _choice(side, "side", ("BUY", "SELL"))
    _integer(asof_ns, "asof_ns")
    _text(report_currency, "report_currency")
    if not isinstance(quantity_native, Decimal) or not quantity_native.is_finite():
        raise ValueError("FINITE_DECIMAL_QUANTITY_REQUIRED")
    for value in (index_in_quote, quote_to_report):
        if value is not None and not isinstance(value, Observation):
            raise TypeError("index and FX must be Observation or None")
    if fee_policy is not None and not isinstance(fee_policy, FeePolicy):
        raise TypeError("fee_policy must be FeePolicy or None")

    dependencies = (("book", book), ("index_in_quote", index_in_quote), ("quote_to_report", quote_to_report))
    assumptions = ["displayed_depth_only", "no_execution_permission"]
    if side == "SELL":
        assumptions.append("sell_exit_or_price_diagnostic_only")
    provenance_inputs = (("instrument", book.spec), ("fee_policy", fee_policy)) + dependencies[1:]
    for name, value in provenance_inputs:
        if value is not None:
            assumptions.append(f"{value.provenance}:{name}")
    for name, value in dependencies:
        if value is not None and value.source_ns > asof_ns:
            assumptions.append(f"source_clock_lead:{name}")

    input_hash = fingerprint(
        {
            "book": book,
            "side": side,
            "quantity_native": quantity_native,
            "index_in_quote": index_in_quote,
            "quote_to_report": quote_to_report,
            "fee_policy": fee_policy,
            "timing": timing,
            "asof_ns": asof_ns,
            "report_currency": report_currency,
        }
    )

    def evidence(
        status: str,
        reason: str,
        *,
        premium: Decimal | None = None,
        fee: Decimal | None = None,
        total: Decimal | None = None,
    ) -> CostEvidence:
        return CostEvidence(
            status=StatusResult(status=status, reason=reason),
            instrument_hash=fingerprint(book.spec),
            book_hash=fingerprint(book),
            side=side,
            quote_currency=book.spec.quote_currency,
            report_currency=report_currency,
            asof_ns=asof_ns,
            quantity_native=quantity_native,
            premium_native=premium,
            fee_native=fee,
            total_report_currency=total,
            assumptions=tuple(assumptions),
            input_hash=input_hash,
        )

    quantity_status = validate_quantity(book.spec, quantity_native)
    if quantity_status.status != "VALID":
        return evidence(quantity_status.status, quantity_status.reason)
    book_status = _book_status(book)
    if book_status is not None:
        return evidence(book_status.status, book_status.reason)
    if fee_policy is None:
        return evidence("NOT_EVALUABLE", "fee policy is missing")
    if fee_policy.fee_currency != book.spec.quote_currency:
        return evidence("NOT_EVALUABLE", "fee denomination conversion is unsupported")
    for name, value in dependencies:
        if value is None:
            return evidence("NOT_EVALUABLE", f"{name} is missing")
        if value.received_ns > asof_ns or value.available_ns > asof_ns:
            return evidence("NOT_EVALUABLE", f"{name} was not available at decision cutoff")
        age = asof_ns - value.source_ns
        if not timing.minimum_source_age_ns <= age <= timing.max_age_ns:
            return evidence("NOT_EVALUABLE", f"{name} source age is outside timing policy")
    # Required observations have passed the None/type/causality gates above.
    assert index_in_quote is not None and quote_to_report is not None
    if index_in_quote.value <= 0 or quote_to_report.value <= 0:
        return evidence("INVALID", "index and FX must be positive")
    if report_currency == book.spec.quote_currency and quote_to_report.value != 1:
        return evidence("INVALID", "same-currency FX must be exact identity")

    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        paid = _consume(book.asks if side == "BUY" else book.bids, quantity_native)
        if paid is None:
            return evidence("NOT_EVALUABLE", "insufficient displayed depth")
        premium = paid * (book.spec.multiplier if book.spec.price_unit == "quote_per_base" else Decimal("1"))
        fee = min(
            fee_policy.rate * index_in_quote.value * base_quantity(book.spec, quantity_native),
            fee_policy.premium_cap * premium,
        )
        native_total = premium + fee if side == "BUY" else premium - fee
        total = native_total * quote_to_report.value
        return evidence(
            "VALID", "displayed native cash cost checked; no execution admission", premium=premium, fee=fee, total=total
        )
