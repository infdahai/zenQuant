//! Shared pure specification, quantity and price rules. No analysis or trading dependency.
use crate::{
    contracts::*,
    decimal::Dec,
    error::{Error, ErrorCode, Result},
};
pub(crate) fn require(ok: bool, code: ErrorCode) -> Result<()> {
    if ok { Ok(()) } else { Err(Error::new(code)) }
}
fn positive(x: &Dec) -> bool {
    !x.is_negative() && !x.is_zero()
}
pub(crate) fn text(x: &str) -> bool {
    !x.trim().is_empty() && x.len() <= MAX_STRING_BYTES
}
pub(crate) fn validate_spec(s: &InstrumentSpec) -> Result<()> {
    require(
        [
            &s.venue,
            &s.instrument_id,
            &s.underlying,
            &s.quote_currency,
            &s.strike_currency,
            &s.settlement_currency,
            &s.margin_currency,
            &s.settlement_index,
            &s.rule_version,
        ]
        .iter()
        .all(|x| text(x))
            && valid_sha256(&s.metadata_hash),
        ErrorCode::InvalidMetadata,
    )?;
    require(
        positive(&s.strike)
            && positive(&s.multiplier)
            && positive(&s.min_quantity_native)
            && positive(&s.quantity_step_native)
            && s.min_quantity_native
                .is_multiple_of(&s.quantity_step_native)
            && s.max_quantity_native.as_ref().is_none_or(|x| {
                x >= &s.min_quantity_native && x.is_multiple_of(&s.quantity_step_native)
            }),
        ErrorCode::InvalidInput,
    )?;
    require(
        s.expiry_ns > s.valid_from_ns
            && s.valid_until_ns.is_none_or(|t| t > s.valid_from_ns)
            && s.settlement_window_ns > 0,
        ErrorCode::InvalidTimestamp,
    )?;
    require(
        !s.tick_bands.is_empty() && s.tick_bands.len() <= MAX_LEVELS && s.tick_bands[0].0.is_zero(),
        ErrorCode::InvalidInput,
    )?;
    for (i, (bound, tick)) in s.tick_bands.iter().enumerate() {
        require(
            !bound.is_negative() && positive(tick) && (i == 0 || bound > &s.tick_bands[i - 1].0),
            ErrorCode::InvalidInput,
        )?;
    }
    Ok(())
}
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
pub(crate) fn tick(s: &InstrumentSpec, p: &Dec) -> Diagnostic<()> {
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
