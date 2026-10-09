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
    let value = value
        .serialize(ExactSerializer)
        .map_err(|_| Error::new(ErrorCode::InvalidInput))?;
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

// Serialize once into exact JSON. In particular serde_json's NaN -> null transform
// must never run on generic Serialize inputs.
#[derive(Debug)]
struct SerializationError;
impl std::fmt::Display for SerializationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("INVALID_INPUT")
    }
}
impl std::error::Error for SerializationError {}
impl serde::ser::Error for SerializationError {
    fn custom<T: std::fmt::Display>(_: T) -> Self {
        Self
    }
}
type SerResult<T> = std::result::Result<T, SerializationError>;
struct ExactSerializer;
struct Compound {
    array: Vec<Value>,
    map: serde_json::Map<String, Value>,
    key: Option<String>,
    variant: Option<String>,
    is_map: bool,
}
impl Compound {
    fn array(variant: Option<String>) -> Self {
        Self {
            array: vec![],
            map: serde_json::Map::new(),
            key: None,
            variant,
            is_map: false,
        }
    }
    fn map(variant: Option<String>) -> Self {
        Self {
            is_map: true,
            ..Self::array(variant)
        }
    }
    fn finish(self) -> Value {
        let value = if self.is_map {
            Value::Object(self.map)
        } else {
            Value::Array(self.array)
        };
        if let Some(variant) = self.variant {
            let mut map = serde_json::Map::new();
            map.insert(variant, value);
            Value::Object(map)
        } else {
            value
        }
    }
}
impl serde::Serializer for ExactSerializer {
    type Ok = Value;
    type Error = SerializationError;
    type SerializeSeq = Compound;
    type SerializeTuple = Compound;
    type SerializeTupleStruct = Compound;
    type SerializeTupleVariant = Compound;
    type SerializeMap = Compound;
    type SerializeStruct = Compound;
    type SerializeStructVariant = Compound;
    fn serialize_i8(self, v: i8) -> SerResult<Value> {
        Ok(Value::Number(v.into()))
    }
    fn serialize_i16(self, v: i16) -> SerResult<Value> {
        Ok(Value::Number(v.into()))
    }
    fn serialize_i32(self, v: i32) -> SerResult<Value> {
        Ok(Value::Number(v.into()))
    }
    fn serialize_i64(self, v: i64) -> SerResult<Value> {
        Ok(Value::Number(v.into()))
    }
    fn serialize_u8(self, v: u8) -> SerResult<Value> {
        Ok(Value::Number(v.into()))
    }
    fn serialize_u16(self, v: u16) -> SerResult<Value> {
        Ok(Value::Number(v.into()))
    }
    fn serialize_u32(self, v: u32) -> SerResult<Value> {
        Ok(Value::Number(v.into()))
    }
    fn serialize_u64(self, v: u64) -> SerResult<Value> {
        Ok(Value::Number(v.into()))
    }

    fn serialize_f32(self, _: f32) -> SerResult<Value> {
        Err(SerializationError)
    }
    fn serialize_f64(self, _: f64) -> SerResult<Value> {
        Err(SerializationError)
    }
    fn serialize_bool(self, v: bool) -> SerResult<Value> {
        Ok(Value::Bool(v))
    }
    fn serialize_char(self, v: char) -> SerResult<Value> {
        Ok(Value::String(v.to_string()))
    }
    fn serialize_str(self, v: &str) -> SerResult<Value> {
        Ok(Value::String(v.into()))
    }
    fn serialize_bytes(self, v: &[u8]) -> SerResult<Value> {
        Ok(Value::Array(
            v.iter().map(|v| Value::Number((*v).into())).collect(),
        ))
    }
    fn serialize_none(self) -> SerResult<Value> {
        Ok(Value::Null)
    }
    fn serialize_some<T: ?Sized + Serialize>(self, v: &T) -> SerResult<Value> {
        v.serialize(self)
    }
    fn serialize_unit(self) -> SerResult<Value> {
        Ok(Value::Null)
    }
    fn serialize_unit_struct(self, _: &'static str) -> SerResult<Value> {
        Ok(Value::Null)
    }
    fn serialize_unit_variant(self, _: &'static str, _: u32, v: &'static str) -> SerResult<Value> {
        Ok(Value::String(v.into()))
    }
    fn serialize_newtype_struct<T: ?Sized + Serialize>(
        self,
        _: &'static str,
        v: &T,
    ) -> SerResult<Value> {
        v.serialize(self)
    }
    fn serialize_newtype_variant<T: ?Sized + Serialize>(
        self,
        _: &'static str,
        _: u32,
        k: &'static str,
        v: &T,
    ) -> SerResult<Value> {
        let mut m = serde_json::Map::new();
        m.insert(k.into(), v.serialize(self)?);
        Ok(Value::Object(m))
    }
    fn serialize_seq(self, _: Option<usize>) -> SerResult<Compound> {
        Ok(Compound::array(None))
    }
    fn serialize_tuple(self, _: usize) -> SerResult<Compound> {
        Ok(Compound::array(None))
    }
    fn serialize_tuple_struct(self, _: &'static str, _: usize) -> SerResult<Compound> {
        Ok(Compound::array(None))
    }
    fn serialize_tuple_variant(
        self,
        _: &'static str,
        _: u32,
        v: &'static str,
        _: usize,
    ) -> SerResult<Compound> {
        Ok(Compound::array(Some(v.into())))
    }
    fn serialize_map(self, _: Option<usize>) -> SerResult<Compound> {
        Ok(Compound::map(None))
    }
    fn serialize_struct(self, _: &'static str, _: usize) -> SerResult<Compound> {
        Ok(Compound::map(None))
    }
    fn serialize_struct_variant(
        self,
        _: &'static str,
        _: u32,
        v: &'static str,
        _: usize,
    ) -> SerResult<Compound> {
        Ok(Compound::map(Some(v.into())))
    }
}
impl serde::ser::SerializeSeq for Compound {
    type Ok = Value;
    type Error = SerializationError;
    fn serialize_element<T: ?Sized + Serialize>(&mut self, v: &T) -> SerResult<()> {
        self.array.push(v.serialize(ExactSerializer)?);
        Ok(())
    }
    fn end(self) -> SerResult<Value> {
        Ok(self.finish())
    }
}
impl serde::ser::SerializeTuple for Compound {
    type Ok = Value;
    type Error = SerializationError;
    fn serialize_element<T: ?Sized + Serialize>(&mut self, v: &T) -> SerResult<()> {
        self.array.push(v.serialize(ExactSerializer)?);
        Ok(())
    }
    fn end(self) -> SerResult<Value> {
        Ok(self.finish())
    }
}
impl serde::ser::SerializeTupleStruct for Compound {
    type Ok = Value;
    type Error = SerializationError;
    fn serialize_field<T: ?Sized + Serialize>(&mut self, v: &T) -> SerResult<()> {
        self.array.push(v.serialize(ExactSerializer)?);
        Ok(())
    }
    fn end(self) -> SerResult<Value> {
        Ok(self.finish())
    }
}
impl serde::ser::SerializeTupleVariant for Compound {
    type Ok = Value;
    type Error = SerializationError;
    fn serialize_field<T: ?Sized + Serialize>(&mut self, v: &T) -> SerResult<()> {
        self.array.push(v.serialize(ExactSerializer)?);
        Ok(())
    }
    fn end(self) -> SerResult<Value> {
        Ok(self.finish())
    }
}
impl serde::ser::SerializeMap for Compound {
    type Ok = Value;
    type Error = SerializationError;
    fn serialize_key<T: ?Sized + Serialize>(&mut self, k: &T) -> SerResult<()> {
        if self.key.is_some() {
            return Err(SerializationError);
        }
        let Value::String(key) = k.serialize(ExactSerializer)? else {
            return Err(SerializationError);
        };
        self.key = Some(key);
        Ok(())
    }
    fn serialize_value<T: ?Sized + Serialize>(&mut self, v: &T) -> SerResult<()> {
        let key = self.key.take().ok_or(SerializationError)?;
        if self
            .map
            .insert(key, v.serialize(ExactSerializer)?)
            .is_some()
        {
            return Err(SerializationError);
        }
        Ok(())
    }
    fn end(self) -> SerResult<Value> {
        if self.key.is_some() {
            return Err(SerializationError);
        }
        Ok(self.finish())
    }
}
impl serde::ser::SerializeStruct for Compound {
    type Ok = Value;
    type Error = SerializationError;
    fn serialize_field<T: ?Sized + Serialize>(&mut self, k: &'static str, v: &T) -> SerResult<()> {
        if self
            .map
            .insert(k.into(), v.serialize(ExactSerializer)?)
            .is_some()
        {
            return Err(SerializationError);
        }
        Ok(())
    }
    fn end(self) -> SerResult<Value> {
        Ok(self.finish())
    }
}
impl serde::ser::SerializeStructVariant for Compound {
    type Ok = Value;
    type Error = SerializationError;
    fn serialize_field<T: ?Sized + Serialize>(&mut self, k: &'static str, v: &T) -> SerResult<()> {
        if self
            .map
            .insert(k.into(), v.serialize(ExactSerializer)?)
            .is_some()
        {
            return Err(SerializationError);
        }
        Ok(())
    }
    fn end(self) -> SerResult<Value> {
        Ok(self.finish())
    }
}
