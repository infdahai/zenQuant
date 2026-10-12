//! Manual paper declarations, never exchange collateral, reservations or orders.
//! A bounded journal is the sole authority for allocation and intent-record state.
use crate::{canonical::canonical_hash, decimal::Dec};
use serde::{Deserialize, Serialize};

pub const SCHEMA: &str = "manual-paper-command.rust.v1";
pub const STORE_SCHEMA: &str = "manual-paper-journal.rust.v1";
pub const PREVIEW_SCHEMA: &str = "manual-paper-preview.rust.v1";
pub const MAX_EVENTS: usize = 256;
pub const PREVIEW_TTL_MS: u64 = 300_000;
type Result<T> = std::result::Result<T, Failure>;
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Failure {
    InvalidInput,
    OverAllocated,
    ConversionUnconfirmed,
    RiskSettingsMissing,
    SingleIntentLimit,
    NotConfigured,
    Conflict,
    StalePreview,
    InvalidPreview,
    MissingIntent,
    AlreadyWithdrawn,
    Limit,
    Corrupt,
    ClockRegression,
}
fn check(ok: bool, e: Failure) -> Result<()> {
    if ok { Ok(()) } else { Err(e) }
}
fn hash<T: Serialize>(v: &T) -> Result<String> {
    canonical_hash(v).map_err(|_| Failure::InvalidInput)
}
fn id(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 80
        && s.bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_-.:".contains(&c))
}
fn sha(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn total() -> Dec {
    Dec::parse("100000").expect("fixed decimal")
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Conversion {
    pub schema_version: String,
    pub assumption: Parity,
    pub confirmed: bool,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Parity {
    #[serde(rename = "EXPERIMENTAL_USD_STABLECOIN_1_TO_1")]
    Experimental,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Allocation {
    pub allocation_usd: Dec,
    pub single_intent_cap_usd: Dec,
    pub conversion: Option<Conversion>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum MarginMode {
    #[serde(rename = "ISOLATED")]
    Isolated,
    #[serde(rename = "CROSS")]
    Cross,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PerpetualAccount {
    pub allocation: Allocation,
    pub margin_mode: Option<MarginMode>,
    pub max_leverage: Option<Dec>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    pub schema_version: String,
    pub total_budget_usd: Dec,
    pub deribit_usdc: Option<Allocation>,
    pub binance_usdt: Option<PerpetualAccount>,
}
impl Settings {
    pub fn validate(&self) -> Result<Dec> {
        check(
            self.schema_version == "manual-paper-settings.rust.v1"
                && self.total_budget_usd == total(),
            Failure::InvalidInput,
        )?;
        let mut allocated = Dec::zero();
        for a in self
            .deribit_usdc
            .iter()
            .chain(self.binance_usdt.iter().map(|a| &a.allocation))
        {
            check(
                a.allocation_usd > Dec::zero()
                    && a.single_intent_cap_usd > Dec::zero()
                    && a.single_intent_cap_usd <= a.allocation_usd,
                Failure::InvalidInput,
            )?;
            check(
                a.conversion.as_ref().is_some_and(|c| {
                    c.schema_version == "paper-fx-assumption.rust.v1" && c.confirmed
                }),
                Failure::ConversionUnconfirmed,
            )?;
            allocated = allocated
                .checked_add(&a.allocation_usd)
                .map_err(|_| Failure::InvalidInput)?;
        }
        if let Some(b) = &self.binance_usdt {
            check(
                b.margin_mode.is_some()
                    && b.max_leverage.as_ref().is_some_and(|l| {
                        *l >= Dec::one() && *l <= Dec::parse("125").expect("fixed decimal")
                    }),
                Failure::RiskSettingsMissing,
            )?;
        }
        check(allocated <= total(), Failure::OverAllocated)?;
        total()
            .checked_sub(&allocated)
            .map_err(|_| Failure::InvalidInput)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EvidenceKind {
    #[serde(rename = "USER_SUPPLIED_UNVERIFIED")]
    UserSupplied,
    #[serde(rename = "SYNTHETIC_ENGINEERING_ONLY")]
    Synthetic,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvidenceRef {
    pub sha256: String,
    pub source: String,
    pub kind: EvidenceKind,
    pub observed_ms: u64,
    pub available_ms: u64,
    pub valid_until_ms: u64,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct Evidence {
    pub specification: Option<EvidenceRef>,
    pub quote: Option<EvidenceRef>,
    pub fees: Option<EvidenceRef>,
    pub mark: Option<EvidenceRef>,
    pub funding: Option<EvidenceRef>,
    pub margin: Option<EvidenceRef>,
}
impl Evidence {
    fn validate(&self, now: u64) -> Result<()> {
        for r in [
            &self.specification,
            &self.quote,
            &self.fees,
            &self.mark,
            &self.funding,
            &self.margin,
        ]
        .into_iter()
        .flatten()
        {
            check(
                sha(&r.sha256)
                    && r.valid_until_ms <= 8_640_000_000_000_000
                    && !r.source.is_empty()
                    && r.source.len() <= 256
                    && r.observed_ms <= r.available_ms
                    && r.available_ms <= now
                    && r.valid_until_ms > r.available_ms,
                Failure::InvalidInput,
            )?;
        }
        Ok(())
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    #[serde(rename = "BUY")]
    Buy,
    #[serde(rename = "SELL")]
    Sell,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum CallPut {
    #[serde(rename = "C")]
    Call,
    #[serde(rename = "P")]
    Put,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OptionIntent {
    pub schema_version: String,
    pub symbol: String,
    pub underlying: String,
    /// User-declared contract identity. No assertion that the venue lists it.
    pub expiry_ms: u64,
    pub option_type: CallPut,
    pub strike_usdc: Dec,
    pub quantity_contracts: Dec,
    pub limit_usdc_per_base: Dec,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PerpetualIntent {
    pub schema_version: String,
    pub symbol: String,
    pub side: Side,
    pub quantity_btc: Dec,
    pub limit_usdt_per_btc: Dec,
    pub margin_mode: MarginMode,
    pub leverage: Dec,
    pub reduce_only: bool,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "product", content = "details", deny_unknown_fields)]
pub enum Product {
    #[serde(rename = "DERIBIT_LINEAR_USDC_OPTION_BUY")]
    Option(OptionIntent),
    #[serde(rename = "BINANCE_USDM_BTCUSDT_PERPETUAL")]
    Perpetual(PerpetualIntent),
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Manual {
    #[serde(rename = "MANUAL")]
    Manual,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Intent {
    pub intent_id: String,
    pub source: Manual,
    pub requested_budget_usd: Dec,
    pub instrument: Product,
    pub evidence: Evidence,
}
impl Intent {
    fn validate(&self, settings: &Settings, now: u64) -> Result<()> {
        check(
            id(&self.intent_id) && self.requested_budget_usd > Dec::zero(),
            Failure::InvalidInput,
        )?;
        self.evidence.validate(now)?;
        let allocation = match &self.instrument {
            Product::Option(o) => {
                check(
                    o.schema_version == "manual-deribit-option.rust.v1"
                        && o.expiry_ms <= 8_640_000_000_000_000
                        && matches!(o.underlying.as_str(), "BTC" | "ETH")
                        && o.expiry_ms > now
                        && o.strike_usdc > Dec::zero()
                        && o.quantity_contracts > Dec::zero()
                        && o.limit_usdc_per_base > Dec::zero(),
                    Failure::InvalidInput,
                )?;
                let parts: Vec<_> = o.symbol.split('-').collect();
                check(
                    parts.len() == 4
                        && parts[0] == format!("{}_USDC", o.underlying)
                        && id(parts[1])
                        && Dec::parse(parts[2]).ok().as_ref() == Some(&o.strike_usdc)
                        && parts[3]
                            == match o.option_type {
                                CallPut::Call => "C",
                                CallPut::Put => "P",
                            },
                    Failure::InvalidInput,
                )?;
                settings
                    .deribit_usdc
                    .as_ref()
                    .ok_or(Failure::NotConfigured)?
            }
            Product::Perpetual(p) => {
                let b = settings
                    .binance_usdt
                    .as_ref()
                    .ok_or(Failure::NotConfigured)?;
                check(
                    p.schema_version == "manual-binance-usdm-perpetual.rust.v1"
                        && p.symbol == "BTCUSDT"
                        && p.quantity_btc > Dec::zero()
                        && p.limit_usdt_per_btc > Dec::zero(),
                    Failure::InvalidInput,
                )?;
                check(
                    b.margin_mode.as_ref() == Some(&p.margin_mode)
                        && p.leverage >= Dec::one()
                        && b.max_leverage
                            .as_ref()
                            .is_some_and(|max| p.leverage <= *max),
                    Failure::RiskSettingsMissing,
                )?;
                &b.allocation
            }
        };
        check(
            self.requested_budget_usd <= allocation.single_intent_cap_usd,
            Failure::SingleIntentLimit,
        )
    }
    pub fn blockers(&self, now: u64) -> Vec<String> {
        let mut b = vec![
            "NO_ORDER_OR_FILL_ENGINE".into(),
            "ACCOUNT_RISK_UNVERIFIED".into(),
            "INTENT_BUDGET_IS_NOT_A_RESERVATION".into(),
        ];
        let mut fields = vec![
            ("SPECIFICATION", &self.evidence.specification),
            ("QUOTE", &self.evidence.quote),
            ("FEES", &self.evidence.fees),
        ];
        if matches!(self.instrument, Product::Perpetual(_)) {
            fields.extend([
                ("MARK", &self.evidence.mark),
                ("FUNDING", &self.evidence.funding),
                ("MARGIN", &self.evidence.margin),
            ]);
        } else {
            b.push("OPTION_CONTRACT_IDENTITY_AND_SETTLEMENT_UNVERIFIED".into());
        }
        for (name, r) in fields {
            b.push(format!(
                "{name}_{}",
                match r {
                    None => "MISSING",
                    Some(e) if now >= e.valid_until_ms => "STALE",
                    Some(_) => "UNVERIFIED",
                }
            ));
        }
        b
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Withdrawal {
    pub intent_id: String,
    pub intent_sha256: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", content = "value", deny_unknown_fields)]
pub enum Action {
    #[serde(rename = "CONFIGURE")]
    Configure(Box<Settings>),
    #[serde(rename = "RECORD_INTENT")]
    Record(Box<Intent>),
    #[serde(rename = "WITHDRAW_INTENT")]
    Withdraw(Withdrawal),
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Command {
    pub schema_version: String,
    pub request_id: String,
    pub action: Action,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Preview {
    pub schema_version: String,
    pub command: Command,
    pub base_sha256: String,
    pub created_ms: u64,
    pub expires_ms: u64,
    pub sha256: String,
}
impl Preview {
    fn digest(&self) -> Result<String> {
        hash(&(
            PREVIEW_SCHEMA,
            &self.command,
            &self.base_sha256,
            self.created_ms,
            self.expires_ms,
        ))
    }
    fn validate(&self) -> Result<()> {
        check(
            self.schema_version == PREVIEW_SCHEMA
                && sha(&self.base_sha256)
                && self.created_ms.checked_add(PREVIEW_TTL_MS) == Some(self.expires_ms)
                && self.digest()? == self.sha256,
            Failure::InvalidPreview,
        )
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Confirmation {
    pub preview: Preview,
    pub acknowledged_record_only: bool,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    preview: Preview,
    confirmed_ms: u64,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Journal {
    schema_version: String,
    entries: Vec<Entry>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Recorded {
    pub record_request_id: String,
    pub intent: Intent,
    pub intent_sha256: String,
    pub settings_sha256: String,
    pub status: &'static str,
    pub recorded_ms: u64,
    pub withdrawn_ms: Option<u64>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct View {
    pub settings: Option<Settings>,
    pub unallocated_usd: Dec,
    pub intents: Vec<Recorded>,
    pub revision: usize,
    pub sha256: String,
    pub total_budget_usd: Dec,
    pub execution_authority: &'static str,
    pub scope: &'static str,
}
impl Default for Journal {
    fn default() -> Self {
        Self {
            schema_version: STORE_SCHEMA.into(),
            entries: vec![],
        }
    }
}
impl Journal {
    pub fn view(&self) -> Result<View> {
        self.validate()?;
        self.materialize()
    }
    fn materialize(&self) -> Result<View> {
        let mut v = View {
            settings: None,
            unallocated_usd: total(),
            intents: vec![],
            revision: self.entries.len(),
            sha256: hash(self)?,
            total_budget_usd: total(),
            execution_authority: "NONE",
            scope: "SIMULATED_CAPITAL_ALLOCATION_AND_MANUAL_RECORDS_ONLY",
        };
        for e in &self.entries {
            apply(&mut v, &e.preview.command, e.confirmed_ms)?;
        }
        Ok(v)
    }
    pub fn validate(&self) -> Result<()> {
        check(
            self.schema_version == STORE_SCHEMA && self.entries.len() <= MAX_EVENTS,
            Failure::Corrupt,
        )?;
        let mut prior = Self::default();
        for e in &self.entries {
            let (next, replay) = prior
                .confirm_unchecked(
                    &Confirmation {
                        preview: e.preview.clone(),
                        acknowledged_record_only: true,
                    },
                    e.confirmed_ms,
                )
                .map_err(|_| Failure::Corrupt)?;
            check(!replay, Failure::Corrupt)?;
            prior = next;
        }
        Ok(())
    }
    pub fn preview(&self, command: Command, now: u64) -> Result<Preview> {
        self.validate()?;
        check(
            command.schema_version == SCHEMA && id(&command.request_id),
            Failure::InvalidInput,
        )?;
        // Original request IDs return the original preview, including after withdrawal.
        if let Some(e) = self
            .entries
            .iter()
            .find(|e| e.preview.command.request_id == command.request_id)
        {
            check(e.preview.command == command, Failure::Conflict)?;
            return Ok(e.preview.clone());
        }
        let mut v = self.materialize()?;
        apply(&mut v, &command, now)?;
        let mut p = Preview {
            schema_version: PREVIEW_SCHEMA.into(),
            command,
            base_sha256: hash(self)?,
            created_ms: now,
            expires_ms: now
                .checked_add(PREVIEW_TTL_MS)
                .ok_or(Failure::InvalidInput)?,
            sha256: String::new(),
        };
        p.sha256 = p.digest()?;
        Ok(p)
    }
    pub fn confirm(&self, c: &Confirmation, now: u64) -> Result<(Self, bool)> {
        self.validate()?;
        self.confirm_unchecked(c, now)
    }
    fn confirm_unchecked(&self, c: &Confirmation, now: u64) -> Result<(Self, bool)> {
        check(c.acknowledged_record_only, Failure::InvalidInput)?;
        let p = &c.preview;
        p.validate()?;
        check(
            p.command.schema_version == SCHEMA && id(&p.command.request_id),
            Failure::InvalidInput,
        )?;
        if let Some(e) = self
            .entries
            .iter()
            .find(|e| e.preview.command.request_id == p.command.request_id)
        {
            // Preview timestamps may differ across retries/tabs; identity belongs to
            // the complete command, including every parameter and evidence reference.
            check(e.preview.command == p.command, Failure::Conflict)?;
            return Ok((self.clone(), true));
        }
        check(self.entries.len() < MAX_EVENTS, Failure::Limit)?;
        check(
            self.entries.last().is_none_or(|e| now >= e.confirmed_ms),
            Failure::ClockRegression,
        )?;
        check(
            now >= p.created_ms && now < p.expires_ms && p.base_sha256 == hash(self)?,
            Failure::StalePreview,
        )?;
        apply(&mut self.materialize()?, &p.command, now)?;
        let mut next = self.clone();
        next.entries.push(Entry {
            preview: p.clone(),
            confirmed_ms: now,
        });
        Ok((next, false))
    }
}
fn apply(v: &mut View, command: &Command, now: u64) -> Result<()> {
    match &command.action {
        Action::Configure(s) => {
            v.unallocated_usd = s.validate()?;
            v.settings = Some(s.as_ref().clone());
        }
        Action::Record(i) => {
            let s = v.settings.as_ref().ok_or(Failure::NotConfigured)?;
            i.validate(s, now)?;
            check(
                !v.intents.iter().any(|r| r.intent.intent_id == i.intent_id),
                Failure::Conflict,
            )?;
            v.intents.push(Recorded {
                record_request_id: command.request_id.clone(),
                intent: i.as_ref().clone(),
                intent_sha256: hash(i)?,
                settings_sha256: hash(s)?,
                status: "RECORDED_UNASSESSED",
                recorded_ms: now,
                withdrawn_ms: None,
            });
        }
        Action::Withdraw(w) => {
            let r = v
                .intents
                .iter_mut()
                .find(|r| r.intent.intent_id == w.intent_id)
                .ok_or(Failure::MissingIntent)?;
            check(r.intent_sha256 == w.intent_sha256, Failure::Conflict)?;
            check(r.withdrawn_ms.is_none(), Failure::AlreadyWithdrawn)?;
            r.withdrawn_ms = Some(now);
            r.status = "WITHDRAWN_RECORD_ONLY";
        }
    }
    Ok(())
}
