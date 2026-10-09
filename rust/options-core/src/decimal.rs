//! Bounded exact finite decimals. No floating point or rounded arithmetic.
use crate::error::{Error, ErrorCode, Result};
use num_bigint::BigInt;
use num_rational::BigRational;
use num_traits::{Signed, Zero};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::{fmt, str::FromStr};
pub const INPUT_SIGNIFICANT_DIGITS: usize = 64;
pub const INPUT_FRACTIONAL_DIGITS: usize = 28;
pub const MAX_INTEGER_DIGITS: usize = 1024;
pub const MAX_RESULT_FRACTIONAL_DIGITS: usize = 256;
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct Dec(BigRational);
impl Dec {
    pub fn parse(s: &str) -> Result<Self> {
        if s.len() > INPUT_SIGNIFICANT_DIGITS + INPUT_FRACTIONAL_DIGITS + 3 {
            return Err(Error::new(ErrorCode::PrecisionLimit));
        }
        let unsigned = s.strip_prefix('-').unwrap_or(s);
        let mut parts = unsigned.split('.');
        let whole = parts.next().unwrap_or("");
        let frac = parts.next();
        if parts.next().is_some()
            || unsigned.is_empty()
            || !whole.bytes().all(|b| b.is_ascii_digit())
            || frac.is_some_and(|v| v.is_empty() || !v.bytes().all(|b| b.is_ascii_digit()))
            || (whole.is_empty() && frac.is_none())
        {
            return Err(Error::new(ErrorCode::InvalidDecimal));
        }
        let frac = frac.unwrap_or("");
        let digits = format!("{whole}{frac}");
        if frac.len() > INPUT_FRACTIONAL_DIGITS
            || digits.trim_start_matches('0').len() > INPUT_SIGNIFICANT_DIGITS
        {
            return Err(Error::new(ErrorCode::PrecisionLimit));
        }
        let mut numerator =
            BigInt::from_str(&digits).map_err(|_| Error::new(ErrorCode::InvalidDecimal))?;
        if s.starts_with('-') {
            numerator = -numerator;
        }
        Self::bounded(BigRational::new(
            numerator,
            BigInt::from(10).pow(frac.len() as u32),
        ))
    }
    fn bounded(value: BigRational) -> Result<Self> {
        if value.numer().abs().to_str_radix(10).len() > MAX_INTEGER_DIGITS
            || value.denom().to_str_radix(10).len() > MAX_INTEGER_DIGITS
        {
            return Err(Error::new(ErrorCode::PrecisionLimit));
        }
        let dec = Self(value);
        dec.canonical_string()?;
        Ok(dec)
    }
    pub fn zero() -> Self {
        Self(BigRational::zero())
    }
    pub fn one() -> Self {
        Self(BigRational::from_integer(BigInt::from(1)))
    }
    pub fn is_negative(&self) -> bool {
        self.0.is_negative()
    }
    pub fn is_zero(&self) -> bool {
        self.0.is_zero()
    }
    pub fn checked_add(&self, rhs: &Self) -> Result<Self> {
        Self::bounded(&self.0 + &rhs.0)
    }
    pub fn checked_sub(&self, rhs: &Self) -> Result<Self> {
        Self::bounded(&self.0 - &rhs.0)
    }
    pub fn checked_mul(&self, rhs: &Self) -> Result<Self> {
        Self::bounded(&self.0 * &rhs.0)
    }
    pub fn checked_div(&self, rhs: &Self) -> Result<Self> {
        if rhs.is_zero() {
            return Err(Error::new(ErrorCode::DivisionByZero));
        }
        Self::bounded(&self.0 / &rhs.0)
    }
    pub fn checked_neg(&self) -> Result<Self> {
        Self::bounded(-&self.0)
    }
    pub fn abs(&self) -> Self {
        Self(self.0.abs())
    }
    pub fn is_multiple_of(&self, step: &Self) -> bool {
        !step.is_zero() && (&self.0 / &step.0).is_integer()
    }
    pub fn canonical_string(&self) -> Result<String> {
        if self.is_zero() {
            return Ok("0".into());
        }
        let numerator = self.0.numer().abs();
        let denominator = self.0.denom();
        let mut integer = (&numerator / denominator).to_str_radix(10);
        let mut remainder = &numerator % denominator;
        if !remainder.is_zero() {
            integer.push('.');
            for _ in 0..MAX_RESULT_FRACTIONAL_DIGITS {
                remainder *= 10;
                integer.push_str(&(&remainder / denominator).to_str_radix(10));
                remainder %= denominator;
                if remainder.is_zero() {
                    break;
                }
            }
            if !remainder.is_zero() {
                return Err(Error::new(ErrorCode::NumericalLimitation));
            }
        }
        if self.is_negative() {
            integer.insert(0, '-');
        }
        Ok(integer)
    }
}
impl FromStr for Dec {
    type Err = Error;
    fn from_str(s: &str) -> Result<Self> {
        Self::parse(s)
    }
}
impl fmt::Display for Dec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.canonical_string().map_err(|_| fmt::Error)?)
    }
}
impl Serialize for Dec {
    fn serialize<S: Serializer>(&self, s: S) -> std::result::Result<S::Ok, S::Error> {
        s.serialize_str(&self.canonical_string().map_err(serde::ser::Error::custom)?)
    }
}
impl<'de> Deserialize<'de> for Dec {
    fn deserialize<D: Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        let s = String::deserialize(d)?;
        Self::parse(&s).map_err(serde::de::Error::custom)
    }
}
