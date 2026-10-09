#![forbid(unsafe_code)]
//! Pure native Rust diagnostic contracts. No network, filesystem, clock or execution APIs.
pub mod canonical;
pub mod codec;
pub mod contracts;
pub mod costs;
pub mod decimal;
pub mod error;
pub mod expressions;
pub mod fixtures;
pub mod inputs;
