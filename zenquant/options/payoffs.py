"""Linear European terminal math and read-only system settlement claims.

Static signed payoffs are not execution admission. Delivery fees are separate
ledger receipts, never implicit deductions from intrinsic cashflows.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from .contracts import (
    InstrumentSpec,
    Observation,
    PayoffLeg,
    StatusResult,
    _choice,
    _decimal,
    _set_decimal,
    _text,
    _tuple,
    base_quantity,
    fingerprint,
    validate_quantity,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SettlementAction:
    kind: str
    option_id: str
    quantity_native: Decimal
    future_quantity_base: Decimal
    amount: Decimal
    currency: str
    link_id: str

    def __post_init__(self) -> None:
        _choice(self.kind, "kind", ("EXPIRY", "CASH_SETTLEMENT", "EXERCISE_TO_FUTURE", "FUTURE_DELIVERY"))
        for name in ("option_id", "currency", "link_id"):
            _text(getattr(self, name), name)
        _set_decimal(self, "quantity_native", positive=True)
        _set_decimal(self, "future_quantity_base")
        _set_decimal(self, "amount", nonnegative=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class SettlementPlan:
    status: StatusResult
    actions: tuple[SettlementAction, ...]
    delivery: Observation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, StatusResult):
            raise TypeError("status must be StatusResult")
        _tuple(self.actions, "actions", SettlementAction)
        if self.delivery is not None and not isinstance(self.delivery, Observation):
            raise TypeError("delivery must be Observation or None")


def _support_status(spec: InstrumentSpec) -> StatusResult | None:
    if not isinstance(spec, InstrumentSpec):
        raise TypeError("spec must be InstrumentSpec")
    if spec.product != "linear_option" or spec.exercise_style != "european":
        return StatusResult(status="UNSUPPORTED", reason="only linear European option economics are supported")
    if len({spec.quote_currency, spec.strike_currency, spec.settlement_currency, spec.margin_currency}) != 1:
        return StatusResult(status="UNSUPPORTED", reason="quote, strike, settlement and margin currencies must agree")
    if spec.settlement_rule not in ("linear_cash", "linear_via_future"):
        return StatusResult(status="UNSUPPORTED", reason="linear settlement rule is unsupported")
    if spec.valid_from_ns > spec.expiry_ns or (
        spec.valid_until_ns is not None and spec.expiry_ns >= spec.valid_until_ns
    ):
        return StatusResult(status="NOT_EVALUABLE", reason="rule version is not effective at contractual expiry")
    return None


def intrinsic_value(*, spec: InstrumentSpec, delivery_price: Decimal | str) -> Decimal:
    """Return per-base intrinsic; unsupported terminal economics raise explicitly."""
    status = _support_status(spec)
    if status is not None:
        raise ValueError(f"{status.status}: {status.reason}")
    price = _decimal(delivery_price, "delivery_price", nonnegative=True)
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        signed = price - spec.strike if spec.option_type == "C" else spec.strike - price
        return _decimal(max(signed, Decimal("0")), "intrinsic_value", nonnegative=True)


def settlement_plan(
    *,
    spec: InstrumentSpec,
    quantity_native: Decimal | str,
    delivery: Observation | None,
    expiry_id: str,
) -> SettlementPlan:
    """Build terminal cashflows using the fixing's explicit knowledge time.

    Normal receipt/publication delay is allowed. This interface has no external
    caller cutoff: a consuming ledger must enforce event.available_ns >=
    plan.delivery.available_ns before applying any generated action.
    """
    _text(expiry_id, "expiry_id")
    if delivery is not None and not isinstance(delivery, Observation):
        raise TypeError("delivery must be Observation or None")

    def failure(status: str, reason: str) -> SettlementPlan:
        return SettlementPlan(status=StatusResult(status=status, reason=reason), actions=(), delivery=delivery)

    support = _support_status(spec)
    if support is not None:
        return failure(support.status, support.reason)
    quantity_status = validate_quantity(spec, quantity_native)
    if quantity_status.status != "VALID":
        return failure(quantity_status.status, quantity_status.reason)
    if delivery is None:
        return failure("NOT_EVALUABLE", "final delivery fixing is missing")
    if delivery.source_ns < spec.expiry_ns:
        return failure("NOT_EVALUABLE", "delivery source precedes contractual expiry")
    if delivery.source_ns > delivery.received_ns or delivery.source_ns > delivery.available_ns:
        return failure("NOT_EVALUABLE", "final delivery source is newer than its knowledge time")
    if delivery.value < 0:
        return failure("INVALID", "delivery price must be nonnegative")

    quantity = _decimal(quantity_native, "quantity_native", positive=True)
    link_id = fingerprint((spec, quantity, delivery, expiry_id))

    def action(
        kind: str, *, future_quantity: Decimal = Decimal("0"), amount: Decimal = Decimal("0")
    ) -> SettlementAction:
        return SettlementAction(
            kind=kind,
            option_id=spec.instrument_id,
            quantity_native=quantity,
            future_quantity_base=future_quantity,
            amount=amount,
            currency=spec.settlement_currency,
            link_id=link_id,
        )

    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        intrinsic = intrinsic_value(spec=spec, delivery_price=delivery.value)
        q_base = base_quantity(spec, quantity)
        amount = _decimal(intrinsic * q_base, "settlement_amount", nonnegative=True)
        if intrinsic == 0:
            actions = (action("EXPIRY"),)
        elif spec.settlement_rule == "linear_cash":
            actions = (action("CASH_SETTLEMENT", amount=amount),)
        else:
            future_quantity = q_base if spec.option_type == "C" else q_base.copy_negate()
            actions = (
                action("EXERCISE_TO_FUTURE", future_quantity=future_quantity),
                action("FUTURE_DELIVERY", future_quantity=future_quantity, amount=amount),
            )
    reason = (
        "terminal model checked; system claims grant no trading permission; "
        f"spec_provenance={spec.provenance}; delivery_provenance={delivery.provenance}; "
        f"delivery_source_id={delivery.source_id}; source_ns={delivery.source_ns}; "
        f"received_ns={delivery.received_ns}; available_ns={delivery.available_ns}; rule_version={spec.rule_version}"
    )
    return SettlementPlan(status=StatusResult(status="VALID", reason=reason), actions=actions, delivery=delivery)


def expiry_payoff(
    *, legs: tuple[PayoffLeg, ...], delivery_prices: tuple[tuple[str, Decimal], ...]
) -> tuple[tuple[str, Decimal], ...]:
    """Static signed cashflows by currency; finite loss says nothing about margin.

    Price keys are native instrument IDs. Ambiguous identities across venues or
    economics are refused rather than silently assigning a shared fixing.
    """
    _tuple(legs, "legs", PayoffLeg)
    _tuple(delivery_prices, "delivery_prices", tuple)
    prices: dict[str, Decimal] = {}
    for item in delivery_prices:
        if len(item) != 2:
            raise ValueError("delivery price must contain instrument_id and price")
        instrument_id, price = item
        _text(instrument_id, "instrument_id")
        if instrument_id in prices:
            raise ValueError("duplicate delivery price instrument_id")
        prices[instrument_id] = _decimal(price, "delivery_price", nonnegative=True)
    identities: dict[str, InstrumentSpec] = {}
    for leg in legs:
        if leg.spec.instrument_id in identities and identities[leg.spec.instrument_id] != leg.spec:
            raise ValueError("ambiguous native instrument_id has distinct venue or economics")
        identities[leg.spec.instrument_id] = leg.spec
    if set(prices) != set(identities):
        raise ValueError("delivery price identities must exactly cover payoff legs")
    cashflows: dict[str, Decimal] = {}
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        for leg in legs:
            value = intrinsic_value(spec=leg.spec, delivery_price=prices[leg.spec.instrument_id])
            quantity = base_quantity(leg.spec, leg.signed_quantity_native.copy_abs())
            if leg.signed_quantity_native < 0:
                quantity = quantity.copy_negate()
            currency = leg.spec.settlement_currency
            cashflows[currency] = _decimal(cashflows.get(currency, Decimal("0")) + value * quantity, "cashflow")
    return tuple(sorted(cashflows.items()))
