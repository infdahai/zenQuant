// Package options implements deterministic, evidence-bounded terminal option scenarios.
package options

type RecordMeta struct {
	ObjectId      string  `json:"object_id"`
	Revision      uint32  `json:"revision"`
	ParentHash    *string `json:"parent_hash"`
	SchemaVersion string  `json:"schema_version"`
	MethodVersion string  `json:"method_version"`
	ReceivedNs    uint64  `json:"received_ns"`
	AvailableNs   uint64  `json:"available_ns"`
	CutoffNs      uint64  `json:"cutoff_ns"`
	SourceRef     string  `json:"source_ref"`
	SourceSha256  *string `json:"source_sha256"`
	Provenance    string  `json:"provenance"`
}

type InstrumentSpec struct {
	Venue              string   `json:"venue"`
	InstrumentId       string   `json:"instrument_id"`
	Product            string   `json:"product"`
	Underlying         string   `json:"underlying"`
	QuoteCurrency      string   `json:"quote_currency"`
	StrikeCurrency     string   `json:"strike_currency"`
	SettlementCurrency string   `json:"settlement_currency"`
	MarginCurrency     string   `json:"margin_currency"`
	ExpiryNs           uint64   `json:"expiry_ns"`
	Strike             Dec      `json:"strike"`
	OptionType         string   `json:"option_type"`
	ExerciseStyle      string   `json:"exercise_style"`
	QuantityUnit       string   `json:"quantity_unit"`
	Multiplier         Dec      `json:"multiplier"`
	PriceUnit          string   `json:"price_unit"`
	MinQuantityNative  Dec      `json:"min_quantity_native"`
	QuantityStepNative Dec      `json:"quantity_step_native"`
	MaxQuantityNative  *Dec     `json:"max_quantity_native"`
	TickBands          [][2]Dec `json:"tick_bands"`
	SettlementIndex    string   `json:"settlement_index"`
	SettlementWindowNs uint64   `json:"settlement_window_ns"`
	SettlementRule     string   `json:"settlement_rule"`
	RuleVersion        string   `json:"rule_version"`
	MetadataHash       string   `json:"metadata_hash"`
	ValidFromNs        uint64   `json:"valid_from_ns"`
	ValidUntilNs       *uint64  `json:"valid_until_ns"`
	Provenance         string   `json:"provenance"`
}

type InstrumentDescriptor struct {
	Venue        string `json:"venue"`
	Product      string `json:"product"`
	InstrumentId string `json:"instrument_id"`
	Underlying   string `json:"underlying"`
	RuleVersion  string `json:"rule_version"`
	SpecSchema   string `json:"spec_schema"`
	SpecHash     string `json:"spec_hash"`
}

type CandidateLeg struct {
	Instrument     InstrumentDescriptor `json:"instrument"`
	Side           string               `json:"side"`
	QuantityNative Dec                  `json:"quantity_native"`
}

type Observation struct {
	Value       Dec    `json:"value"`
	SourceNs    uint64 `json:"source_ns"`
	ReceivedNs  uint64 `json:"received_ns"`
	AvailableNs uint64 `json:"available_ns"`
	SourceId    string `json:"source_id"`
	Provenance  string `json:"provenance"`
}

type BookLevel struct {
	Price          Dec `json:"price"`
	QuantityNative Dec `json:"quantity_native"`
}

type Book struct {
	Spec        InstrumentSpec `json:"spec"`
	Bids        []BookLevel    `json:"bids"`
	Asks        []BookLevel    `json:"asks"`
	SourceNs    uint64         `json:"source_ns"`
	ReceivedNs  uint64         `json:"received_ns"`
	AvailableNs uint64         `json:"available_ns"`
	RawHash     string         `json:"raw_hash"`
}

type FeePolicy struct {
	Rate        Dec    `json:"rate"`
	PremiumCap  Dec    `json:"premium_cap"`
	FeeCurrency string `json:"fee_currency"`
	Provenance  string `json:"provenance"`
}

type TimingPolicy struct {
	MaxAgeNs           uint64 `json:"max_age_ns"`
	MinimumSourceAgeNs int64  `json:"minimum_source_age_ns"`
}

type QuoteInput struct {
	QuoteId          string       `json:"quote_id"`
	Book             Book         `json:"book"`
	IndexInQuote     *Observation `json:"index_in_quote"`
	IndexCurrency    *string      `json:"index_currency"`
	QuoteToReport    *Observation `json:"quote_to_report"`
	FxPair           *[2]string   `json:"fx_pair"`
	FeePolicy        *FeePolicy   `json:"fee_policy"`
	FeeKnownNs       *uint64      `json:"fee_known_ns"`
	MetadataKnownNs  *uint64      `json:"metadata_known_ns"`
	Timing           TimingPolicy `json:"timing"`
	SourceIdentities [][2]string  `json:"source_identities"`
	Assumptions      []string     `json:"assumptions"`
}

type StatusResult struct {
	Status string `json:"status"`
	Reason string `json:"reason"`
}

type SupportColumns struct {
	InputValidity      StatusResult `json:"input_validity"`
	ModelSupport       StatusResult `json:"model_support"`
	ModeledFeasibility StatusResult `json:"modeled_feasibility"`
	AccountEligibility StatusResult `json:"account_eligibility"`
	ExecutionEvidence  StatusResult `json:"execution_evidence"`
}

type CostEvidence struct {
	Status              StatusResult `json:"status"`
	InstrumentHash      string       `json:"instrument_hash"`
	BookHash            string       `json:"book_hash"`
	Side                string       `json:"side"`
	QuoteCurrency       string       `json:"quote_currency"`
	ReportCurrency      string       `json:"report_currency"`
	AsofNs              uint64       `json:"asof_ns"`
	QuantityNative      Dec          `json:"quantity_native"`
	PremiumNative       *Dec         `json:"premium_native"`
	FeeNative           *Dec         `json:"fee_native"`
	TotalReportCurrency *Dec         `json:"total_report_currency"`
	Assumptions         []string     `json:"assumptions"`
	InputHash           string       `json:"input_hash"`
}

type ScenarioOutcome struct {
	ScenarioId          string `json:"scenario_id"`
	Currency            string `json:"currency"`
	GrossPayoff         *Dec   `json:"gross_payoff"`
	ConditionalModelPnl *Dec   `json:"conditional_model_pnl"`
	Reason              string `json:"reason"`
}

type Candidate struct {
	Leg              CandidateLeg   `json:"leg"`
	Spec             InstrumentSpec `json:"spec"`
	MetadataKnownNs  *uint64        `json:"metadata_known_ns"`
	Quotes           []QuoteInput   `json:"quotes"`
	TerminalFee      *TerminalFee   `json:"terminal_fee"`
	SourceIdentities [][2]string    `json:"source_identities"`
}

type Grid struct {
	Meta     RecordMeta `json:"meta"`
	SpecHash string     `json:"spec_hash"`
	AtNs     uint64     `json:"at_ns"`
	First    Dec        `json:"first"`
	Step     Dec        `json:"step"`
	Count    int        `json:"count"`
}

type ManualSignal struct {
	SchemaVersion string `json:"schema_version"`
	SourceRef     string `json:"source_ref"`
	SourceSha256  string `json:"source_sha256"`
	ObservedNs    uint64 `json:"observed_ns"`
	KnownNs       uint64 `json:"known_ns"`
	ValidUntilNs  uint64 `json:"valid_until_ns"`
	Hypothesis    string `json:"hypothesis"`
}

type Request struct {
	SchemaVersion  string        `json:"schema_version"`
	RequestId      string        `json:"request_id"`
	CutoffNs       uint64        `json:"cutoff_ns"`
	ReportCurrency string        `json:"report_currency"`
	Candidate      *Candidate    `json:"candidate"`
	Grid           *Grid         `json:"grid"`
	Signal         *ManualSignal `json:"signal"`
}

type Point struct {
	DeliveryPrice       Dec  `json:"delivery_price"`
	GrossPayoff         *Dec `json:"gross_payoff"`
	ConditionalModelPnl *Dec `json:"conditional_model_pnl"`
}

type Report struct {
	SchemaVersion         string              `json:"schema_version"`
	ReportCurrency        string              `json:"report_currency"`
	CutoffNs              string              `json:"cutoff_ns"`
	ExpiryNs              *string             `json:"expiry_ns"`
	TerminalFeeAssumption *Dec                `json:"terminal_fee_assumption"`
	InputHash             string              `json:"input_hash"`
	GridHash              *string             `json:"grid_hash"`
	SignalHash            *string             `json:"signal_hash"`
	SignalStatus          StatusResult        `json:"signal_status"`
	Missing               []string            `json:"missing"`
	Diagnostic            *TerminalDiagnostic `json:"diagnostic"`
	Points                []Point             `json:"points"`
	Limitations           []string            `json:"limitations"`
}

type TerminalFee struct {
	AmountReport Dec    `json:"amount_report"`
	KnownNs      uint64 `json:"known_ns"`
	SourceRef    string `json:"source_ref"`
}

type TerminalScenario struct {
	ScenarioId    string `json:"scenario_id"`
	AtNs          uint64 `json:"at_ns"`
	DeliveryPrice Dec    `json:"delivery_price"`
}

type TerminalScenarios struct {
	Meta      RecordMeta         `json:"meta"`
	SpecHash  string             `json:"spec_hash"`
	Scenarios []TerminalScenario `json:"scenarios"`
}

type TerminalDiagnosticRequest struct {
	SchemaVersion    string            `json:"schema_version"`
	RequestId        string            `json:"request_id"`
	CutoffNs         uint64            `json:"cutoff_ns"`
	ReportCurrency   string            `json:"report_currency"`
	Leg              CandidateLeg      `json:"leg"`
	Spec             InstrumentSpec    `json:"spec"`
	MetadataKnownNs  *uint64           `json:"metadata_known_ns"`
	Scenarios        TerminalScenarios `json:"scenarios"`
	Quotes           []QuoteInput      `json:"quotes"`
	TerminalFee      *TerminalFee      `json:"terminal_fee"`
	SourceIdentities [][2]string       `json:"source_identities"`
}

type TerminalDiagnostic struct {
	RequestId         string            `json:"request_id"`
	InputHash         string            `json:"input_hash"`
	SpecHash          string            `json:"spec_hash"`
	ScenarioHash      string            `json:"scenario_hash"`
	ModelVersion      string            `json:"model_version"`
	Support           SupportColumns    `json:"support"`
	SelectedQuoteHash *string           `json:"selected_quote_hash"`
	Cost              *CostEvidence     `json:"cost"`
	NetStatus         StatusResult      `json:"net_status"`
	Outcomes          []ScenarioOutcome `json:"outcomes"`
	Reasons           []string          `json:"reasons"`
}
