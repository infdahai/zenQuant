//! Independent simulation request recorder; never a diagnostic-to-order adapter.
use options_workbench::{
    intent_store::{self, Receipt, StoreError},
    options_core::canonical::canonical_bytes,
};
use std::{io::Write, path::PathBuf};

fn execute() -> Result<Option<Receipt>, StoreError> {
    let mut args = std::env::args_os().skip(1);
    let action = args.next().ok_or(StoreError::InvalidInput)?;
    if action == "--help" && args.next().is_none() {
        println!(
            "simulated-intents init --store-dir PATH --store-id ID\nsimulated-intents record --store-dir PATH --request-file PATH\nsimulated-intents replay --store-dir PATH\nSimulation requests only. No risk approval, funds reservation or execution authority."
        );
        return Ok(None);
    }
    let mut store = None;
    let mut id = None;
    let mut request = None;
    while let Some(flag) = args.next() {
        let target = if flag == "--store-dir" {
            &mut store
        } else if flag == "--store-id" {
            &mut id
        } else if flag == "--request-file" {
            &mut request
        } else {
            return Err(StoreError::InvalidInput);
        };
        if target.is_some() {
            return Err(StoreError::InvalidInput);
        }
        *target = Some(args.next().ok_or(StoreError::InvalidInput)?);
    }
    let store = PathBuf::from(store.ok_or(StoreError::InvalidInput)?);
    let result = if action == "init" && request.is_none() {
        let id = id.ok_or(StoreError::InvalidInput)?;
        intent_store::initialize(&store, id.to_str().ok_or(StoreError::InvalidInput)?)
    } else if action == "record" && id.is_none() {
        intent_store::record_file(
            &store,
            &PathBuf::from(request.ok_or(StoreError::InvalidInput)?),
        )
    } else if action == "replay" && request.is_none() && id.is_none() {
        intent_store::replay(&store)
    } else {
        return Err(StoreError::InvalidInput);
    };
    result.map(Some)
}
fn main() {
    let result = execute().and_then(|receipt| {
        if let Some(receipt) = receipt {
            let mut bytes =
                canonical_bytes(&receipt).map_err(|_| StoreError::DurabilityUnconfirmed)?;
            bytes.push(b'\n');
            std::io::stdout()
                .lock()
                .write_all(&bytes)
                .map_err(|_| StoreError::DurabilityUnconfirmed)?;
        }
        Ok(())
    });
    if let Err(error) = result {
        eprintln!("{}", error.message());
        std::process::exit(error.exit_code());
    }
}
