use crate::error::{Error, ErrorCode, Result};
use serde::de::{DeserializeOwned, DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde_json::Value;
use std::fmt;

pub fn decode<T: DeserializeOwned>(bytes: &[u8], limit: usize) -> Result<T> {
    if bytes.len() > limit {
        return Err(Error::new(ErrorCode::LimitExceeded));
    }
    let mut de = serde_json::Deserializer::from_slice(bytes);
    let value = Node(0).deserialize(&mut de).map_err(|e| {
        let s = e.to_string();
        Error::new(if s.contains("duplicate key") {
            ErrorCode::DuplicateKey
        } else if s.contains("limit exceeded") {
            ErrorCode::LimitExceeded
        } else {
            ErrorCode::InvalidJson
        })
    })?;
    de.end().map_err(|_| Error::new(ErrorCode::InvalidJson))?;
    serde_json::from_value(value).map_err(|_| Error::new(ErrorCode::InvalidInput))
}
struct Node(usize);
impl<'de> DeserializeSeed<'de> for Node {
    type Value = Value;
    fn deserialize<D: serde::Deserializer<'de>>(
        self,
        d: D,
    ) -> std::result::Result<Value, D::Error> {
        if self.0 > crate::contracts::MAX_JSON_DEPTH {
            return Err(serde::de::Error::custom("limit exceeded"));
        }
        d.deserialize_any(self)
    }
}
impl<'de> Visitor<'de> for Node {
    type Value = Value;
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("strict JSON")
    }
    fn visit_bool<E: serde::de::Error>(self, v: bool) -> std::result::Result<Value, E> {
        Ok(Value::Bool(v))
    }
    fn visit_unit<E: serde::de::Error>(self) -> std::result::Result<Value, E> {
        Ok(Value::Null)
    }
    fn visit_i64<E: serde::de::Error>(self, v: i64) -> std::result::Result<Value, E> {
        Ok(v.into())
    }
    fn visit_u64<E: serde::de::Error>(self, v: u64) -> std::result::Result<Value, E> {
        Ok(v.into())
    }
    fn visit_f64<E: serde::de::Error>(self, _: f64) -> std::result::Result<Value, E> {
        Err(E::custom("float forbidden"))
    }
    fn visit_str<E: serde::de::Error>(self, v: &str) -> std::result::Result<Value, E> {
        if v.len() > crate::contracts::MAX_STRING_BYTES {
            return Err(E::custom("limit exceeded"));
        }
        Ok(v.into())
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> std::result::Result<Value, A::Error> {
        let mut out = Vec::new();
        while let Some(v) = a.next_element_seed(Node(self.0 + 1))? {
            if out.len() >= 4096 {
                return Err(serde::de::Error::custom("limit exceeded"));
            }
            out.push(v);
        }
        Ok(Value::Array(out))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> std::result::Result<Value, A::Error> {
        let mut out = serde_json::Map::new();
        while let Some(k) = a.next_key::<String>()? {
            if k.len() > crate::contracts::MAX_STRING_BYTES || out.len() >= 4096 {
                return Err(serde::de::Error::custom("limit exceeded"));
            }
            if out.contains_key(&k) {
                return Err(serde::de::Error::custom("duplicate key"));
            }
            out.insert(k, a.next_value_seed(Node(self.0 + 1))?);
        }
        Ok(Value::Object(out))
    }
}
