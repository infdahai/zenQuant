//! Stateless observation only: no position, intent, forecast, execution or risk authority.
use crate::{
    canonical::canonical_hash,
    codec,
    contracts::MAX_REQUEST_BYTES,
    decimal::Dec,
    error::{Error, ErrorCode, Result},
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub const REQUEST_SCHEMA: &str = "vix-observation-request.rust.v1";
pub const REPORT_SCHEMA: &str = "vix-observation-report.rust.v1";
pub const MODEL: &str = "monthly-vx-and-spot-horizons.exact.v1";
pub const ROLL_RULE: &str = "MONTHLY_AT_SUPPLIED_LAST_TRADE_V1";
const MAX_TIME: u64 = 8_640_000_000_000_000; // Exact JavaScript Date milliseconds.

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DataMode {
    Demonstration,
    SuppliedSnapshotUnverified,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub schema_version: String,
    pub mode: DataMode,
    pub cutoff_ms: u64,
    pub max_age_ms: u64,
    pub futures: Option<Futures>,
    pub spot: Option<Spot>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub reference: String,
    pub sha256: String,
    pub timezone: String,
    pub known_ms: u64,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Tenor {
    Monthly,
    Weekly,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Contract {
    /// Specific supplied exchange/vendor symbol, never a continuous VX1/VX2 alias.
    pub symbol: String,
    pub tenor: Tenor,
    /// YYYYMM; only used to verify consecutive monthly maturities.
    pub delivery_month: u32,
    /// Explicit calendar values; the core never calculates a recurring expiry rule.
    pub last_trade_ms: u64,
    pub settlement_date: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Calendar {
    pub source: Source,
    pub exchange_timezone: String,
    pub valid_from_ms: u64,
    pub valid_until_ms: u64,
    /// Declares the first unexpired monthly contract at valid_from_ms.
    /// Completeness and authenticity remain UNVERIFIED.
    pub first_delivery_month: u32,
    pub contracts: Vec<Contract>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields, tag = "kind", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Price {
    Settlement { value: Option<Dec> },
    Midpoint { bid: Option<Dec>, ask: Option<Dec> },
    Index { value: Option<Dec> },
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Quote {
    pub symbol: String,
    pub price: Price,
    pub source: Source,
    pub observed_ms: u64,
    pub received_ms: u64,
    pub trading_session_date: String,
    /// None means unknown, never an implicit zero delay.
    pub declared_delay_ms: Option<u64>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreviousPair {
    pub cutoff_ms: u64,
    pub front: String,
    pub next: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Futures {
    pub roll_rule: String,
    pub calendar: Calendar,
    pub quotes: Vec<Quote>,
    pub previous_pair: Option<PreviousPair>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Spot {
    pub vix9d: Option<Quote>,
    pub vix: Option<Quote>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Status {
    Ready,
    Missing,
    Stale,
    Invalid,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Roll {
    UnknownNoPreviousPair,
    UnchangedFromSuppliedPair,
    ChangedFromSuppliedPair,
}
#[derive(Debug, Clone, Serialize)]
pub struct ExactRatio {
    pub numerator: Dec,
    pub denominator: Dec,
    /// None for a nonterminating decimal; the exact fraction always remains available.
    pub decimal_exact: Option<Dec>,
}
#[derive(Debug, Clone, Serialize)]
pub struct Metrics {
    pub first_value: Dec,
    pub second_value: Dec,
    pub spread_points: Dec,
    pub ratio: ExactRatio,
    pub ratio_minus_one: ExactRatio,
    pub classification: &'static str,
}
#[derive(Debug, Clone, Serialize)]
pub struct Panel {
    pub status: Status,
    pub reason: &'static str,
    pub metrics: Option<Metrics>,
    pub selected_contracts: Vec<Contract>,
    pub roll: Roll,
}
impl Panel {
    fn unavailable(status: Status, reason: &'static str) -> Self {
        Self {
            status,
            reason,
            metrics: None,
            selected_contracts: vec![],
            roll: Roll::UnknownNoPreviousPair,
        }
    }
}
#[derive(Debug, Clone, Serialize)]
pub struct Report {
    pub schema_version: &'static str,
    pub model_version: &'static str,
    pub mode: DataMode,
    pub evidence_status: &'static str,
    pub cutoff_ms: u64,
    pub max_age_ms: u64,
    pub input_sha256: String,
    pub futures: Panel,
    pub spot: Panel,
}
type Check<T> = std::result::Result<T, (Status, &'static str)>;
fn invalid<T>(reason: &'static str) -> Check<T> {
    Err((Status::Invalid, reason))
}
fn source(s: &Source, cutoff: u64) -> Check<()> {
    if s.reference.trim().is_empty()
        || s.reference.len() > 512
        || s.sha256.len() != 64
        || !s.sha256.bytes().all(|b| b.is_ascii_hexdigit())
        || !matches!(
            s.timezone.as_str(),
            "UTC" | "America/Chicago" | "America/New_York"
        )
        || s.known_ms > cutoff
    {
        return invalid("INVALID_SOURCE_OR_FUTURE_KNOWLEDGE");
    }
    Ok(())
}
fn month(n: u32) -> Option<u32> {
    let (y, m) = (n / 100, n % 100);
    ((2000..=2199).contains(&y) && (1..=12).contains(&m)).then_some(y * 12 + m - 1)
}
fn valid_date(s: &str) -> bool {
    let b = s.as_bytes();
    if b.len() != 10
        || b[4] != b'-'
        || b[7] != b'-'
        || !b
            .iter()
            .enumerate()
            .all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit())
    {
        return false;
    }
    let y = s[..4].parse::<u32>().unwrap_or(0);
    let m = s[5..7].parse::<usize>().unwrap_or(0);
    let d = s[8..].parse::<u32>().unwrap_or(0);
    let leap = y.is_multiple_of(4) && (!y.is_multiple_of(100) || y.is_multiple_of(400));
    let days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    (2000..=2199).contains(&y) && (1..=12).contains(&m) && d > 0 && d <= days[m - 1]
}
fn date_utc_day(s: &str) -> u64 {
    // Called only after valid_date; Gregorian day identity, not an expiry schedule.
    let year = s[..4].parse::<u32>().unwrap_or(2000);
    let month = s[5..7].parse::<usize>().unwrap_or(1);
    let day = s[8..].parse::<u64>().unwrap_or(1);
    let leap = |y: u32| y.is_multiple_of(4) && (!y.is_multiple_of(100) || y.is_multiple_of(400));
    let years: u64 = (1970..year).map(|y| if leap(y) { 366 } else { 365 }).sum();
    let months = [
        31,
        if leap(year) { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    years + months[..month - 1].iter().sum::<u64>() + day - 1
}
fn pair_at(monthly: &[&Contract], cutoff: u64) -> Check<[Contract; 2]> {
    let mut active = monthly.iter().filter(|c| c.last_trade_ms > cutoff);
    match (active.next(), active.next()) {
        (Some(a), Some(b)) => Ok([(*a).clone(), (*b).clone()]),
        _ => Err((Status::Missing, "TWO_UNEXPIRED_MONTHLY_CONTRACTS_REQUIRED")),
    }
}
fn select(f: &Futures, cutoff: u64) -> Check<([Contract; 2], Roll)> {
    let c = &f.calendar;
    source(&c.source, cutoff)?;
    if f.roll_rule != ROLL_RULE
        || c.exchange_timezone != "America/Chicago"
        || c.valid_from_ms >= c.valid_until_ms
        || c.valid_until_ms > MAX_TIME
        || cutoff < c.valid_from_ms
        || c.source.known_ms > c.valid_from_ms
    {
        return invalid("INVALID_CALENDAR_OR_ROLL_RULE");
    }
    if cutoff >= c.valid_until_ms {
        return Err((Status::Stale, "CALENDAR_EXPIRED"));
    }
    if c.contracts.len() > 32 || f.quotes.len() > 32 {
        return invalid("INPUT_LIMIT");
    }
    let mut symbols = BTreeSet::new();
    let mut monthly = vec![];
    for contract in &c.contracts {
        let m =
            month(contract.delivery_month).ok_or((Status::Invalid, "INVALID_DELIVERY_MONTH"))?;
        // These are CFE-style specific symbols. Reject vendor continuous aliases and mislabeled weeklies.
        let prefix = contract.symbol.split('/').next().unwrap_or("");
        let monthly_symbol = prefix == "VX";
        let weekly_symbol = prefix.len() == 4
            && prefix.starts_with("VX")
            && prefix[2..]
                .parse::<u32>()
                .is_ok_and(|w| (1..=53).contains(&w));
        let code = "FGHJKMNQUVXZ".as_bytes()[(m % 12) as usize] as char;
        let suffix = format!("/{code}{}", (contract.delivery_month / 100) % 10);
        if !symbols.insert(&contract.symbol)
            || contract.symbol != format!("{prefix}{suffix}")
            || (contract.tenor == Tenor::Monthly && !monthly_symbol)
            || (contract.tenor == Tenor::Weekly && !weekly_symbol)
            || !valid_date(&contract.settlement_date)
            || contract.last_trade_ms > MAX_TIME
            || contract.last_trade_ms <= c.valid_from_ms
        {
            return invalid("INVALID_CONTRACT_CALENDAR");
        }
        // VX trading ends in the Chicago morning on its settlement date; that is
        // the same UTC calendar day. Do not accept contradictory supplied identities.
        if contract.last_trade_ms / 86_400_000 != date_utc_day(&contract.settlement_date)
            || contract.settlement_date[..4].parse::<u32>().ok()
                != Some(contract.delivery_month / 100)
            || contract.settlement_date[5..7].parse::<u32>().ok()
                != Some(contract.delivery_month % 100)
        {
            return invalid("CONFLICTING_EXPIRY_DATE");
        }
        if contract.tenor == Tenor::Monthly {
            monthly.push(contract);
        }
    }
    monthly.sort_by_key(|c| c.delivery_month);
    if monthly
        .first()
        .is_none_or(|x| x.delivery_month != c.first_delivery_month)
    {
        return Err((Status::Missing, "FIRST_MONTHLY_CONTRACT_REQUIRED"));
    }
    if monthly.windows(2).any(|w| {
        month(w[0].delivery_month).map(|n| n + 1) != month(w[1].delivery_month)
            || w[0].last_trade_ms >= w[1].last_trade_ms
    }) {
        return invalid("NONCONSECUTIVE_MONTHLY_CALENDAR");
    }
    let pair = pair_at(&monthly, cutoff)?;
    let roll = if let Some(previous) = &f.previous_pair {
        if previous.cutoff_ms < c.valid_from_ms || previous.cutoff_ms >= cutoff {
            return invalid("INVALID_PREVIOUS_PAIR_TIME");
        }
        let expected = pair_at(&monthly, previous.cutoff_ms)?;
        if expected[0].symbol != previous.front || expected[1].symbol != previous.next {
            return invalid("PREVIOUS_PAIR_DISAGREES_WITH_CALENDAR");
        }
        if pair[0].symbol == previous.front && pair[1].symbol == previous.next {
            Roll::UnchangedFromSuppliedPair
        } else {
            Roll::ChangedFromSuppliedPair
        }
    } else {
        Roll::UnknownNoPreviousPair
    };
    Ok((pair, roll))
}
fn quote(q: &Quote, cutoff: u64, max_age: u64) -> Check<Dec> {
    source(&q.source, cutoff)?;
    if q.observed_ms > q.received_ms
        || q.received_ms > q.source.known_ms
        || !valid_date(&q.trading_session_date)
    {
        return invalid("INVALID_QUOTE_TIME");
    }
    let delay = q
        .declared_delay_ms
        .ok_or((Status::Missing, "UNKNOWN_SOURCE_DELAY"))?;
    if delay > q.received_ms - q.observed_ms {
        return invalid("INCONSISTENT_SOURCE_DELAY");
    }
    if cutoff - q.observed_ms > max_age {
        return Err((Status::Stale, "QUOTE_EXPIRED"));
    }
    let positive = |p: &Option<Dec>| -> Check<Dec> {
        let p = p.as_ref().ok_or((Status::Missing, "MISSING_PRICE"))?;
        if p <= &Dec::zero() {
            return invalid("NONPOSITIVE_PRICE");
        }
        Ok(p.clone())
    };
    match &q.price {
        Price::Settlement { value } | Price::Index { value } => positive(value),
        Price::Midpoint { bid, ask } => {
            let (bid, ask) = (positive(bid)?, positive(ask)?);
            if bid > ask {
                return invalid("CROSSED_MARKET");
            }
            bid.checked_add(&ask)
                .and_then(|v| v.checked_div(&Dec::parse("2")?))
                .map_err(|_| (Status::Invalid, "NUMERICAL_LIMIT"))
        }
    }
}
fn metrics(a: &Quote, b: &Quote, r: &Request, futures: bool) -> Check<Metrics> {
    let (x, y) = (
        quote(a, r.cutoff_ms, r.max_age_ms)?,
        quote(b, r.cutoff_ms, r.max_age_ms)?,
    );
    if a.observed_ms != b.observed_ms || a.trading_session_date != b.trading_session_date {
        return invalid("ASYNCHRONOUS_OR_DIFFERENT_SESSION_QUOTES");
    }
    let same_basis = matches!(
        (&a.price, &b.price),
        (Price::Settlement { .. }, Price::Settlement { .. })
            | (Price::Midpoint { .. }, Price::Midpoint { .. })
    );
    if (futures && !same_basis)
        || (!futures
            && !matches!(
                (&a.price, &b.price),
                (Price::Index { .. }, Price::Index { .. })
            ))
    {
        return invalid("INCOMPATIBLE_PRICE_BASIS");
    }
    let spread = x
        .checked_sub(&y)
        .map_err(|_| (Status::Invalid, "NUMERICAL_LIMIT"))?;
    let exact_ratio = |numerator: &Dec| -> Check<ExactRatio> {
        let decimal_exact = match numerator.checked_div(&y) {
            Ok(v) => Some(v),
            Err(e) if e.code == ErrorCode::NumericalLimitation => None,
            Err(_) => return invalid("NUMERICAL_LIMIT"),
        };
        Ok(ExactRatio {
            numerator: numerator.clone(),
            denominator: y.clone(),
            decimal_exact,
        })
    };
    let ratio = exact_ratio(&x)?;
    let ratio_minus_one = exact_ratio(&spread)?;
    let classification = match (futures, spread.is_zero(), spread.is_negative()) {
        (true, true, _) => "FLAT",
        (true, false, true) => "CONTANGO",
        (true, false, false) => "BACKWARDATION",
        (false, true, _) => "EQUAL_HORIZON_VALUES",
        (false, false, true) => "SHORT_HORIZON_LOWER",
        (false, false, false) => "SHORT_HORIZON_HIGHER",
    };
    Ok(Metrics {
        first_value: x,
        second_value: y.clone(),
        spread_points: spread.clone(),
        ratio,
        ratio_minus_one,
        classification,
    })
}
fn futures_panel(r: &Request) -> Panel {
    let Some(f) = &r.futures else {
        return Panel::unavailable(Status::Missing, "FUTURES_INPUT_REQUIRED");
    };
    let (pair, roll) = match select(f, r.cutoff_ms) {
        Ok(x) => x,
        Err((status, reason)) => return Panel::unavailable(status, reason),
    };
    let calculate = || -> Check<Metrics> {
        let mut seen = BTreeSet::new();
        if f.quotes.iter().any(|q| {
            !seen.insert(&q.symbol) || !f.calendar.contracts.iter().any(|c| c.symbol == q.symbol)
        }) {
            return invalid("DUPLICATE_OR_UNKNOWN_QUOTE_SYMBOL");
        }
        let find = |c: &Contract| {
            f.quotes
                .iter()
                .find(|q| q.symbol == c.symbol)
                .ok_or((Status::Missing, "SELECTED_MONTHLY_QUOTE_REQUIRED"))
        };
        metrics(find(&pair[0])?, find(&pair[1])?, r, true)
    };
    let mut panel = match calculate() {
        Ok(m) => Panel {
            status: Status::Ready,
            reason: "AT_INPUT_CUTOFF_ONLY",
            metrics: Some(m),
            selected_contracts: vec![],
            roll,
        },
        Err((status, reason)) => Panel::unavailable(status, reason),
    };
    panel.selected_contracts = pair.into();
    panel.roll = roll;
    panel
}
fn spot_panel(r: &Request) -> Panel {
    let result = || -> Check<Metrics> {
        let s = r
            .spot
            .as_ref()
            .ok_or((Status::Missing, "SPOT_INPUT_REQUIRED"))?;
        let (Some(a), Some(b)) = (&s.vix9d, &s.vix) else {
            return Err((Status::Missing, "BOTH_SPOT_INDICES_REQUIRED"));
        };
        if a.symbol != "VIX9D" || b.symbol != "VIX" {
            return invalid("WRONG_SPOT_INDEX_IDENTITY");
        }
        metrics(a, b, r, false)
    };
    match result() {
        Ok(m) => Panel {
            status: Status::Ready,
            reason: "AT_INPUT_CUTOFF_ONLY",
            metrics: Some(m),
            selected_contracts: vec![],
            roll: Roll::UnknownNoPreviousPair,
        },
        Err((status, reason)) => Panel::unavailable(status, reason),
    }
}
pub fn decode_request(bytes: &[u8]) -> Result<Request> {
    codec::decode(bytes, MAX_REQUEST_BYTES)
}
pub fn analyze(r: &Request) -> Result<Report> {
    if r.schema_version != REQUEST_SCHEMA {
        return Err(Error::new(ErrorCode::InvalidSchema));
    }
    if r.cutoff_ms > MAX_TIME || r.max_age_ms == 0 || r.max_age_ms > 86_400_000 {
        return Err(Error::new(ErrorCode::InvalidTimestamp));
    }
    Ok(Report {
        schema_version: REPORT_SCHEMA,
        model_version: MODEL,
        mode: r.mode,
        evidence_status: "UNVERIFIED",
        cutoff_ms: r.cutoff_ms,
        max_age_ms: r.max_age_ms,
        input_sha256: canonical_hash(r)?,
        futures: futures_panel(r),
        spot: spot_panel(r),
    })
}
