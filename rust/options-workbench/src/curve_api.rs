//! Thin synchronous batch API. Synthetic controls are explicitly separate from evidence input.
use options_core::{
    canonical::{bytes_sha256, canonical_hash},
    contracts::*,
    decimal::Dec,
    error::{Error, ErrorCode, Result},
    fixtures,
    payoff_curve::{self as curve, ManualSignal},
};
use serde::{Deserialize, Serialize};
use std::time::Instant;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SyntheticParameters {
    pub option_type: OptionType,
    pub strike: Dec,
    pub quantity: Dec,
    pub ask: Dec,
    pub bid: Dec,
    pub fee_rate: Option<Dec>,
    pub terminal_fee: Option<Dec>,
    pub first: Dec,
    pub step: Dec,
    pub count: usize,
    pub signal_hypothesis: Option<String>,
    /// Strings on this browser boundary prevent JavaScript integer rounding.
    pub signal_known_ns: String,
    pub signal_valid_until_ns: String,
}

pub fn synthetic(bytes: &[u8]) -> Result<curve::Request> {
    let p: SyntheticParameters = options_core::codec::decode(bytes, MAX_REQUEST_BYTES)?;
    let mut r = fixtures::mathematical_curve()?;
    let c = r.candidate.as_mut().expect("fixed fixture candidate");
    c.spec = fixtures::mathematical_spec(p.option_type, p.strike);
    c.leg.instrument = InstrumentDescriptor::from_spec(&c.spec)?;
    c.leg.quantity_native = p.quantity.clone();
    let mut quote = fixtures::mathematical_quote(c.spec.clone(), p.ask, p.bid);
    quote.book.asks[0].quantity_native = p.quantity;
    // No invented executable depth: this is visibly an explicit synthetic depth assumption.
    quote.book.bids[0].quantity_native = quote.book.asks[0].quantity_native.clone();
    quote.fee_policy = match p.fee_rate {
        Some(rate) => {
            let mut f = quote.fee_policy.take().expect("fixture fees");
            f.rate = rate;
            Some(f)
        }
        None => None,
    };
    if quote.fee_policy.is_none() {
        quote.fee_known_ns = None;
    }
    c.quotes = vec![quote];
    c.terminal_fee =
        p.terminal_fee.map(
            |amount_report| options_core::terminal_diagnostics::TerminalFee {
                amount_report,
                known_ns: 0,
                source_ref: "EXPLICIT_SYNTHETIC_TOTAL_TERMINAL_FEE".into(),
            },
        );
    let g = r.grid.as_mut().expect("fixed fixture grid");
    g.spec_hash = canonical_hash(&c.spec)?;
    g.first = p.first;
    g.step = p.step;
    g.count = p.count;
    r.signal = p
        .signal_hypothesis
        .map(|hypothesis| {
            let parse = |s: &str| {
                s.parse::<u64>()
                    .map_err(|_| Error::new(ErrorCode::InvalidTimestamp))
            };
            Ok(ManualSignal {
                schema_version: curve::SIGNAL_SCHEMA.into(),
                source_ref: "MANUAL_HYPOTHESIS_ON_SYNTHETIC_CLOCK".into(),
                source_sha256: bytes_sha256(hypothesis.as_bytes()),
                observed_ns: parse(&p.signal_known_ns)?,
                known_ns: parse(&p.signal_known_ns)?,
                valid_until_ns: parse(&p.signal_valid_until_ns)?,
                hypothesis,
            })
        })
        .transpose()?;
    Ok(r)
}

#[derive(Serialize)]
pub struct Response {
    pub report: curve::Report,
    pub request_json: String,
    pub calculation_us: u128,
    pub transport_sha256: String,
    pub code_sha256: String,
    pub cargo_lock_sha256: String,
}
pub fn evaluate(path: &str, bytes: &[u8]) -> Result<Vec<u8>> {
    let r = match path {
        "/api/synthetic" => synthetic(bytes)?,
        "/api/curve" => curve::decode_request(bytes)?,
        _ => return Err(Error::new(ErrorCode::InvalidInput)),
    };
    let start = Instant::now();
    let report = curve::analyze(&r)?;
    let calculation_us = start.elapsed().as_micros();
    let request_json =
        serde_json::to_string_pretty(&r).map_err(|_| Error::new(ErrorCode::Internal))?;
    serde_json::to_vec(&Response {
        report,
        request_json,
        calculation_us,
        transport_sha256: bytes_sha256(bytes),
        code_sha256: canonical_hash(
            &crate::report::CODE_FILES
                .iter()
                .map(|(name, bytes)| (*name, bytes_sha256(bytes)))
                .collect::<Vec<_>>(),
        )?,
        cargo_lock_sha256: bytes_sha256(crate::report::LOCK_BYTES),
    })
    .map_err(|_| Error::new(ErrorCode::Internal))
}
