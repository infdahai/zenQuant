//! Offline diagnostics and simulated requests; no account, order, live or paper authority.
pub use options_core;
pub mod intent_store;
pub mod io;
pub mod publication;
pub mod report;
mod snapshot_file;
