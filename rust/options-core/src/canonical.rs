//! Rust-specific canonical JSON: recursively sorted keys, ordered arrays, Dec strings.
use crate::contracts::{MAX_JSON_DEPTH, MAX_STRING_BYTES};
use crate::{
    contracts::ARITHMETIC_METHOD,
    error::{Error, ErrorCode, Result},
};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
fn sorted(v: Value) -> Value {
    match v {
        Value::Object(m) => Value::Object(m.into_iter().map(|(k, v)| (k, sorted(v))).collect()),
        Value::Array(a) => Value::Array(a.into_iter().map(sorted).collect()),
        v => v,
    }
}
fn validate(value: &Value, depth: usize) -> Result<()> {
    if depth > MAX_JSON_DEPTH {
        return Err(Error::new(ErrorCode::LimitExceeded));
    }
    match value {
        Value::Number(n) if !n.is_i64() && !n.is_u64() => {
            return Err(Error::new(ErrorCode::InvalidInput));
        }
        Value::String(s) if s.len() > MAX_STRING_BYTES => {
            return Err(Error::new(ErrorCode::LimitExceeded));
        }
        Value::Array(a) => {
            for item in a {
                validate(item, depth + 1)?;
            }
        }
        Value::Object(m) => {
            for (k, v) in m {
                if k.len() > MAX_STRING_BYTES {
                    return Err(Error::new(ErrorCode::LimitExceeded));
                }
                validate(v, depth + 1)?;
            }
        }
        _ => {}
    }
    Ok(())
}
pub fn canonical_bytes<T: Serialize>(value: &T) -> Result<Vec<u8>> {
    let value = serde_json::to_value(value).map_err(|_| Error::new(ErrorCode::InvalidInput))?;
    validate(&value, 0)?;
    serde_json::to_vec(&sorted(value)).map_err(|_| Error::new(ErrorCode::InvalidInput))
}
pub fn bytes_sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn canonical_hash<T: Serialize>(value: &T) -> Result<String> {
    let mut bytes = ARITHMETIC_METHOD.as_bytes().to_vec();
    bytes.push(0);
    bytes.extend(canonical_bytes(value)?);
    Ok(bytes_sha256(&bytes))
}
