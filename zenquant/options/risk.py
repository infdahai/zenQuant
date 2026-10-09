"""Funded-long model admission with native cash, remaining basis and paid budgets.

VALID describes model feasibility only. Opaque cost hashes identify inputs but
cannot recover or authenticate pricing inputs, fee tiers or account eligibility.
No result grants execution permission, futures orders, or real-account access.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from .contracts import (
    CombinationIntent,
    Observation,
    PayoffLeg,
    StatusResult,
    WalletKey,
    _integer,
    _set_decimal,
    _text,
    _tuple,
    base_quantity,
    fingerprint,
    premium_cash,
)
from .costs import CostEvidence, TimingPolicy
from .ledger import LedgerState, active_risk_native
from .payoffs import expiry_payoff

ZERO = Decimal("0")
_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskPolicy:
    per_intent_report: Decimal
    active_and_pending_report: Decimal
    rolling_tail_paid_report: Decimal
    rolling_window_ns: int
    report_currency: str
    fx_timing: TimingPolicy | None = None

    def __post_init__(self) -> None:
        for name in ("per_intent_report", "active_and_pending_report", "rolling_tail_paid_report"):
            _set_decimal(self, name, nonnegative=True)
        _integer(self.rolling_window_ns, "rolling_window_ns", positive=True)
        _text(self.report_currency, "report_currency")
        if self.fx_timing is not None and not isinstance(self.fx_timing, TimingPolicy):
            raise TypeError("fx_timing must be TimingPolicy or None")


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskApproval:
    status: StatusResult
    approved_intent: CombinationIntent | None
    cash_reserve: tuple[tuple[WalletKey, Decimal], ...]
    reasons: tuple[str, ...]
    policy_hash: str
    execution_authority: str = "NONE"

    def __post_init__(self) -> None:
        if not isinstance(self.status, StatusResult):
            raise TypeError("status must be StatusResult")
        if self.approved_intent is not None and not isinstance(self.approved_intent, CombinationIntent):
            raise TypeError("approved_intent must be CombinationIntent or None")
        _tuple(self.cash_reserve, "cash_reserve", tuple)
        seen = set()
        for item in self.cash_reserve:
            if len(item) != 2:
                raise ValueError("wallet reserve pair required")
            w, amount = item
            if not isinstance(w, WalletKey) or w in seen:
                raise ValueError("reserve wallet must be unique WalletKey")
            if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
                raise ValueError("reserve must be finite nonnegative Decimal")
            seen.add(w)
        _tuple(self.reasons, "reasons", str)
        for reason in self.reasons:
            _text(reason, "reason")
        _text(self.policy_hash, "policy_hash")
        if self.execution_authority != "NONE":
            raise ValueError("execution_authority must be NONE")
        if self.status.status != "VALID" and (self.approved_intent is not None or self.cash_reserve):
            raise ValueError("unsuccessful approval cannot authorize intent or reserve")
        if self.status.status == "VALID" and self.approved_intent is None:
            raise ValueError("model approval requires intent")


def _template_valid(intent: CombinationIntent) -> bool:
    legs = intent.legs
    if intent.template in ("long_call", "long_put"):
        return len(legs) == 1 and legs[0].spec.option_type == ("C" if intent.template == "long_call" else "P")
    if len(legs) != 2 or {leg.spec.option_type for leg in legs} != {"C", "P"}:
        return False
    put = next(leg for leg in legs if leg.spec.option_type == "P")
    call = next(leg for leg in legs if leg.spec.option_type == "C")
    if (
        put.spec.underlying != call.spec.underlying
        or put.spec.expiry_ns != call.spec.expiry_ns
        or put.spec.settlement_currency != call.spec.settlement_currency
        or base_quantity(put.spec, put.quantity_native) != base_quantity(call.spec, call.quantity_native)
    ):
        return False
    return (
        put.spec.strike == call.spec.strike
        if intent.template == "long_straddle"
        else put.spec.strike < call.spec.strike
    )


def _supported(spec, wallet: WalletKey) -> bool:
    return (
        spec.product == "linear_option"
        and spec.exercise_style == "european"
        and spec.settlement_rule in ("linear_cash", "linear_via_future")
        and spec.quote_currency
        == spec.strike_currency
        == spec.settlement_currency
        == spec.margin_currency
        == wallet.currency
    )


def _native_receipts(state: LedgerState):
    """One economic receipt per native identity; ledger already freezes conflicts."""
    seen, receipts = set(), []
    for receipt in state.receipts:
        debit_wallet = receipt.fee_wallet if receipt.kind == "FEE" else receipt.wallet
        identity = (receipt.key.source, debit_wallet, receipt.key.event_id)
        if identity not in seen:
            receipts.append(receipt)
            seen.add(identity)
    return tuple(receipts)


def _sleeve(receipts, intent_id):
    tags = {
        r.sleeve
        for r in receipts
        if r.intent_id == intent_id and r.kind in ("RESERVE", "FILL", "FEE") and r.sleeve is not None
    }
    if intent_id is None or len(tags) != 1:
        return None
    tag = next(iter(tags))
    return tag if tag in ("directional", "volatility", "tail") else None


def _fee_intent(receipt, receipts):
    if receipt.intent_id is not None:
        return receipt.intent_id
    matches = {
        r.intent_id
        for r in receipts
        if r.wallet == receipt.wallet
        and r.key.source == receipt.key.source
        and r.kind in ("FILL", "EXPIRY", "CASH_SETTLEMENT", "EXERCISE_TO_FUTURE", "FUTURE_DELIVERY")
        and (
            (receipt.parent_id is not None and receipt.parent_id in (r.key.event_id, r.parent_id))
            or (receipt.order_id is not None and receipt.order_id == r.order_id)
        )
        and r.intent_id is not None
    }
    return next(iter(matches)) if len(matches) == 1 else None


def approve_intent(
    *,
    intent: CombinationIntent,
    state: LedgerState,
    costs: tuple[CostEvidence, ...],
    wallet_fx: tuple[tuple[WalletKey, Observation], ...],
    policy: RiskPolicy,
    asof_ns: int,
) -> RiskApproval:
    """Check a fresh proposal against funded-long budgets at a causal cutoff.

    Cash is never pooled across wallets. Active cost basis survives fills and
    system exercise claims until delivery. Rolling tail outlays never net sale
    proceeds. Historical foreign-currency paid costs require payment-time FX,
    which this P1 interface does not carry, so that dimension fails closed.
    """
    if (
        not isinstance(intent, CombinationIntent)
        or not isinstance(state, LedgerState)
        or not isinstance(policy, RiskPolicy)
    ):
        raise TypeError("intent/state/policy type mismatch")
    _integer(asof_ns, "asof_ns")
    _tuple(costs, "costs", CostEvidence)
    _tuple(wallet_fx, "wallet_fx", tuple)
    policy_hash = fingerprint(policy)
    assumptions = []

    def result(status, reason, *, reserves=()):
        return RiskApproval(
            status=StatusResult(status=status, reason=reason),
            approved_intent=intent if status == "VALID" else None,
            cash_reserve=reserves if status == "VALID" else (),
            reasons=tuple(dict.fromkeys((reason, *assumptions))),
            policy_hash=policy_hash,
            execution_authority="NONE",
        )

    if state.frozen_reasons:
        return result("REJECTED", "STATE_FROZEN")
    if state.unapplied:
        return result("NOT_EVALUABLE", "UNAPPLIED_RECEIPTS")
    if state.last_available_ns > asof_ns or any(
        r.key.received_ns > asof_ns or r.key.available_ns > asof_ns for r in state.receipts
    ):
        return result("NOT_EVALUABLE", "STATE_NOT_YET_AVAILABLE")
    if any(b.cash < 0 for b in state.balances):
        return result("REJECTED", "NEGATIVE_ACTUAL_CASH")
    if intent.decision_cutoff_ns > asof_ns:
        return result("REJECTED", "INTENT_NOT_YET_AVAILABLE")
    if asof_ns >= intent.valid_until_ns:
        return result("REJECTED", "INTENT_EXPIRED")
    if intent.policy_hash != policy_hash:
        return result("REJECTED", "POLICY_HASH_MISMATCH")
    if any(r.intent_id == intent.intent_id for r in state.receipts) or any(
        x.intent_id == intent.intent_id for x in (*state.positions, *state.reservations, *state.claims)
    ):
        return result("REJECTED", "INTENT_ID_ALREADY_USED")

    fx = {}
    for item in wallet_fx:
        if len(item) != 2:
            raise ValueError("wallet FX pair required")
        w, obs = item
        if not isinstance(w, WalletKey) or not isinstance(obs, Observation) or w in fx:
            raise ValueError("wallet FX must contain unique wallet and Observation")
        if obs.received_ns > asof_ns or obs.available_ns > asof_ns or obs.source_ns > asof_ns:
            return result("NOT_EVALUABLE", "WALLET_FX_NOT_YET_AVAILABLE")
        if obs.value <= 0 or (w.currency == policy.report_currency and obs.value != 1):
            return result("INVALID", "WALLET_FX_INVALID")
        fx[w] = obs

    class ConversionUnavailable(Exception):
        pass

    def rate(w):
        if w.currency == policy.report_currency:
            return Decimal("1")
        if policy.fx_timing is None:
            raise ConversionUnavailable("WALLET_FX_TIMING_POLICY_MISSING")
        obs = fx.get(w)
        if obs is None:
            raise ConversionUnavailable("WALLET_FX_MISSING")
        if not policy.fx_timing.minimum_source_age_ns <= asof_ns - obs.source_ns <= policy.fx_timing.max_age_ns:
            raise ConversionUnavailable("WALLET_FX_STALE")
        if obs.provenance == "proxy":
            assumptions.append("PROXY_WALLET_FX")
        return obs.value

    with localcontext(_CONTEXT):
        if not _template_valid(intent):
            return result("REJECTED", "TEMPLATE_MISMATCH")
        if len(costs) != len(intent.legs):
            return result("REJECTED", "COST_COVERAGE_MISMATCH")
        balances = {b.wallet: b.cash for b in state.balances}
        proposed, pending = {}, {}
        try:
            for leg, cost in zip(intent.legs, costs, strict=True):
                s, w = leg.spec, leg.wallet
                if w.account_mode != "standard" or w.venue != s.venue or w.currency != s.quote_currency:
                    return result("UNSUPPORTED", "UNSUPPORTED_WALLET")
                if not _supported(s, w):
                    return result("UNSUPPORTED", "UNSUPPORTED_INSTRUMENT")
                if not s.valid_from_ns <= asof_ns < s.expiry_ns or (
                    s.valid_until_ns is not None and (asof_ns >= s.valid_until_ns or s.expiry_ns >= s.valid_until_ns)
                ):
                    return result("NOT_EVALUABLE", "INSTRUMENT_RULE_NOT_EFFECTIVE")
                matches = tuple(x for x in state.specs if x.venue == s.venue and x.instrument_id == s.instrument_id)
                if matches != (s,):
                    return result("NOT_EVALUABLE", "INSTRUMENT_IDENTITY_MISMATCH")
                if w not in balances:
                    return result("REJECTED", "UNKNOWN_WALLET")
                if (
                    cost.instrument_hash != fingerprint(s)
                    or cost.quantity_native != leg.quantity_native
                    or cost.side != "BUY"
                    or cost.quote_currency != w.currency
                    or cost.report_currency != policy.report_currency
                    or cost.asof_ns != asof_ns
                ):
                    return result("REJECTED", "COST_BINDING_MISMATCH")
                if cost.status.status != "VALID":
                    assumptions.append(cost.status.reason)
                    return result(cost.status.status, "COST_NOT_VALID")
                if cost.premium_native is None or cost.fee_native is None or cost.total_report_currency is None:
                    return result("NOT_EVALUABLE", "COST_AMOUNT_UNKNOWN")
                amount = cost.premium_native + cost.fee_native
                if cost.total_report_currency != amount * rate(w):
                    return result("REJECTED", "COST_REPORT_MISMATCH")
                proposed[w] = proposed.get(w, ZERO) + amount
                assumptions.extend(cost.assumptions)
            for reservation in state.reservations:
                if reservation.wallet.account_mode != "standard":
                    return result("NOT_EVALUABLE", "RESERVATION_ACCOUNT_UNSUPPORTED")
                if reservation.wallet not in balances:
                    return result("NOT_EVALUABLE", "RESERVATION_WALLET_UNKNOWN")
                pending[reservation.wallet] = pending.get(reservation.wallet, ZERO) + reservation.remaining_cash
            for w, amount in proposed.items():
                if balances[w] - pending.get(w, ZERO) < amount:
                    return result("REJECTED", "INSUFFICIENT_WALLET_CASH")
            for item in (*state.positions, *state.claims):
                matches = tuple(
                    s for s in state.specs if s.venue == item.wallet.venue and s.instrument_id == item.instrument_id
                )
                if (
                    len(matches) != 1
                    or not _supported(matches[0], item.wallet)
                    or item.wallet.account_mode != "standard"
                ):
                    return result("NOT_EVALUABLE", "ACTIVE_INSTRUMENT_UNSUPPORTED_OR_UNKNOWN")
                if item.wallet not in balances or any(w not in balances for w, _ in item.opening_fees_basis):
                    return result("NOT_EVALUABLE", "ACTIVE_WALLET_UNKNOWN")
            proposed_report = sum((amount * rate(w) for w, amount in proposed.items()), ZERO)
            pending_report = sum((amount * rate(w) for w, amount in pending.items()), ZERO)
            active_report = ZERO
            for w in balances:
                active = active_risk_native(state, w)
                if active != 0:
                    active_report += active * rate(w)
            if proposed_report > policy.per_intent_report:
                return result("REJECTED", "PER_INTENT_LIMIT")
            if pending_report + active_report + proposed_report > policy.active_and_pending_report:
                return result("REJECTED", "ACTIVE_AND_PENDING_LIMIT")
            receipts = _native_receipts(state)
            tail_pending = ZERO
            for reservation in state.reservations:
                matches = tuple(
                    r
                    for r in receipts
                    if r.kind == "RESERVE"
                    and r.wallet == reservation.wallet
                    and r.order_id == reservation.order_id
                    and r.intent_id == reservation.intent_id
                )
                tag = _sleeve(receipts, reservation.intent_id)
                if len(matches) != 1 or tag is None:
                    return result("NOT_EVALUABLE", "RESERVATION_ATTRIBUTION_UNKNOWN")
                if tag == "tail":
                    tail_pending += reservation.remaining_cash * rate(reservation.wallet)
            tail_paid = ZERO
            for receipt in receipts:
                if receipt.kind not in ("FILL", "FEE"):
                    continue
                identity = _fee_intent(receipt, receipts) if receipt.kind == "FEE" else receipt.intent_id
                tag = _sleeve(receipts, identity)
                if tag is None:
                    return result("NOT_EVALUABLE", "ACTUAL_ATTRIBUTION_UNKNOWN")
                if receipt.key.event_ns > asof_ns:
                    return result("NOT_EVALUABLE", "ACTUAL_ECONOMIC_TIME_IN_FUTURE")
                if tag != "tail" or receipt.key.event_ns < asof_ns - policy.rolling_window_ns:
                    continue
                payments = []
                if receipt.kind == "FILL" and receipt.quantity_native > 0:
                    specs = tuple(
                        s
                        for s in state.specs
                        if s.venue == receipt.wallet.venue and s.instrument_id == receipt.instrument_id
                    )
                    if len(specs) != 1 or receipt.price is None:
                        return result("NOT_EVALUABLE", "ACTUAL_PREMIUM_UNKNOWN")
                    payments.append((receipt.wallet, premium_cash(specs[0], receipt.price, receipt.quantity_native)))
                if receipt.fee > 0:
                    if receipt.fee_wallet is None:
                        return result("NOT_EVALUABLE", "ACTUAL_FEE_WALLET_UNKNOWN")
                    payments.append((receipt.fee_wallet, receipt.fee))
                for w, amount in payments:
                    if w.currency != policy.report_currency and amount != 0:
                        return result("NOT_EVALUABLE", "HISTORICAL_PAID_FX_MISSING")
                    tail_paid += amount
            tail_proposed = proposed_report if intent.sleeve == "tail" else ZERO
            if tail_paid + tail_pending + tail_proposed > policy.rolling_tail_paid_report:
                return result("REJECTED", "ROLLING_TAIL_PAID_LIMIT")
        except ConversionUnavailable as exc:
            return result("NOT_EVALUABLE", str(exc))
        assumptions.extend(
            (
                "ACCOUNT_ELIGIBILITY_NOT_EVALUABLE",
                "INTERIM_VALUATION_NOT_EVALUABLE",
                "REAL_MAXIMUM_RISK_NOT_EVALUABLE",
                "OPAQUE_COST_HASH_NOT_AUTHENTICATION",
            )
        )
        return result("VALID", "MODELED_FUNDED_LONG_FEASIBLE", reserves=tuple(proposed.items()))


def long_expiry_scenarios(
    *,
    intent: CombinationIntent,
    deliveries: tuple[tuple[tuple[str, Decimal], ...], ...],
    paid_by_currency: tuple[tuple[str, Decimal], ...],
) -> tuple[tuple[tuple[str, Decimal], ...], ...]:
    """One terminal payoff-minus-paid row per manual delivery, by native currency.

    For supported longs, the terminal premium/known-fee loss bound per currency
    is -paid_by_currency. Future fees, interim value and reporting-currency FX
    risk remain NOT_EVALUABLE; this diagnostic cannot promise real max risk.
    Neither fixing scenarios nor favorable terminal math imply reachable fills.
    """
    if not isinstance(intent, CombinationIntent):
        raise TypeError("intent must be CombinationIntent")
    _tuple(deliveries, "deliveries", tuple)
    _tuple(paid_by_currency, "paid_by_currency", tuple)
    paid = {}
    for item in paid_by_currency:
        if len(item) != 2:
            raise ValueError("paid currency pair required")
        currency, amount = item
        _text(currency, "currency")
        if currency in paid or not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
            raise ValueError("unique currency and finite nonnegative paid Decimal required")
        paid[currency] = amount
    with localcontext(_CONTEXT):
        if not _template_valid(intent):
            raise ValueError("TEMPLATE_MISMATCH")
        legs = tuple(PayoffLeg(spec=leg.spec, signed_quantity_native=leg.quantity_native) for leg in intent.legs)
        rows = []
        for delivery in deliveries:
            flows = dict(expiry_payoff(legs=legs, delivery_prices=delivery))
            for currency, amount in paid.items():
                flows[currency] = flows.get(currency, ZERO) - amount
            rows.append(tuple(sorted(flows.items())))
        return tuple(rows)
