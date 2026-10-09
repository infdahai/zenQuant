# Task 1 report — native Rust contracts

Status: DONE with explicit downstream limitations. No Python/P1 or historical artifacts changed; no merge, push, trading network call or deployment.

## Delivered

Two-crate Rust2024 workspace under `rust/`; committed Cargo.lock. Core `#![forbid(unsafe_code)]`, six requested direct dependencies only. Workbench is library-only and reexports core; no executable or Python runtime bridge.

`Dec` stores private normalized BigRational, never float. Input syntax excludes plus, exponents, whitespace, trailing dot and nonfinite text; `.01` and `-.01` accepted, zero canonical `0`. Input bounds64 significant/28 fractional; raw length guard before bigint construction. Normalized numerator and denominator independently bounded1024 decimal digits. Operands are bounded before operations, so temporary rational products are bounded to2048 digits before normalization; no unbounded external bigint constructor exposed. Add/sub/mul/div/neg return Result and never round; finite decimal output at most256 fractional places. `1/8=0.125`; `1/3` yields NUMERICAL_LIMITATION. Division zero is DIVISION_BY_ZERO. Result outside digit domain is PRECISION_LIMIT.

Canonical bytes recursively sort object keys, preserve ordered arrays and canonical Dec strings, use compact serde JSON; reject float numbers, >4096-byte strings/keys and depth>64. Canonical hash SHA256 prefix is UTF8 `exact_decimal.rust.v1` followed by NUL then canonical bytes. Source-byte hashes separately use `bytes_sha256`; neither claims Python fingerprint equivalence.

Structs have explicit typed fields, serde deny_unknown_fields, Clone/Eq and no interior mutability. Public struct fields permit ownership-based construction/updates; validation occurs explicitly, not automatically on every native struct mutation. Timestamp u64 rejects negative/boolean/float transport. RecordMeta::validate verifies clock ordering, revision/parent shape, source/hash shape and expected schema/method; View::validate verifies horizon and budgets. Request::validate_limits enforces collection/leg/book limits. Full causal/revision/strict JSON admission remains Task2.

## Frozen interfaces

Modules: `decimal`, `error`, `canonical`, `contracts`, `fixtures`. Decimal: `Dec::parse(&str)->Result<Dec>`, `zero`, `one`, `is_negative`, `is_zero`, `checked_add/sub/mul/div(&Dec)->Result<Dec>`, `checked_neg()->Result<Dec>`, `abs()->Dec`, `is_multiple_of(&Dec)->bool`, `canonical_string()->Result<String>`; FromStr/Display/serde strings. `Dec::parse` and deserialize accept input domain64/28; larger computed outputs serialize but are deliberately outside request re-admission domain. Reports are outputs, not automatic request roundtrips.

Hashes: `canonical_bytes<T:Serialize>(&T)->Result<Vec<u8>>`, `canonical_hash<T:Serialize>(&T)->Result<String>`, `bytes_sha256(&[u8])->String`. `InstrumentDescriptor::from_spec(&InstrumentSpec)->Result<InstrumentDescriptor>` binds all spec fields including venue and rule. `valid_sha256(&str)->bool`. `StatusResult::new(Status,impl Into<String>)`.

Error: `Error { code: ErrorCode }`, alias `Result<T>`, fixed safe codes InvalidDecimal/PrecisionLimit/NumericalLimitation/DivisionByZero/InvalidTimestamp/InvalidMetadata/InvalidSchema/InvalidIdentity/InvalidInput/LimitExceeded/DuplicateKey/InvalidJson/ConflictingEvidence/MissingEvidence/Unsupported/Internal. Error Display never echoes input.

Schemas: REQUEST_SCHEMA=`options-workbench-request.rust.v1`, REPORT_SCHEMA=`options-workbench-report.rust.v1`, VIEW_SCHEMA=`options-workbench-view.rust.v1`, SCENARIOS_SCHEMA=`options-workbench-scenarios.rust.v1`, SPEC_SCHEMA=`options-instrument.rust.v1`, ARITHMETIC_METHOD=`exact_decimal.rust.v1`. Version constants are in contracts.rs. All enum serialized spelling is explicit and case-sensitive.

Request fields must be named exactly as below; optional fields decode missing as None, serialize as null. Collections are arrays; `(String,Dec)`, `(WalletKey,Dec)`, FX pairs and source identities are JSON arrays of two elements. Dec always JSON string. Fixture_name optional explicit marker; no financial defaults. Candidate unknown template is retained in String, never downgraded by contracts.

- `RecordMeta`: `object_id: String`; `revision: u32`; `parent_hash: Option<String>`; `schema_version: String`; `method_version: String`; `received_ns: u64`; `available_ns: u64`; `cutoff_ns: u64`; `source_ref: String`; `source_sha256: Option<String>`; `provenance: Provenance`.
- `View`: `meta: RecordMeta`; `underlying: String`; `mechanism: String`; `direction: Direction`; `horizon_ns: u64`; `report_currency: String`; `initial_capital: Dec`; `max_loss_budget: Dec`; `competing_explanations: Vec<String>`; `invalidation: Vec<String>`; `no_trade_conditions: Vec<String>`; `clock_policy: String`.
- `ScenarioPoint`: `at_ns: u64`; `delivery_by_spec_hash: Vec<(String, Dec)>`; `iv_shift: Option<Dec>`; `liquidity_haircut: Option<Dec>`.
- `Scenario`: `scenario_id: String`; `points: Vec<ScenarioPoint>`; `probability: Option<Dec>`.
- `ScenarioSet`: `meta: RecordMeta`; `view_hash: String`; `purpose: ScenarioPurpose`; `probability_source: Option<ProbabilitySource>`; `scenarios: Vec<Scenario>`.
- `InstrumentSpec`: `venue: String`; `instrument_id: String`; `product: Product`; `underlying: String`; `quote_currency: String`; `strike_currency: String`; `settlement_currency: String`; `margin_currency: String`; `expiry_ns: u64`; `strike: Dec`; `option_type: OptionType`; `exercise_style: ExerciseStyle`; `quantity_unit: QuantityUnit`; `multiplier: Dec`; `price_unit: PriceUnit`; `min_quantity_native: Dec`; `quantity_step_native: Dec`; `max_quantity_native: Option<Dec>`; `tick_bands: Vec<(Dec, Dec)>`; `settlement_index: String`; `settlement_window_ns: u64`; `settlement_rule: SettlementRule`; `rule_version: String`; `metadata_hash: String`; `valid_from_ns: u64`; `valid_until_ns: Option<u64>`; `provenance: Provenance`.
- `InstrumentDescriptor`: `venue: String`; `product: Product`; `instrument_id: String`; `underlying: String`; `rule_version: String`; `spec_schema: String`; `spec_hash: String`.
- `CandidateLeg`: `instrument: InstrumentDescriptor`; `side: Side`; `quantity_native: Dec`.
- `CandidateRequest`: `request_id: String`; `template_id: String`; `template_version: String`; `legs: Vec<CandidateLeg>`; `exit_policy: String`; `terminal_fee_native: Option<Dec>`; `sleeve: Sleeve`.
- `Observation`: `value: Dec`; `source_ns: u64`; `received_ns: u64`; `available_ns: u64`; `source_id: String`; `provenance: Provenance`.
- `BookLevel`: `price: Dec`; `quantity_native: Dec`.
- `Book`: `spec: InstrumentSpec`; `bids: Vec<BookLevel>`; `asks: Vec<BookLevel>`; `source_ns: u64`; `received_ns: u64`; `available_ns: u64`; `raw_hash: String`.
- `FeePolicy`: `rate: Dec`; `premium_cap: Dec`; `fee_currency: String`; `provenance: Provenance`.
- `TimingPolicy`: `max_age_ns: u64`; `minimum_source_age_ns: i64`.
- `QuoteInput`: `quote_id: String`; `book: Book`; `index_in_quote: Option<Observation>`; `index_currency: Option<String>`; `quote_to_report: Option<Observation>`; `fx_pair: Option<(String, String)>`; `fee_policy: Option<FeePolicy>`; `fee_known_ns: Option<u64>`; `metadata_known_ns: Option<u64>`; `timing: TimingPolicy`; `source_identities: Vec<(String, String)>`; `assumptions: Vec<String>`.
- `WalletKey`: `venue: String`; `account: String`; `account_mode: String`; `currency: String`.
- `WalletBalance`: `wallet: WalletKey`; `cash: Dec`.
- `RiskPolicy`: `per_intent_report: Dec`; `active_and_pending_report: Dec`; `rolling_tail_paid_report: Dec`; `rolling_window_ns: u64`; `report_currency: String`; `fx_timing: Option<TimingPolicy>`.
- `Request`: `schema_version: String`; `cutoff_ns: u64`; `view_id: String`; `views: Vec<View>`; `scenarios: ScenarioSet`; `candidates: Vec<CandidateRequest>`; `quotes: Vec<QuoteInput>`; `hypothetical_balances: Vec<WalletBalance>`; `risk_policy: RiskPolicy`; `source_identities: Vec<(String, String)>`; `fixture_name: Option<String>`.
- `AnalysisInput`: `schema_version: String`; `adapter_version: String`; `cutoff_ns: u64`; `view: View`; `view_history: Vec<View>`; `scenarios: ScenarioSet`; `requests: Vec<CandidateRequest>`; `quotes: Vec<QuoteInput>`; `hypothetical_balances: Vec<WalletBalance>`; `risk_policy: RiskPolicy`; `source_identities: Vec<(String, String)>`; `gaps: Vec<StatusResult>`.
- `StatusResult`: `status: Status`; `reason: String`.
- `SupportColumns`: `input_validity: StatusResult`; `model_support: StatusResult`; `modeled_feasibility: StatusResult`; `account_eligibility: StatusResult`; `execution_evidence: StatusResult`.
- `CostEvidence`: `status: StatusResult`; `instrument_hash: String`; `book_hash: String`; `side: Side`; `quote_currency: String`; `report_currency: String`; `asof_ns: u64`; `quantity_native: Dec`; `premium_native: Option<Dec>`; `fee_native: Option<Dec>`; `total_report_currency: Option<Dec>`; `assumptions: Vec<String>`; `input_hash: String`.
- `ScenarioOutcome`: `scenario_id: String`; `currency: String`; `gross_payoff: Option<Dec>`; `conditional_model_pnl: Option<Dec>`; `reason: String`.
- `ModeledCandidate`: `request: CandidateRequest`; `view_hash: String`; `scenario_hash: String`; `report_currency: String`.
- `ModeledFunding`: `status: StatusResult`; `required_cash_by_wallet: Vec<(WalletKey, Dec)>`; `policy_hash: String`; `reasons: Vec<String>`.
- `ExpressionAnalysis`: `request_id: String`; `request_hash: String`; `input_hash: String`; `model_version: String`; `support: SupportColumns`; `modeled_candidate: Option<ModeledCandidate>`; `costs: Vec<CostEvidence>`; `model_funding: Option<ModeledFunding>`; `outcomes: Vec<ScenarioOutcome>`; `conditional_ev: Option<Dec>`; `break_even_levels: Option<Vec<Dec>>`; `conditional_model_loss_bound: Option<Dec>`; `reasons: Vec<String>`.
- `ConstraintCertificate`: `request_id: String`; `input_hash: String`; `method_version: String`; `status: StatusResult`; `spec_hashes: Vec<String>`; `currency: Option<String>`; `knots: Vec<Dec>`; `values_at_knots: Vec<Dec>`; `segment_slopes: Vec<Dec>`; `gross_payoff_lower: Option<Dec>`; `gross_payoff_upper: Option<Dec>`; `opening_net_spend: Option<Dec>`; `conditional_model_lower: Option<Dec>`; `conditional_model_upper: Option<Dec>`; `reasons: Vec<String>`.
- `PinnedSource`: `path: String`; `size_bytes: u64`; `sha256: String`.
- `PanelEnvelope`: `schema_version: String`; `exporter_version: String`; `raw_source_integrity: RawSourceIntegrity`; `projection_trust: ProjectionTrust`; `pinned_sources: Vec<PinnedSource>`; `projection_sha256: String`; `quotes: Vec<QuoteInput>`; `gaps: Vec<StatusResult>`.

Support Status values: VALID/REJECTED/NOT_EVALUABLE/UNSUPPORTED/INVALID/UNVERIFIED. Account/execution downstream must always UNVERIFIED; contracts confer no authority. ExpressionAnalysis uses modeled_candidate/ModeledFunding, no P1 intent/approval/ledger. Reporting Task4 must emit fixed diagnostic_only=true/rankable=false/live_eligible=false/execution_authority=NONE and null actual/paper PnL/Sharpe/capacity/lifecycle edge; none are accepted Request fields.

PanelEnvelope raw integrity RAW_PINNED_BYTES_VERIFIED is separate from projection trust EXPORTED_PROJECTION_UNATTESTED. Downstream adapter must force sealed_proxy/UNKNOWN even if supplied quotes claim fixture/observed. Core defines labels only; pinned paths/byte validation belongs CLI. Panel schema string to be allowlisted by Task2/exporter, not arbitrary trusted input.

Public mathematical fixtures: `mathematical_spec(OptionType, Dec)->InstrumentSpec`, `mathematical_meta(&str)->RecordMeta`, `mathematical_view(capital:Dec,loss_budget:Dec)->Result<View>`, `mathematical_quote(InstrumentSpec,ask:Dec,bid:Dec)->QuoteInput`, `mathematical_request(name:&str,capital:Dec,loss_budget:Dec,balances:Vec<WalletBalance>,risk_policy:RiskPolicy)->Result<Request>`. Names exactly terminal-eth-call/terminal-eth-straddle/terminal-vertical. Builder is explicit public fixture module, tagged MATHEMATICAL_FIXTURE_NOT_MARKET_DATA; caller must supply every capital/loss/wallet/policy amount. No calculations of expected model outcomes in fixtures. Subsequent integration tests can reuse these factories, never Python tests.

## Evidence

RED: initial decimal stubs compiled, then three tests FAILED on .01 canonicalization, 1/8 equality and exponent admission. Additional canonical float/length/depth test FAILED on 0.25 admission before canonical validator implementation. Contract timestamp/identity/collection/roundtrip cases are GREEN regressions; they were not individually observed RED before validator construction (process limitation recorded rather than claiming false evidence).

GREEN: `cargo test --manifest-path rust/Cargo.toml --locked`:11 integration tests passed; both crates/doc tests pass. `cargo fmt --manifest-path rust/Cargo.toml --all --check` and `cargo clippy --manifest-path rust/Cargo.toml --locked --all-targets -- -D warnings` pass. Covers high64-digit product, overflow, output fractional cap, nonterminating division, negative zero, input precision/exponent/nonfinite, venue/rule identity, timestamp/horizon/budget, parent shape, nested unknown fields, bool/integer/float-money rejection, canonical float/depth/string limits, complete request roundtrip and129-view rejection.

Dependency references reviewed: official num-rational BigRational documentation https://docs.rs/num-rational/latest/num_rational/type.BigRational.html; official serde container attribute docs https://serde.rs/attributes.html. cargo fetched crates only; no trading/network APIs. sha2 transitively includes cpufeatures/libc on this platform; core has no direct libc dependency or unsafe blocks.

## Remaining work and risks

Task2 must add strict recursive pre-map duplicate rejection, byte/depth/all string limits before admission, schema allowlist and validation of every nested economic field, complete revision chain/PIT/scenarios/spec/quote conflict checks. Plain serde alone is not the admission codec. Task3 owns costs/payoffs/budget constraints and must preserve arithmetic failures as NOT_EVALUABLE, never zero. Task4 owns report authority/null fields and publication. Task5 owns finite-domain Python oracle integration and full gates. No economics or account/execution acceptance claimed here.

Commits: c3f66fa workspace; 60f8f71 arithmetic; 9b7b484 contracts/canonical identity; 4161cc1 mathematical fixtures; 6c4b432 tests. All preexisting tracked files unchanged relative task base b6258bf; only new rust paths and this new report added.

## Review fix round 1

Generic canonical Serialize inputs now use a single-pass exact custom serializer into JSON Value; f32/f64 are rejected recursively before serde_json can turn NaN/Inf into null. No serde_json::to_value is called by canonical_bytes. Subsequent sorted/value validation remains unchanged; string-key maps only, duplicate serialized map/struct keys reject. Error remains fixed INVALID_INPUT. This is canonical serialization, not the later input decoder. Existing typed consumers and canonical request bytes remain compatible.

Focused RED: generic_serialization_rejects_nested_nonfinite_before_json_conversion failed its nested NaN assertion (serde_json had transformed it into null). GREEN: regression now rejects nested f64 NaN/+Inf/-Inf/0.25 and nested f32 NaN; all12 tests pass with locked Cargo. fmt and clippy warnings-as-errors pass. Frozen-field list corrected from actual contracts.rs to include every last field; no contract fields removed or changed.
