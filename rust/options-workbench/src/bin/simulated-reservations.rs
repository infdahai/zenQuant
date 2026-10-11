//! Repository-scoped simulation cash reservations. No store/clock override or orders.
use options_workbench::{
    options_core::canonical::canonical_bytes,
    reservation_store::{self as store, Failure},
};
use std::{io::Write, path::PathBuf};
fn execute() -> Result<Option<store::Receipt>, Failure> {
    let mut args = std::env::args_os().skip(1);
    let action = args.next().ok_or(Failure::InvalidInput)?;
    if action == "--help" && args.next().is_none() {
        println!(
            "simulated-reservations init\nsimulated-reservations fund --request-file PATH\nsimulated-reservations reserve --request-file PATH\nsimulated-reservations replay\nOne repository simulation authority. No orders, real funds, release, TTL expiry or clock override."
        );
        return Ok(None);
    }
    if action == "init" && args.next().is_none() {
        return store::initialize().map(Some);
    }
    if action == "replay" && args.next().is_none() {
        return store::replay().map(Some);
    }
    if action != "fund" && action != "reserve" {
        return Err(Failure::InvalidInput);
    }
    if args.next().is_none_or(|flag| flag != "--request-file") {
        return Err(Failure::InvalidInput);
    }
    let path = PathBuf::from(args.next().ok_or(Failure::InvalidInput)?);
    if args.next().is_some() {
        return Err(Failure::InvalidInput);
    }
    if action == "fund" {
        store::fund_file(&path).map(Some)
    } else {
        store::reserve_file(&path).map(Some)
    }
}
fn main() {
    let result = execute().and_then(|receipt| {
        if let Some(receipt) = receipt {
            let mut bytes = canonical_bytes(&receipt).map_err(|_| Failure::Corrupt)?;
            bytes.push(b'\n');
            std::io::stdout().lock().write_all(&bytes).map_err(|_| {
                Failure::Storage(options_workbench::intent_store::StoreError::DurabilityUnconfirmed)
            })?;
        }
        Ok(())
    });
    if let Err(e) = result {
        eprintln!("{}", e.message());
        std::process::exit(e.exit_code());
    }
}
