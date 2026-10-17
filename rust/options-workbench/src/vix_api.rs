//! Read-only snapshot evaluation. No feed credentials, arbitrary paths, storage or trading calls.
use options_core::{
    canonical::{bytes_sha256, canonical_hash},
    codec,
    contracts::MAX_REQUEST_BYTES,
    error::{Error, ErrorCode, Result},
    vix_observation::{self as vix, Price, Request},
};
use serde::{Deserialize, Serialize};

pub const DEMO: &[u8] = include_bytes!("../fixtures/vix/demo.json");
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DemoRequest {
    scenario: Scenario,
}
#[derive(Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum Scenario {
    Backwardation,
    Contango,
    Flat,
    Missing,
    Stale,
    Asynchronous,
    Roll,
}

fn demonstration(bytes: &[u8]) -> Result<Request> {
    let choice: DemoRequest = codec::decode(bytes, MAX_REQUEST_BYTES)?;
    let mut r = vix::decode_request(DEMO)?;
    let f = r.futures.as_mut().ok_or(Error::new(ErrorCode::Internal))?;
    match choice.scenario {
        Scenario::Backwardation => {}
        Scenario::Contango => {
            f.quotes[0].price = Price::Settlement {
                value: Some("18".parse()?),
            }
        }
        Scenario::Flat => {
            f.quotes[0].price = Price::Settlement {
                value: Some("20".parse()?),
            }
        }
        Scenario::Missing => {
            f.quotes.remove(0);
        }
        Scenario::Stale => {
            r.cutoff_ms += r.max_age_ms;
        }
        Scenario::Asynchronous => {
            f.quotes[1].observed_ms -= 1;
        }
        Scenario::Roll => {
            let cutoff = f.calendar.contracts[0].last_trade_ms;
            f.previous_pair = Some(vix::PreviousPair {
                cutoff_ms: r.cutoff_ms,
                front: "VX/V6".into(),
                next: "VX/X6".into(),
            });
            r.cutoff_ms = cutoff;
            // Fixed synthetic post-roll snapshot; no market download or quote interpolation.
            for q in f.quotes.iter_mut().chain(
                r.spot
                    .iter_mut()
                    .flat_map(|s| s.vix9d.iter_mut().chain(s.vix.iter_mut())),
            ) {
                q.observed_ms = cutoff - 1000;
                q.received_ms = cutoff;
                q.source.known_ms = cutoff;
                q.trading_session_date = "2026-10-21".into();
            }
        }
    }
    Ok(r)
}
#[derive(Serialize)]
struct Response {
    report: vix::Report,
    request_json: String,
    transport_sha256: String,
    code_sha256: String,
    cargo_lock_sha256: String,
}
pub fn evaluate(path: &str, bytes: &[u8]) -> Result<Vec<u8>> {
    let r = match path {
        "/api/vix/demo" => demonstration(bytes)?,
        "/api/vix/evaluate" => vix::decode_request(bytes)?,
        _ => return Err(Error::new(ErrorCode::InvalidInput)),
    };
    let response = Response {
        report: vix::analyze(&r)?,
        request_json: serde_json::to_string_pretty(&r)
            .map_err(|_| Error::new(ErrorCode::Internal))?,
        transport_sha256: bytes_sha256(bytes),
        code_sha256: canonical_hash(
            &crate::report::CODE_FILES
                .iter()
                .map(|(name, bytes)| (*name, bytes_sha256(bytes)))
                .collect::<Vec<_>>(),
        )?,
        cargo_lock_sha256: bytes_sha256(crate::report::LOCK_BYTES),
    };
    serde_json::to_vec(&response).map_err(|_| Error::new(ErrorCode::Internal))
}
