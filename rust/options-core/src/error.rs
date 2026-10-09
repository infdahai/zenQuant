use serde::{Deserialize, Serialize};
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ErrorCode {
    InvalidDecimal,
    PrecisionLimit,
    NumericalLimitation,
    DivisionByZero,
    InvalidTimestamp,
    InvalidMetadata,
    InvalidSchema,
    InvalidIdentity,
    InvalidInput,
    LimitExceeded,
    DuplicateKey,
    InvalidJson,
    ConflictingEvidence,
    MissingEvidence,
    Unsupported,
    Internal,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub code: ErrorCode,
}
impl Error {
    pub const fn new(code: ErrorCode) -> Self {
        Self { code }
    }
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:?}", self.code)
    }
}
impl std::error::Error for Error {}
pub type Result<T> = std::result::Result<T, Error>;
