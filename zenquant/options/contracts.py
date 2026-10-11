"""Immutable options identities, units, and input contracts.

Representation is distinct from operational admission: a currency or exercise
variant can be described here without granting any permission to trade it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from hashlib import sha256
from json import dumps
from typing import Any


def _decimal(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    if isinstance(value, str):
        try:
            value = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{name} must be a decimal string") from exc
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal or a decimal string")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _set_decimal(instance: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> None:
    object.__setattr__(
        instance, name, _decimal(getattr(instance, name), name, positive=positive, nonnegative=nonnegative)
    )


def _text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _texts(instance: Any, *names: str) -> None:
    for name in names:
        _text(getattr(instance, name), name)


def _integer(value: Any, name: str, *, positive: bool = False) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be a strict integer")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")


def _choice(value: Any, name: str, choices: tuple[str, ...]) -> None:
    _text(value, name)
    if value not in choices:
        raise ValueError(f"{name} must be one of {choices}")


def _knowledge_times(instance: Any, source_name: str) -> None:
    for name in (source_name, "received_ns", "available_ns"):
        _integer(getattr(instance, name), name)
    if instance.available_ns < instance.received_ns:
        raise ValueError("available_ns must be at least received_ns")


def _tuple(value: Any, name: str, element_type: type) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be an immutable tuple")
    if not all(isinstance(item, element_type) for item in value):
        raise TypeError(f"{name} contains an invalid element")


def _on_step(value: Decimal, step: Decimal) -> bool:
    # Integer ratios avoid precision loss and caller-context dependent remainder.
    numerator, denominator = value.as_integer_ratio()
    step_numerator, step_denominator = step.as_integer_ratio()
    return (numerator * step_denominator) % (denominator * step_numerator) == 0


@dataclass(frozen=True, slots=True, kw_only=True)
class InstrumentSpec:
    venue: str
    instrument_id: str
    product: str
    underlying: str
    quote_currency: str
    strike_currency: str
    settlement_currency: str
    margin_currency: str
    expiry_ns: int
    strike: Decimal
    option_type: str
    exercise_style: str
    quantity_unit: str
    multiplier: Decimal
    price_unit: str
    min_quantity_native: Decimal
    quantity_step_native: Decimal
    max_quantity_native: Decimal | None
    tick_bands: tuple[tuple[Decimal, Decimal], ...]
    settlement_index: str
    settlement_window_ns: int
    settlement_rule: str
    rule_version: str
    metadata_hash: str
    valid_from_ns: int
    valid_until_ns: int | None
    provenance: str

    def __post_init__(self) -> None:
        _texts(
            self,
            "venue",
            "instrument_id",
            "underlying",
            "quote_currency",
            "strike_currency",
            "settlement_currency",
            "margin_currency",
            "exercise_style",
            "settlement_index",
            "rule_version",
            "metadata_hash",
        )
        _choice(self.product, "product", ("linear_option", "inverse_option"))
        _choice(self.option_type, "option_type", ("C", "P"))
        _choice(self.quantity_unit, "quantity_unit", ("base", "contracts"))
        _choice(self.price_unit, "price_unit", ("quote_per_base", "quote_per_contract"))
        _choice(self.provenance, "provenance", ("fixture", "observed", "proxy"))
        rules = ("linear_cash", "linear_via_future") if self.product == "linear_option" else ("inverse_via_future",)
        _choice(self.settlement_rule, "settlement_rule", rules)
        _integer(self.expiry_ns, "expiry_ns", positive=True)
        _integer(self.settlement_window_ns, "settlement_window_ns", positive=True)
        _integer(self.valid_from_ns, "valid_from_ns")
        if self.valid_from_ns >= self.expiry_ns:
            raise ValueError("valid_from_ns must precede expiry_ns")
        if self.valid_until_ns is not None:
            _integer(self.valid_until_ns, "valid_until_ns")
            if self.valid_until_ns <= self.valid_from_ns:
                raise ValueError("valid_until_ns must follow valid_from_ns")
        for name in ("strike", "multiplier", "min_quantity_native", "quantity_step_native"):
            _set_decimal(self, name, positive=True)
        if self.quantity_unit == "base" and self.multiplier != 1:
            raise ValueError("base quantity requires multiplier=1")
        if not _on_step(self.min_quantity_native, self.quantity_step_native):
            raise ValueError("min_quantity_native must align to quantity_step_native")
        if self.max_quantity_native is not None:
            _set_decimal(self, "max_quantity_native", positive=True)
            if self.max_quantity_native < self.min_quantity_native:
                raise ValueError("max_quantity_native must be at least min_quantity_native")
            if not _on_step(self.max_quantity_native, self.quantity_step_native):
                raise ValueError("max_quantity_native must align to quantity_step_native")
        _tuple(self.tick_bands, "tick_bands", tuple)
        if not self.tick_bands:
            raise ValueError("tick_bands must not be empty")
        bands = []
        for band in self.tick_bands:
            if len(band) != 2:
                raise ValueError("tick band must contain lower bound and tick")
            lower = _decimal(band[0], "tick lower bound", nonnegative=True)
            tick = _decimal(band[1], "tick", positive=True)
            if (not bands and lower != 0) or (bands and lower <= bands[-1][0]):
                raise ValueError("tick lower bounds must begin at zero and strictly increase")
            bands.append((lower, tick))
        object.__setattr__(self, "tick_bands", tuple(bands))


@dataclass(frozen=True, slots=True, kw_only=True)
class WalletKey:
    venue: str
    account: str
    account_mode: str
    currency: str

    def __post_init__(self) -> None:
        _texts(self, "venue", "account", "account_mode", "currency")


@dataclass(frozen=True, slots=True, kw_only=True)
class EventKey:
    event_id: str
    source: str
    event_ns: int
    received_ns: int
    available_ns: int
    journal_sequence: int

    def __post_init__(self) -> None:
        _texts(self, "event_id", "source")
        _knowledge_times(self, "event_ns")
        _integer(self.journal_sequence, "journal_sequence")


@dataclass(frozen=True, slots=True, kw_only=True)
class Observation:
    value: Decimal
    source_ns: int
    received_ns: int
    available_ns: int
    source_id: str
    provenance: str

    def __post_init__(self) -> None:
        _set_decimal(self, "value")
        _text(self.source_id, "source_id")
        _choice(self.provenance, "provenance", ("fixture", "observed", "proxy"))
        _knowledge_times(self, "source_ns")


@dataclass(frozen=True, slots=True, kw_only=True)
class BookLevel:
    price: Decimal
    quantity_native: Decimal

    def __post_init__(self) -> None:
        _set_decimal(self, "price", nonnegative=True)
        _set_decimal(self, "quantity_native", positive=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class Book:
    spec: InstrumentSpec
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    source_ns: int
    received_ns: int
    available_ns: int
    raw_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.spec, InstrumentSpec):
            raise TypeError("spec must be InstrumentSpec")
        _tuple(self.bids, "bids", BookLevel)
        _tuple(self.asks, "asks", BookLevel)
        _text(self.raw_hash, "raw_hash")
        _knowledge_times(self, "source_ns")


@dataclass(frozen=True, slots=True, kw_only=True)
class LegIntent:
    spec: InstrumentSpec
    wallet: WalletKey
    quantity_native: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.spec, InstrumentSpec) or not isinstance(self.wallet, WalletKey):
            raise TypeError("leg requires InstrumentSpec and WalletKey")
        _set_decimal(self, "quantity_native", positive=True)
        _require_quantity(self.spec, self.quantity_native)


@dataclass(frozen=True, slots=True, kw_only=True)
class PayoffLeg:
    spec: InstrumentSpec
    signed_quantity_native: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.spec, InstrumentSpec):
            raise TypeError("spec must be InstrumentSpec")
        _set_decimal(self, "signed_quantity_native")
        _require_quantity(self.spec, self.signed_quantity_native.copy_abs())


@dataclass(frozen=True, slots=True, kw_only=True)
class CombinationIntent:
    intent_id: str
    legs: tuple[LegIntent, ...]
    template: str
    sleeve: str
    decision_cutoff_ns: int
    valid_until_ns: int
    policy_hash: str

    def __post_init__(self) -> None:
        _texts(self, "intent_id", "policy_hash")
        _tuple(self.legs, "legs", LegIntent)
        if not self.legs:
            raise ValueError("legs must not be empty")
        _choice(self.template, "template", ("long_call", "long_put", "long_straddle", "long_strangle"))
        _choice(self.sleeve, "sleeve", ("directional", "volatility", "tail"))
        _integer(self.decision_cutoff_ns, "decision_cutoff_ns")
        _integer(self.valid_until_ns, "valid_until_ns")
        if self.valid_until_ns <= self.decision_cutoff_ns:
            raise ValueError("valid_until_ns must follow decision_cutoff_ns")


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusResult:
    status: str
    reason: str

    def __post_init__(self) -> None:
        _choice(self.status, "status", ("VALID", "REJECTED", "NOT_EVALUABLE", "UNSUPPORTED", "INVALID"))
        _text(self.reason, "reason")


def validate_quantity(spec: InstrumentSpec, quantity_native: Decimal | str) -> StatusResult:
    """Validate native order quantity without rounding or granting permission."""
    if not isinstance(spec, InstrumentSpec):
        raise TypeError("spec must be InstrumentSpec")
    try:
        quantity = _decimal(quantity_native, "quantity_native", positive=True)
    except (ValueError, TypeError) as exc:
        return StatusResult(status="INVALID", reason=str(exc))
    if quantity < spec.min_quantity_native:
        return StatusResult(status="INVALID", reason="quantity_native below minimum")
    if spec.max_quantity_native is not None and quantity > spec.max_quantity_native:
        return StatusResult(status="INVALID", reason="quantity_native above maximum")
    if not _on_step(quantity, spec.quantity_step_native):
        return StatusResult(status="INVALID", reason="quantity_native is off step")
    return StatusResult(status="VALID", reason="native quantity checked")


def _require_quantity(spec: InstrumentSpec, quantity_native: Decimal | str) -> Decimal:
    result = validate_quantity(spec, quantity_native)
    if result.status != "VALID":
        raise ValueError(result.reason)
    return _decimal(quantity_native, "quantity_native", positive=True)


def base_quantity(spec: InstrumentSpec, quantity_native: Decimal | str) -> Decimal:
    quantity = _require_quantity(spec, quantity_native)
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        return _decimal(quantity * spec.multiplier, "base_quantity", positive=True)


def premium_cash(spec: InstrumentSpec, price: Decimal | str, quantity_native: Decimal | str) -> Decimal:
    quantity = _require_quantity(spec, quantity_native)
    premium = _decimal(price, "price", nonnegative=True)
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        units = base_quantity(spec, quantity) if spec.price_unit == "quote_per_base" else quantity
        return _decimal(premium * units, "premium_cash", nonnegative=True)


def canonical_payload(value: Any) -> dict | list | str | int | bool | None:
    """Expand immutable records with exact, context-independent decimal strings."""
    if isinstance(value, Decimal):
        value = _decimal(value, "canonical decimal")
        if value == 0:
            return "0"
        rendered = format(value, "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    if value is None or type(value) in (str, int, bool):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: canonical_payload(getattr(value, field.name)) for field in fields(value)}
    if type(value) is tuple:
        return [canonical_payload(item) for item in value]
    if isinstance(value, Mapping):
        if not all(type(key) is str for key in value):
            raise TypeError("canonical mapping keys must be strings")
        return {key: canonical_payload(item) for key, item in value.items()}
    raise TypeError(f"unsupported canonical type: {type(value).__name__}")


def fingerprint(value: Any) -> str:
    payload = dumps(canonical_payload(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode("utf-8")).hexdigest()
