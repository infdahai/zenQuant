//! Offline diagnostics and simulated requests; no account, order, live or paper authority.
pub use options_core;
pub mod curve_api;
pub mod curve_http;
pub mod intent_store;
pub mod io;
pub mod paper_store;
pub mod publication;
pub mod report;
pub mod reservation_store;
mod snapshot_file;
pub mod vix_api;
