//! Displayed depth and fee diagnostics, never execution or fill evidence.
use crate::{canonical::canonical_hash, contracts::*, decimal::Dec, error::Result};
pub(crate) type Diagnostic<T> = std::result::Result<T, StatusResult>;
pub(crate) fn math<T>(v: Result<T>) -> Diagnostic<T> {
    v.map_err(|e| {
        StatusResult::new(
            Status::NotEvaluable,
            format!("NUMERICAL_LIMITATION:{:?}", e.code),
        )
    })
}
pub(crate) fn need(ok: bool, status: Status, reason: &str) -> Diagnostic<()> {
    if ok {
        Ok(())
    } else {
        Err(StatusResult::new(status, reason))
    }
}
pub(crate) fn legal_quantity(s: &InstrumentSpec, q: &Dec) -> Diagnostic<Dec> {
    need(
        s.multiplier > Dec::zero(),
        Status::Invalid,
        "INVALID_MULTIPLIER",
    )?;
    need(
        s.min_quantity_native > Dec::zero() && s.quantity_step_native > Dec::zero(),
        Status::Invalid,
        "INVALID_LOT_RULE",
    )?;
    need(
        q > &Dec::zero()
            && q >= &s.min_quantity_native
            && q.is_multiple_of(&s.quantity_step_native)
            && s.max_quantity_native.as_ref().is_none_or(|m| q <= m),
        Status::Rejected,
        "ILLEGAL_NATIVE_QUANTITY",
    )?;
    match s.quantity_unit {
        QuantityUnit::Base => {
            need(
                s.multiplier == Dec::one(),
                Status::Unsupported,
                "BASE_MULTIPLIER_UNSUPPORTED",
            )?;
            Ok(q.clone())
        }
        QuantityUnit::Contracts => math(q.checked_mul(&s.multiplier)),
    }
}
fn timestamp(
    source: u64,
    received: u64,
    available: u64,
    cutoff: u64,
    p: &TimingPolicy,
    label: &str,
    notes: &mut Vec<String>,
) -> Diagnostic<()> {
    need(
        received <= available,
        Status::Invalid,
        "CLOCK_ORDER_INVALID",
    )?;
    need(
        received <= cutoff && available <= cutoff,
        Status::NotEvaluable,
        &format!("{label}_NOT_YET_AVAILABLE"),
    )?;
    let age = i128::from(cutoff) - i128::from(source);
    need(
        i128::from(p.minimum_source_age_ns) <= i128::from(p.max_age_ns),
        Status::Invalid,
        "INVALID_SOURCE_AGE_POLICY",
    )?;
    need(
        age >= i128::from(p.minimum_source_age_ns) && age <= i128::from(p.max_age_ns),
        Status::NotEvaluable,
        &format!("{label}_SOURCE_AGE_OUTSIDE_POLICY"),
    )?;
    if age < 0 {
        notes.push(format!("SOURCE_CLOCK_LEAD:{label}:{}", -age));
    }
    Ok(())
}
fn knowledge(at: Option<u64>, cutoff: u64, label: &str) -> Diagnostic<()> {
    need(
        at.is_some_and(|n| n <= cutoff),
        Status::NotEvaluable,
        &format!("{label}_KNOWLEDGE_UNAVAILABLE"),
    )
}
fn tick(s: &InstrumentSpec, p: &Dec) -> Diagnostic<()> {
    need(!p.is_negative(), Status::Invalid, "NEGATIVE_BOOK_PRICE")?;
    need(
        !s.tick_bands.is_empty() && s.tick_bands[0].0.is_zero(),
        Status::Invalid,
        "INVALID_TICK_BANDS",
    )?;
    need(
        s.tick_bands
            .iter()
            .all(|(b, t)| !b.is_negative() && t > &Dec::zero())
            && s.tick_bands.windows(2).all(|w| w[0].0 < w[1].0),
        Status::Invalid,
        "INVALID_TICK_BANDS",
    )?;
    let (_, t) = s
        .tick_bands
        .iter()
        .rev()
        .find(|(b, _)| p >= b)
        .ok_or_else(|| StatusResult::new(Status::Invalid, "MISSING_TICK_BAND"))?;
    need(
        p.is_multiple_of(t),
        Status::Rejected,
        "ILLEGAL_NATIVE_PRICE_TICK",
    )
}
fn book_valid(b: &Book) -> Diagnostic<()> {
    need(
        !b.bids.is_empty() && !b.asks.is_empty(),
        Status::NotEvaluable,
        "TWO_SIDED_BOOK_REQUIRED",
    )?;
    need(
        b.bids.len() <= MAX_LEVELS && b.asks.len() <= MAX_LEVELS,
        Status::Invalid,
        "BOOK_LIMIT_EXCEEDED",
    )?;
    for levels in [&b.bids, &b.asks] {
        for l in levels {
            tick(&b.spec, &l.price)?;
            need(
                l.quantity_native > Dec::zero(),
                Status::Invalid,
                "INVALID_BOOK_DEPTH",
            )?;
        }
    }
    need(
        b.bids.windows(2).all(|w| w[0].price > w[1].price)
            && b.asks.windows(2).all(|w| w[0].price < w[1].price)
            && b.bids[0].price < b.asks[0].price,
        Status::Invalid,
        "UNORDERED_OR_CROSSED_BOOK",
    )
}
fn premium(
    q: &QuoteInput,
    l: &CandidateLeg,
    cutoff: u64,
    notes: &mut Vec<String>,
) -> Diagnostic<(Dec, Dec)> {
    let s = &q.book.spec;
    let desc = math(InstrumentDescriptor::from_spec(s))?;
    need(
        desc == l.instrument,
        Status::Invalid,
        "EXACT_SPEC_BINDING_REQUIRED",
    )?;
    knowledge(q.metadata_known_ns, cutoff, "METADATA")?;
    need(
        s.valid_from_ns <= cutoff && s.valid_until_ns.is_none_or(|n| cutoff < n),
        Status::NotEvaluable,
        "SPEC_OUTSIDE_VALID_DOMAIN",
    )?;
    timestamp(
        q.book.source_ns,
        q.book.received_ns,
        q.book.available_ns,
        cutoff,
        &q.timing,
        "BOOK",
        notes,
    )?;
    let base = legal_quantity(s, &l.quantity_native)?;
    need(
        s.quantity_unit == QuantityUnit::Contracts || s.price_unit == PriceUnit::QuotePerBase,
        Status::Unsupported,
        "PRICE_QUANTITY_UNIT_UNSUPPORTED",
    )?;
    book_valid(&q.book)?;
    let mut remaining = l.quantity_native.clone();
    let mut total = Dec::zero();
    let levels = if l.side == Side::Buy {
        &q.book.asks
    } else {
        &q.book.bids
    };
    for level in levels {
        if remaining.is_zero() {
            break;
        }
        let take = remaining.clone().min(level.quantity_native.clone());
        let priced = match s.price_unit {
            PriceUnit::QuotePerBase => {
                if s.quantity_unit == QuantityUnit::Contracts {
                    math(take.checked_mul(&s.multiplier))?
                } else {
                    take.clone()
                }
            }
            PriceUnit::QuotePerContract => take.clone(),
        };
        total = math(total.checked_add(&math(level.price.checked_mul(&priced))?))?;
        remaining = math(remaining.checked_sub(&take))?;
    }
    need(
        remaining.is_zero(),
        Status::NotEvaluable,
        "INSUFFICIENT_DISPLAYED_DEPTH",
    )?;
    Ok((total, base))
}
/// Exact premium plus separately capped fee; SELL returns net proceeds, BUY spend.
/// All ages use signed i128; tolerated clock lead is labeled, knowledge never leads.
pub fn quote_cost(q: &QuoteInput, l: &CandidateLeg, cutoff: u64, report: &str) -> CostEvidence {
    let mut out = CostEvidence {
        status: StatusResult::new(Status::NotEvaluable, "COST_NOT_EVALUATED"),
        instrument_hash: l.instrument.spec_hash.clone(),
        book_hash: q.book.raw_hash.clone(),
        side: l.side,
        quote_currency: q.book.spec.quote_currency.clone(),
        report_currency: report.into(),
        asof_ns: cutoff,
        quantity_native: l.quantity_native.clone(),
        premium_native: None,
        fee_native: None,
        total_report_currency: None,
        assumptions: q.assumptions.clone(),
        input_hash: String::new(),
    };
    let result = (|| -> Diagnostic<()> {
        out.input_hash = math(canonical_hash(&(q, l, cutoff, report)))?;
        let (premium, base) = premium(q, l, cutoff, &mut out.assumptions)?;
        out.premium_native = Some(premium.clone());
        knowledge(q.fee_known_ns, cutoff, "FEE")?;
        let f = q
            .fee_policy
            .as_ref()
            .ok_or_else(|| StatusResult::new(Status::NotEvaluable, "MISSING_FEE_POLICY"))?;
        need(
            !f.rate.is_negative() && !f.premium_cap.is_negative(),
            Status::Invalid,
            "NEGATIVE_FEE_POLICY",
        )?;
        need(
            f.fee_currency == q.book.spec.quote_currency,
            Status::Unsupported,
            "FEE_CURRENCY_UNSUPPORTED",
        )?;
        let index = q
            .index_in_quote
            .as_ref()
            .ok_or_else(|| StatusResult::new(Status::NotEvaluable, "MISSING_INDEX"))?;
        need(
            q.index_currency.as_deref() == Some(q.book.spec.quote_currency.as_str()),
            Status::NotEvaluable,
            "INDEX_CURRENCY_MISMATCH",
        )?;
        need(index.value > Dec::zero(), Status::Invalid, "INVALID_INDEX")?;
        timestamp(
            index.source_ns,
            index.received_ns,
            index.available_ns,
            cutoff,
            &q.timing,
            "INDEX",
            &mut out.assumptions,
        )?;
        let fee = math(math(f.rate.checked_mul(&index.value))?.checked_mul(&base))?
            .min(math(f.premium_cap.checked_mul(&premium))?);
        out.fee_native = Some(fee.clone());
        let fx = q
            .quote_to_report
            .as_ref()
            .ok_or_else(|| StatusResult::new(Status::NotEvaluable, "MISSING_EXPLICIT_FX"))?;
        need(
            q.fx_pair
                .as_ref()
                .is_some_and(|(from, to)| from == &q.book.spec.quote_currency && to == report),
            Status::NotEvaluable,
            "FX_PAIR_MISMATCH",
        )?;
        need(
            fx.value > Dec::zero()
                && (q.book.spec.quote_currency != report || fx.value == Dec::one()),
            Status::Invalid,
            "INVALID_FX_VALUE",
        )?;
        timestamp(
            fx.source_ns,
            fx.received_ns,
            fx.available_ns,
            cutoff,
            &q.timing,
            "FX",
            &mut out.assumptions,
        )?;
        let net = if l.side == Side::Buy {
            math(premium.checked_add(&fee))?
        } else {
            math(premium.checked_sub(&fee))?
        };
        out.total_report_currency = Some(math(net.checked_mul(&fx.value))?);
        Ok(())
    })();
    out.status = match result {
        Ok(()) => StatusResult::new(Status::Valid, "DISPLAYED_COST_ONLY_NO_FILL"),
        Err(s) => s,
    };
    out
}
