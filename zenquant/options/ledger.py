"""Immutable actual cash and funded-long inventory; no execution permission.

Business-invalid receipts survive in ``unapplied`` with frozen evidence. Some
receipts (notably an unallocated FEE) can have known cash applied while their
basis attribution remains unapplied; never blindly retry this collection.
Lifecycle and duplicate handling are owned by the next ledger stage.
"""

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from types import MappingProxyType

from .contracts import (
    EventKey,
    InstrumentSpec,
    WalletKey,
    _integer,
    _set_decimal,
    _text,
    _tuple,
    premium_cash,
    validate_quantity,
)

ZERO = Decimal("0")
ONE = Decimal("1")
_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)
_PAYLOAD = frozenset(
    (
        "instrument_id",
        "intent_id",
        "order_id",
        "sleeve",
        "quantity_native",
        "price",
        "amount",
        "fee_wallet",
        "parent_id",
    )
)


def _schema(required: tuple[str, ...], allowed: tuple[str, ...]):
    return (frozenset(required), _PAYLOAD - frozenset(allowed))


# Required / forbidden nullable fields. ``fee`` is always present and separately
# checked. Lifecycle quantity/amount meanings are completed by Task 5.
EVENT_SCHEMAS = MappingProxyType(
    {
        "RESERVE": _schema(
            ("intent_id", "order_id", "quantity_native", "amount"),
            ("instrument_id", "intent_id", "order_id", "sleeve", "quantity_native", "amount"),
        ),
        "CANCEL_CONFIRMED": _schema(("intent_id", "order_id"), ("instrument_id", "intent_id", "order_id")),
        "FILL": _schema(
            ("instrument_id", "intent_id", "order_id", "quantity_native", "price"),
            ("instrument_id", "intent_id", "order_id", "sleeve", "quantity_native", "price", "fee_wallet"),
        ),
        "FEE": _schema(
            ("fee_wallet",), ("instrument_id", "intent_id", "order_id", "sleeve", "fee_wallet", "parent_id")
        ),
        "MARK": _schema(("instrument_id", "price"), ("instrument_id", "price")),
        "EXPIRY": _schema(
            ("instrument_id", "intent_id", "parent_id", "quantity_native"),
            ("instrument_id", "intent_id", "parent_id", "quantity_native", "amount", "fee_wallet"),
        ),
        "EXERCISE_TO_FUTURE": _schema(
            ("instrument_id", "intent_id", "parent_id", "quantity_native", "price", "amount"),
            ("instrument_id", "intent_id", "parent_id", "quantity_native", "price", "amount", "fee_wallet"),
        ),
        "FUTURE_DELIVERY": _schema(
            ("instrument_id", "intent_id", "parent_id", "amount"),
            ("instrument_id", "intent_id", "parent_id", "quantity_native", "price", "amount", "fee_wallet"),
        ),
        "CASH_SETTLEMENT": _schema(
            ("instrument_id", "intent_id", "parent_id", "quantity_native", "amount"),
            ("instrument_id", "intent_id", "parent_id", "quantity_native", "amount", "fee_wallet"),
        ),
        "CORRECTION": (frozenset(), frozenset()),
    }
)


def _wallet(value: WalletKey) -> None:
    if not isinstance(value, WalletKey):
        raise TypeError("wallet must be WalletKey")


def _fee_basis(value: tuple[tuple[WalletKey, Decimal], ...]) -> None:
    _tuple(value, "opening_fees_basis", tuple)
    seen = set()
    for item in value:
        if len(item) != 2:
            raise ValueError("fee basis must contain wallet and Decimal")
        w, amount = item
        _wallet(w)
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
            raise ValueError("fee basis must be finite nonnegative Decimal")
        if w in seen:
            raise ValueError("duplicate fee basis wallet")
        seen.add(w)


@dataclass(frozen=True, slots=True, kw_only=True)
class WalletBalance:
    wallet: WalletKey
    cash: Decimal

    def __post_init__(self) -> None:
        _wallet(self.wallet)
        _set_decimal(self, "cash")


@dataclass(frozen=True, slots=True, kw_only=True)
class OptionPosition:
    wallet: WalletKey
    instrument_id: str
    intent_id: str
    quantity_native: Decimal
    premium_basis: Decimal
    opening_fees_basis: tuple[tuple[WalletKey, Decimal], ...]

    def __post_init__(self) -> None:
        _wallet(self.wallet)
        _text(self.instrument_id, "instrument_id")
        _text(self.intent_id, "intent_id")
        _set_decimal(self, "quantity_native", positive=True)
        _set_decimal(self, "premium_basis", nonnegative=True)
        _fee_basis(self.opening_fees_basis)


@dataclass(frozen=True, slots=True, kw_only=True)
class Reservation:
    wallet: WalletKey
    intent_id: str
    order_id: str
    remaining_cash: Decimal
    remaining_quantity_native: Decimal

    def __post_init__(self) -> None:
        _wallet(self.wallet)
        _text(self.intent_id, "intent_id")
        _text(self.order_id, "order_id")
        _set_decimal(self, "remaining_cash", nonnegative=True)
        _set_decimal(self, "remaining_quantity_native", nonnegative=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class SystemClaim:
    wallet: WalletKey
    instrument_id: str
    link_id: str
    future_quantity_base: Decimal
    strike: Decimal
    settlement_amount: Decimal
    premium_basis: Decimal
    opening_fees_basis: tuple[tuple[WalletKey, Decimal], ...]

    def __post_init__(self) -> None:
        _wallet(self.wallet)
        _text(self.instrument_id, "instrument_id")
        _text(self.link_id, "link_id")
        _set_decimal(self, "future_quantity_base")
        _set_decimal(self, "strike", positive=True)
        _set_decimal(self, "settlement_amount", nonnegative=True)
        _set_decimal(self, "premium_basis", nonnegative=True)
        _fee_basis(self.opening_fees_basis)


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerEvent:
    key: EventKey
    kind: str
    wallet: WalletKey
    instrument_id: str | None
    intent_id: str | None
    order_id: str | None
    sleeve: str | None
    quantity_native: Decimal | None
    price: Decimal | None
    amount: Decimal | None
    fee: Decimal
    fee_wallet: WalletKey | None
    parent_id: str | None
    raw_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, EventKey):
            raise TypeError("key must be EventKey")
        _wallet(self.wallet)
        _text(self.kind, "kind")
        _text(self.raw_hash, "raw_hash")
        for name in ("instrument_id", "intent_id", "order_id", "sleeve", "parent_id"):
            if getattr(self, name) is not None:
                _text(getattr(self, name), name)
        for name in ("quantity_native", "price", "amount"):
            if getattr(self, name) is not None:
                _set_decimal(self, name)
        _set_decimal(self, "fee")
        if self.fee_wallet is not None:
            _wallet(self.fee_wallet)


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerState:
    specs: tuple[InstrumentSpec, ...]
    initial_balances: tuple[WalletBalance, ...]
    balances: tuple[WalletBalance, ...]
    positions: tuple[OptionPosition, ...]
    reservations: tuple[Reservation, ...]
    claims: tuple[SystemClaim, ...]
    receipts: tuple[LedgerEvent, ...]
    unapplied: tuple[LedgerEvent, ...]
    last_sequence: int
    last_available_ns: int
    frozen_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, kind in (
            ("specs", InstrumentSpec),
            ("initial_balances", WalletBalance),
            ("balances", WalletBalance),
            ("positions", OptionPosition),
            ("reservations", Reservation),
            ("claims", SystemClaim),
            ("receipts", LedgerEvent),
            ("unapplied", LedgerEvent),
            ("frozen_reasons", str),
        ):
            _tuple(getattr(self, name), name, kind)
        for name in ("initial_balances", "balances"):
            values = getattr(self, name)
            if len({b.wallet for b in values}) != len(values):
                raise ValueError("duplicate wallet balance")
        _integer(self.last_sequence, "last_sequence")
        _integer(self.last_available_ns, "last_available_ns")
        for reason in self.frozen_reasons:
            _text(reason, "frozen reason")


def initial_state(*, specs: tuple[InstrumentSpec, ...], balances: tuple[WalletBalance, ...]) -> LedgerState:
    _tuple(specs, "specs", InstrumentSpec)
    _tuple(balances, "balances", WalletBalance)
    return LedgerState(
        specs=specs,
        initial_balances=balances,
        balances=balances,
        positions=(),
        reservations=(),
        claims=(),
        receipts=(),
        unapplied=(),
        last_sequence=0,
        last_available_ns=0,
        frozen_reasons=("NEGATIVE_INITIAL_CASH",) if any(b.cash < 0 for b in balances) else (),
    )


def wallet_cash(state: LedgerState, wallet: WalletKey) -> Decimal:
    _wallet(wallet)
    for balance in state.balances:
        if balance.wallet == wallet:
            return balance.cash
    raise ValueError("UNKNOWN_WALLET")


def active_risk_native(state: LedgerState, wallet: WalletKey) -> Decimal:
    """Sum only this wallet's native basis; no cross-currency conversion."""
    wallet_cash(state, wallet)
    with localcontext(_CONTEXT):
        total = ZERO
        for item in (*state.positions, *state.claims):
            if item.wallet == wallet:
                total += item.premium_basis
            total += sum((amount for w, amount in item.opening_fees_basis if w == wallet), ZERO)
        return total


def _freeze(state: LedgerState, *reasons: str) -> LedgerState:
    return replace(state, frozen_reasons=tuple(dict.fromkeys((*state.frozen_reasons, *reasons))))


def _unapplied(state: LedgerState, event: LedgerEvent, reason: str) -> LedgerState:
    return _freeze(replace(state, unapplied=(*state.unapplied, event)), reason)


def _cash_change(state: LedgerState, wallet: WalletKey, amount: Decimal) -> LedgerState:
    wallet_cash(state, wallet)
    balances = tuple(replace(b, cash=b.cash + amount) if b.wallet == wallet else b for b in state.balances)
    state = replace(state, balances=balances)
    return _freeze(state, "NEGATIVE_CASH") if any(b.cash < 0 for b in balances) else state


def _payload_error(event: LedgerEvent) -> str | None:
    if event.kind not in EVENT_SCHEMAS:
        return "UNKNOWN_EVENT_KIND"
    required, forbidden = EVENT_SCHEMAS[event.kind]
    if any(getattr(event, name) is None for name in required):
        return "MISSING_EVENT_PAYLOAD"
    if any(getattr(event, name) is not None for name in forbidden):
        return "FORBIDDEN_EVENT_PAYLOAD"
    if event.fee < 0 or (event.price is not None and event.price < 0):
        return "INVALID_EVENT_PAYLOAD"
    if event.kind == "RESERVE" and (event.amount <= 0 or event.quantity_native <= 0):
        return "INVALID_RESERVATION"
    if event.kind == "FILL" and event.quantity_native == 0:
        return "ZERO_FILL_QUANTITY"
    if event.kind == "FEE" and event.fee <= 0:
        return "INVALID_FEE"
    if event.kind in ("RESERVE", "CANCEL_CONFIRMED", "MARK") and event.fee != 0:
        return "FORBIDDEN_EVENT_FEE"
    if event.fee > 0 and event.fee_wallet is None:
        return "UNRESOLVED_FEE_WALLET"
    return None


def _spec(state: LedgerState, event: LedgerEvent) -> tuple[InstrumentSpec | None, str | None]:
    matches = tuple(s for s in state.specs if s.venue == event.wallet.venue and s.instrument_id == event.instrument_id)
    if not matches:
        return None, "UNKNOWN_INSTRUMENT"
    if len(matches) != 1:
        return None, "AMBIGUOUS_INSTRUMENT"
    spec = matches[0]
    if (
        event.wallet.account_mode != "standard"
        or spec.product != "linear_option"
        or spec.exercise_style != "european"
        or not (
            spec.quote_currency
            == spec.strike_currency
            == spec.settlement_currency
            == spec.margin_currency
            == event.wallet.currency
        )
    ):
        return None, "UNSUPPORTED_INSTRUMENT"
    return spec, None


def _add_fee(basis, wallet: WalletKey | None, amount: Decimal):
    if amount == 0:
        return basis
    amounts = dict(basis)
    amounts[wallet] = amounts.get(wallet, ZERO) + amount
    return tuple(amounts.items())


def _release_reservation(state: LedgerState, event: LedgerEvent, spent: Decimal) -> LedgerState:
    matches = tuple(r for r in state.reservations if r.wallet == event.wallet and r.order_id == event.order_id)
    if len(matches) != 1 or matches[0].intent_id != event.intent_id:
        return _freeze(state, "UNKNOWN_ORDER" if not matches else "ORDER_ATTRIBUTION_MISMATCH")
    reservation = matches[0]
    quantity = event.quantity_native.copy_abs()
    if spent > reservation.remaining_cash or quantity > reservation.remaining_quantity_native:
        state = _freeze(state, "RESERVATION_OVERRUN")
    remaining_quantity = max(ZERO, reservation.remaining_quantity_native - quantity)
    updated = replace(
        reservation,
        remaining_cash=max(ZERO, reservation.remaining_cash - spent),
        remaining_quantity_native=remaining_quantity,
    )
    return replace(
        state,
        reservations=tuple(
            updated if r == reservation else r for r in state.reservations if r != reservation or remaining_quantity > 0
        ),
    )


def _fill(state: LedgerState, event: LedgerEvent) -> LedgerState:
    spec, error = _spec(state, event)
    if error:
        return _unapplied(state, event, error)
    position = next(
        (
            p
            for p in state.positions
            if (p.wallet == event.wallet and p.instrument_id == event.instrument_id and p.intent_id == event.intent_id)
        ),
        None,
    )
    quantity = event.quantity_native
    if quantity < 0 and (position is None or quantity.copy_abs() > position.quantity_native):
        return _unapplied(state, event, "EXIT_EXCEEDS_LONG")
    status = validate_quantity(spec, quantity.copy_abs())
    if status.status == "VALID":
        premium = premium_cash(spec, event.price, quantity.copy_abs())
    else:
        units = quantity.copy_abs() * (spec.multiplier if spec.price_unit == "quote_per_base" else ONE)
        premium = event.price * units
        state = _freeze(state, "INVALID_FILL_QUANTITY")
    if not spec.valid_from_ns <= event.key.event_ns or (
        spec.valid_until_ns is not None and event.key.event_ns >= spec.valid_until_ns
    ):
        state = _freeze(state, "INSTRUMENT_RULE_OUTSIDE_VALIDITY")
    if event.key.event_ns >= spec.expiry_ns:
        state = _freeze(state, "FILL_AT_OR_AFTER_EXPIRY")
    if event.sleeve not in ("directional", "volatility", "tail"):
        state = _freeze(state, "UNRESOLVED_SLEEVE_ATTRIBUTION")
    elif any(
        r.wallet == event.wallet
        and r.intent_id == event.intent_id
        and r.kind in ("FILL", "RESERVE")
        and r.sleeve in ("directional", "volatility", "tail")
        and r.sleeve != event.sleeve
        for r in state.receipts[:-1]
    ):
        state = _freeze(state, "SLEEVE_ATTRIBUTION_CONFLICT")
    state = _cash_change(state, event.wallet, -premium if quantity > 0 else premium)
    if event.fee > 0:
        state = _cash_change(state, event.fee_wallet, -event.fee)
    if quantity > 0:
        updated = OptionPosition(
            wallet=event.wallet,
            instrument_id=event.instrument_id,
            intent_id=event.intent_id,
            quantity_native=quantity + (position.quantity_native if position else ZERO),
            premium_basis=premium + (position.premium_basis if position else ZERO),
            opening_fees_basis=_add_fee(position.opening_fees_basis if position else (), event.fee_wallet, event.fee),
        )
    elif quantity.copy_abs() == position.quantity_native:
        updated = None
    else:
        remaining = ONE - quantity.copy_abs() / position.quantity_native
        updated = replace(
            position,
            quantity_native=position.quantity_native + quantity,
            premium_basis=position.premium_basis * remaining,
            opening_fees_basis=tuple((w, amount * remaining) for w, amount in position.opening_fees_basis),
        )
    positions = tuple(p for p in state.positions if p != position)
    if updated is not None:
        # Keep original insertion order for existing intent inventory.
        positions = (
            tuple(updated if p == position else p for p in state.positions) if position else (*positions, updated)
        )
    state = replace(state, positions=positions)
    spent = premium + (event.fee if event.fee_wallet == event.wallet else ZERO) if quantity > 0 else ZERO
    return _release_reservation(state, event, spent)


def _opening_fee_attribution(state: LedgerState, event: LedgerEvent) -> tuple[OptionPosition, str] | None:
    """Require retained positive fill provenance, not merely inventory identity.

    A late fee after a reduction needs lot allocation beyond P1's aggregate
    basis. Its actual debit is retained but its basis remains unresolved.
    """
    candidates = tuple(
        (index, r)
        for index, r in enumerate(state.receipts[:-1])
        if (
            r.kind == "FILL"
            and r.wallet == event.wallet
            and (
                (event.parent_id is not None and r.key.event_id == event.parent_id and r.key.source == event.key.source)
                or (event.parent_id is None and event.order_id is not None and r.order_id == event.order_id)
            )
        )
    )
    if not candidates or (event.parent_id is not None and len(candidates) != 1):
        return None
    if any(
        r in state.unapplied
        or r.quantity_native is None
        or r.quantity_native <= 0
        or r.instrument_id != event.instrument_id
        or r.intent_id != event.intent_id
        for _, r in candidates
    ):
        return None
    orders = {r.order_id for _, r in candidates}
    if len(orders) != 1 or (event.order_id is not None and event.order_id not in orders):
        return None
    first_index = candidates[0][0]
    if any(
        r.kind == "FILL"
        and r.quantity_native is not None
        and r.quantity_native < 0
        and r not in state.unapplied
        and r.wallet == event.wallet
        and r.instrument_id == event.instrument_id
        and r.intent_id == event.intent_id
        for r in state.receipts[first_index + 1 : -1]
    ):
        return None
    matches = tuple(
        p
        for p in state.positions
        if (p.wallet == event.wallet and p.instrument_id == event.instrument_id and p.intent_id == event.intent_id)
    )
    return (matches[0], candidates[0][1].order_id) if len(matches) == 1 else None


def _fee(state: LedgerState, event: LedgerEvent) -> LedgerState:
    # Debit the actual fee wallet even if the separate inventory-attribution
    # wallet is unknown. An unresolved receipt may therefore be partly applied.
    if event.fee_wallet not in {balance.wallet for balance in state.balances}:
        return _unapplied(state, event, "UNKNOWN_WALLET")
    state = _cash_change(state, event.fee_wallet, -event.fee)
    attribution = _opening_fee_attribution(state, event)
    if attribution is None:
        return _unapplied(state, event, "UNRESOLVED_FEE_BASIS_ATTRIBUTION")
    position, order_id = attribution
    updated = replace(position, opening_fees_basis=_add_fee(position.opening_fees_basis, event.fee_wallet, event.fee))
    state = replace(state, positions=tuple(updated if p == position else p for p in state.positions))
    if event.fee_wallet != event.wallet:
        return state
    matching = tuple(r for r in state.reservations if r.wallet == event.wallet and r.order_id == order_id)
    # A completed or cancelled order has no remaining reservation to release.
    if not matching:
        return state
    if len(matching) != 1 or matching[0].intent_id != event.intent_id:
        return _freeze(state, "ORDER_ATTRIBUTION_MISMATCH")
    reservation = matching[0]
    if event.fee > reservation.remaining_cash:
        state = _freeze(state, "RESERVATION_OVERRUN")
    remaining = replace(reservation, remaining_cash=max(ZERO, reservation.remaining_cash - event.fee))
    return replace(state, reservations=tuple(remaining if r == reservation else r for r in state.reservations))


def apply_event(state: LedgerState, event: LedgerEvent) -> LedgerState:
    """Pure actual-event reducer. Frozen state still records computable receipts."""
    if not isinstance(state, LedgerState) or not isinstance(event, LedgerEvent):
        raise TypeError("apply_event requires LedgerState and LedgerEvent")
    with localcontext(_CONTEXT):
        state = replace(
            state,
            receipts=(*state.receipts, event),
            last_sequence=max(state.last_sequence, event.key.journal_sequence),
            last_available_ns=max(state.last_available_ns, event.key.available_ns),
        )
        if event.kind == "CORRECTION":
            return _unapplied(state, event, "CORRECTION_REQUIRES_RECONCILIATION")
        error = _payload_error(event)
        if error:
            return _unapplied(state, event, error)
        if event.kind == "FEE":
            return _fee(state, event)
        known_wallets = {balance.wallet for balance in state.balances}
        if event.wallet not in known_wallets or (event.fee > 0 and event.fee_wallet not in known_wallets):
            return _unapplied(state, event, "UNKNOWN_WALLET")
        if event.kind == "FILL":
            return _fill(state, event)
        if event.kind == "RESERVE":
            if event.wallet.account_mode != "standard":
                return _unapplied(state, event, "UNSUPPORTED_ACCOUNT_MODE")
            if any(r.wallet == event.wallet and r.order_id == event.order_id for r in state.reservations):
                return _unapplied(state, event, "DUPLICATE_RESERVATION_ORDER")
            reserved = sum((r.remaining_cash for r in state.reservations if r.wallet == event.wallet), ZERO)
            if event.amount > wallet_cash(state, event.wallet) - reserved:
                return _unapplied(state, event, "INSUFFICIENT_SPENDABLE_CASH")
            return replace(
                state,
                reservations=(
                    *state.reservations,
                    Reservation(
                        wallet=event.wallet,
                        intent_id=event.intent_id,
                        order_id=event.order_id,
                        remaining_cash=event.amount,
                        remaining_quantity_native=event.quantity_native,
                    ),
                ),
            )
        if event.kind == "CANCEL_CONFIRMED":
            matching = tuple(r for r in state.reservations if r.wallet == event.wallet and r.order_id == event.order_id)
            if any(r.intent_id != event.intent_id for r in matching):
                return _unapplied(state, event, "ORDER_ATTRIBUTION_MISMATCH")
            return replace(state, reservations=tuple(r for r in state.reservations if r not in matching))
        if event.kind == "MARK":
            _, error = _spec(state, event)
            return _unapplied(state, event, error) if error else state
        return _unapplied(state, event, "LIFECYCLE_NOT_IMPLEMENTED")
