#![forbid(unsafe_code)]
//! Pure native Rust diagnostic contracts. No network, filesystem, clock or execution APIs.
pub mod canonical;
pub mod contracts;
pub mod decimal;
pub mod error;
pub mod fixtures;
