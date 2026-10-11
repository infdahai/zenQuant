use crate::{
    decimal::Dec,
    error::{Error, ErrorCode, Result},
};
use serde::{Deserialize, Serialize};
pub const REQUEST_SCHEMA: &str = "options-workbench-request.rust.v1";
pub const REPORT_SCHEMA: &str = "options-workbench-report.rust.v1";
pub const VIEW_SCHEMA: &str = "options-workbench-view.rust.v1";
pub const SCENARIOS_SCHEMA: &str = "options-workbench-scenarios.rust.v1";
pub const SPEC_SCHEMA: &str = "options-instrument.rust.v1";
pub const ARITHMETIC_METHOD: &str = "exact_decimal.rust.v1";
pub const MAX_REQUEST_BYTES: usize = 1048576;
pub const MAX_VIEWS: usize = 128;
pub const MAX_CANDIDATES: usize = 64;
pub const MAX_SCENARIOS: usize = 64;
pub const MAX_QUOTES: usize = 512;
pub const MAX_LEGS: usize = 16;
pub const MAX_LEVELS: usize = 128;
pub const MAX_JSON_DEPTH: usize = 64;
pub const MAX_STRING_BYTES: usize = 4096;
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Provenance {
    #[serde(rename = "fixture")]
    Fixture,
    #[serde(rename = "manual_unattested")]
    ManualUnattested,
    #[serde(rename = "sealed_proxy")]
    SealedProxy,
    #[serde(rename = "derived")]
    Derived,
    #[serde(rename = "observed")]
    Observed,
    #[serde(rename = "proxy")]
    Proxy,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Direction {
    #[serde(rename = "bullish")]
    Bullish,
    #[serde(rename = "bearish")]
    Bearish,
    #[serde(rename = "neutral")]
    Neutral,
    #[serde(rename = "volatility")]
    Volatility,
    #[serde(rename = "unspecified")]
    Unspecified,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ScenarioPurpose {
    #[serde(rename = "forecast")]
    Forecast,
    #[serde(rename = "stress")]
    Stress,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ProbabilitySource {
    #[serde(rename = "subjective")]
    Subjective,
    #[serde(rename = "risk_neutral")]
    RiskNeutral,
    #[serde(rename = "empirical")]
    Empirical,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    #[serde(rename = "BUY")]
    Buy,
    #[serde(rename = "SELL")]
    Sell,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Sleeve {
    #[serde(rename = "directional")]
    Directional,
    #[serde(rename = "volatility")]
    Volatility,
    #[serde(rename = "tail")]
    Tail,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Status {
    #[serde(rename = "VALID")]
    Valid,
    #[serde(rename = "REJECTED")]
    Rejected,
    #[serde(rename = "NOT_EVALUABLE")]
    NotEvaluable,
    #[serde(rename = "UNSUPPORTED")]
    Unsupported,
    #[serde(rename = "INVALID")]
    Invalid,
    #[serde(rename = "UNVERIFIED")]
    Unverified,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Product {
    #[serde(rename = "linear_option")]
    LinearOption,
    #[serde(rename = "inverse_option")]
    InverseOption,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OptionType {
    #[serde(rename = "C")]
    C,
    #[serde(rename = "P")]
    P,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ExerciseStyle {
    #[serde(rename = "EUROPEAN")]
    European,
    #[serde(rename = "AMERICAN")]
    American,
    #[serde(rename = "UNKNOWN")]
    Unknown,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum QuantityUnit {
    #[serde(rename = "base")]
    Base,
    #[serde(rename = "contracts")]
    Contracts,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PriceUnit {
    #[serde(rename = "quote_per_base")]
    QuotePerBase,
    #[serde(rename = "quote_per_contract")]
    QuotePerContract,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SettlementRule {
    #[serde(rename = "linear_cash")]
    LinearCash,
    #[serde(rename = "linear_via_future")]
    LinearViaFuture,
    #[serde(rename = "inverse_via_future")]
    InverseViaFuture,
    #[serde(rename = "UNKNOWN")]
    Unknown,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecordMeta {
    pub object_id: String,
    pub revision: u32,
    pub parent_hash: Option<String>,
    pub schema_version: String,
    pub method_version: String,
    pub received_ns: u64,
    pub available_ns: u64,
    pub cutoff_ns: u64,
    pub source_ref: String,
    pub source_sha256: Option<String>,
    pub provenance: Provenance,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct View {
    pub meta: RecordMeta,
    pub underlying: String,
    pub mechanism: String,
    pub direction: Direction,
    pub horizon_ns: u64,
    pub report_currency: String,
    pub initial_capital: Dec,
    pub max_loss_budget: Dec,
    pub competing_explanations: Vec<String>,
    pub invalidation: Vec<String>,
    pub no_trade_conditions: Vec<String>,
    pub clock_policy: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScenarioPoint {
    pub at_ns: u64,
    pub delivery_by_spec_hash: Vec<(String, Dec)>,
    pub iv_shift: Option<Dec>,
    pub liquidity_haircut: Option<Dec>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Scenario {
    pub scenario_id: String,
    pub points: Vec<ScenarioPoint>,
    pub probability: Option<Dec>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScenarioSet {
    pub meta: RecordMeta,
    pub view_hash: String,
    pub purpose: ScenarioPurpose,
    pub probability_source: Option<ProbabilitySource>,
    pub scenarios: Vec<Scenario>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstrumentSpec {
    pub venue: String,
    pub instrument_id: String,
    pub product: Product,
    pub underlying: String,
    pub quote_currency: String,
    pub strike_currency: String,
    pub settlement_currency: String,
    pub margin_currency: String,
    pub expiry_ns: u64,
    pub strike: Dec,
    pub option_type: OptionType,
    pub exercise_style: ExerciseStyle,
    pub quantity_unit: QuantityUnit,
    pub multiplier: Dec,
    pub price_unit: PriceUnit,
    pub min_quantity_native: Dec,
    pub quantity_step_native: Dec,
    pub max_quantity_native: Option<Dec>,
    pub tick_bands: Vec<(Dec, Dec)>,
    pub settlement_index: String,
    pub settlement_window_ns: u64,
    pub settlement_rule: SettlementRule,
    pub rule_version: String,
    pub metadata_hash: String,
    pub valid_from_ns: u64,
    pub valid_until_ns: Option<u64>,
    pub provenance: Provenance,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstrumentDescriptor {
    pub venue: String,
    pub product: Product,
    pub instrument_id: String,
    pub underlying: String,
    pub rule_version: String,
    pub spec_schema: String,
    pub spec_hash: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CandidateLeg {
    pub instrument: InstrumentDescriptor,
    pub side: Side,
    pub quantity_native: Dec,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CandidateRequest {
    pub request_id: String,
    pub template_id: String,
    pub template_version: String,
    pub legs: Vec<CandidateLeg>,
    pub exit_policy: String,
    pub terminal_fee_native: Option<Dec>,
    pub sleeve: Sleeve,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Observation {
    pub value: Dec,
    pub source_ns: u64,
    pub received_ns: u64,
    pub available_ns: u64,
    pub source_id: String,
    pub provenance: Provenance,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BookLevel {
    pub price: Dec,
    pub quantity_native: Dec,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Book {
    pub spec: InstrumentSpec,
    pub bids: Vec<BookLevel>,
    pub asks: Vec<BookLevel>,
    pub source_ns: u64,
    pub received_ns: u64,
    pub available_ns: u64,
    pub raw_hash: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FeePolicy {
    pub rate: Dec,
    pub premium_cap: Dec,
    pub fee_currency: String,
    pub provenance: Provenance,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TimingPolicy {
    pub max_age_ns: u64,
    pub minimum_source_age_ns: i64,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QuoteInput {
    pub quote_id: String,
    pub book: Book,
    pub index_in_quote: Option<Observation>,
    pub index_currency: Option<String>,
    pub quote_to_report: Option<Observation>,
    pub fx_pair: Option<(String, String)>,
    pub fee_policy: Option<FeePolicy>,
    pub fee_known_ns: Option<u64>,
    pub metadata_known_ns: Option<u64>,
    pub timing: TimingPolicy,
    pub source_identities: Vec<(String, String)>,
    pub assumptions: Vec<String>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WalletKey {
    pub venue: String,
    pub account: String,
    pub account_mode: String,
    pub currency: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WalletBalance {
    pub wallet: WalletKey,
    pub cash: Dec,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RiskPolicy {
    pub per_intent_report: Dec,
    pub active_and_pending_report: Dec,
    pub rolling_tail_paid_report: Dec,
    pub rolling_window_ns: u64,
    pub report_currency: String,
    pub fx_timing: Option<TimingPolicy>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub schema_version: String,
    pub cutoff_ns: u64,
    pub view_id: String,
    pub views: Vec<View>,
    pub scenarios: ScenarioSet,
    pub candidates: Vec<CandidateRequest>,
    pub quotes: Vec<QuoteInput>,
    pub hypothetical_balances: Vec<WalletBalance>,
    pub risk_policy: RiskPolicy,
    pub source_identities: Vec<(String, String)>,
    pub fixture_name: Option<String>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalysisInput {
    pub schema_version: String,
    pub adapter_version: String,
    pub cutoff_ns: u64,
    pub view: View,
    pub view_history: Vec<View>,
    pub scenarios: ScenarioSet,
    pub requests: Vec<CandidateRequest>,
    pub quotes: Vec<QuoteInput>,
    pub hypothetical_balances: Vec<WalletBalance>,
    pub risk_policy: RiskPolicy,
    pub source_identities: Vec<(String, String)>,
    pub gaps: Vec<StatusResult>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StatusResult {
    pub status: Status,
    pub reason: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SupportColumns {
    pub input_validity: StatusResult,
    pub model_support: StatusResult,
    pub modeled_feasibility: StatusResult,
    pub account_eligibility: StatusResult,
    pub execution_evidence: StatusResult,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CostEvidence {
    pub status: StatusResult,
    pub instrument_hash: String,
    pub book_hash: String,
    pub side: Side,
    pub quote_currency: String,
    pub report_currency: String,
    pub asof_ns: u64,
    pub quantity_native: Dec,
    pub premium_native: Option<Dec>,
    pub fee_native: Option<Dec>,
    pub total_report_currency: Option<Dec>,
    pub assumptions: Vec<String>,
    pub input_hash: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScenarioOutcome {
    pub scenario_id: String,
    pub currency: String,
    pub gross_payoff: Option<Dec>,
    pub conditional_model_pnl: Option<Dec>,
    pub reason: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModeledCandidate {
    pub request: CandidateRequest,
    pub view_hash: String,
    pub scenario_hash: String,
    pub report_currency: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModeledFunding {
    pub status: StatusResult,
    pub required_cash_by_wallet: Vec<(WalletKey, Dec)>,
    pub policy_hash: String,
    pub reasons: Vec<String>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExpressionAnalysis {
    pub request_id: String,
    pub request_hash: String,
    pub input_hash: String,
    pub model_version: String,
    pub support: SupportColumns,
    pub modeled_candidate: Option<ModeledCandidate>,
    pub costs: Vec<CostEvidence>,
    pub model_funding: Option<ModeledFunding>,
    pub outcomes: Vec<ScenarioOutcome>,
    pub conditional_ev: Option<Dec>,
    pub break_even_levels: Option<Vec<Dec>>,
    pub conditional_model_loss_bound: Option<Dec>,
    pub reasons: Vec<String>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConstraintCertificate {
    pub request_id: String,
    pub input_hash: String,
    pub method_version: String,
    pub status: StatusResult,
    pub spec_hashes: Vec<String>,
    pub currency: Option<String>,
    pub knots: Vec<Dec>,
    pub values_at_knots: Vec<Dec>,
    pub segment_slopes: Vec<Dec>,
    pub gross_payoff_lower: Option<Dec>,
    pub gross_payoff_upper: Option<Dec>,
    pub opening_net_spend: Option<Dec>,
    pub conditional_model_lower: Option<Dec>,
    pub conditional_model_upper: Option<Dec>,
    pub reasons: Vec<String>,
}

pub fn valid_sha256(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
impl RecordMeta {
    pub fn validate(&self, schema: &str) -> Result<()> {
        if self.received_ns > self.available_ns || self.available_ns > self.cutoff_ns {
            return Err(Error::new(ErrorCode::InvalidTimestamp));
        }
        if self.schema_version != schema || self.method_version != ARITHMETIC_METHOD {
            return Err(Error::new(ErrorCode::InvalidSchema));
        }
        if self.revision == 0
            || (self.revision == 1) != self.parent_hash.is_none()
            || self.object_id.trim().is_empty()
            || self.source_ref.trim().is_empty()
        {
            return Err(Error::new(ErrorCode::InvalidMetadata));
        }
        if self.parent_hash.as_ref().is_some_and(|s| !valid_sha256(s))
            || self
                .source_sha256
                .as_ref()
                .is_some_and(|s| !valid_sha256(s))
            || (self.provenance == Provenance::SealedProxy && self.source_sha256.is_none())
        {
            return Err(Error::new(ErrorCode::InvalidIdentity));
        }
        Ok(())
    }
}
impl View {
    pub fn validate(&self) -> Result<()> {
        self.meta.validate(VIEW_SCHEMA)?;
        if self.horizon_ns <= self.meta.cutoff_ns {
            return Err(Error::new(ErrorCode::InvalidTimestamp));
        }
        if self.initial_capital.is_negative()
            || self.max_loss_budget.is_negative()
            || self.max_loss_budget > self.initial_capital
        {
            return Err(Error::new(ErrorCode::InvalidInput));
        }
        Ok(())
    }
}
impl Request {
    pub fn validate_limits(&self) -> Result<()> {
        if self.views.len() > MAX_VIEWS
            || self.candidates.len() > MAX_CANDIDATES
            || self.scenarios.scenarios.len() > MAX_SCENARIOS
            || self.quotes.len() > MAX_QUOTES
            || self.candidates.iter().any(|c| c.legs.len() > MAX_LEGS)
            || self
                .quotes
                .iter()
                .any(|q| q.book.asks.len() > MAX_LEVELS || q.book.bids.len() > MAX_LEVELS)
        {
            return Err(Error::new(ErrorCode::LimitExceeded));
        }
        Ok(())
    }
}
impl InstrumentDescriptor {
    pub fn from_spec(spec: &InstrumentSpec) -> Result<Self> {
        Ok(Self {
            venue: spec.venue.clone(),
            product: spec.product,
            instrument_id: spec.instrument_id.clone(),
            underlying: spec.underlying.clone(),
            rule_version: spec.rule_version.clone(),
            spec_schema: SPEC_SCHEMA.into(),
            spec_hash: crate::canonical::canonical_hash(spec)?,
        })
    }
}
impl StatusResult {
    pub fn new(status: Status, reason: impl Into<String>) -> Self {
        Self {
            status,
            reason: reason.into(),
        }
    }
}

/// Integrity is not authenticity: exported semantic projections remain unattested.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RawSourceIntegrity {
    #[serde(rename = "RAW_PINNED_BYTES_VERIFIED")]
    RawPinnedBytesVerified,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ProjectionTrust {
    #[serde(rename = "EXPORTED_PROJECTION_UNATTESTED")]
    ExportedProjectionUnattested,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PinnedSource {
    pub path: String,
    pub size_bytes: u64,
    pub sha256: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PanelEnvelope {
    pub schema_version: String,
    pub exporter_version: String,
    pub raw_source_integrity: RawSourceIntegrity,
    pub projection_trust: ProjectionTrust,
    pub pinned_sources: Vec<PinnedSource>,
    pub projection_sha256: String,
    pub quotes: Vec<QuoteInput>,
    pub gaps: Vec<StatusResult>,
}
