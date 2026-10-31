package options

import "strconv"

type syntheticParameters struct {
	OptionType         string  `json:"option_type"`
	Strike             Dec     `json:"strike"`
	Quantity           Dec     `json:"quantity"`
	Ask                Dec     `json:"ask"`
	Bid                Dec     `json:"bid"`
	FeeRate            *Dec    `json:"fee_rate"`
	TerminalFee        *Dec    `json:"terminal_fee"`
	First              Dec     `json:"first"`
	Step               Dec     `json:"step"`
	Count              int     `json:"count"`
	SignalHypothesis   *string `json:"signal_hypothesis"`
	SignalKnownNs      string  `json:"signal_known_ns"`
	SignalValidUntilNs string  `json:"signal_valid_until_ns"`
}

func synthetic(p syntheticParameters) Request {
	require(oneOf(p.OptionType, "C", "P"), "INVALID_INPUT")
	zeroHash := "0000000000000000000000000000000000000000000000000000000000000000"
	s := InstrumentSpec{Venue: "MATHEMATICAL_FIXTURE", InstrumentId: "ETH-1000-" + p.Strike.String() + "-" + p.OptionType, Product: "linear_option", Underlying: "ETH", QuoteCurrency: "USDC", StrikeCurrency: "USDC", SettlementCurrency: "USDC", MarginCurrency: "USDC", ExpiryNs: 1000, Strike: p.Strike, OptionType: p.OptionType, ExerciseStyle: "EUROPEAN", QuantityUnit: "base", Multiplier: decimal("1"), PriceUnit: "quote_per_base", MinQuantityNative: decimal(".01"), QuantityStepNative: decimal(".01"), TickBands: [][2]Dec{{decimal("0"), decimal(".01")}}, SettlementIndex: "MATHEMATICAL_ETH", SettlementWindowNs: 1, SettlementRule: "linear_cash", RuleVersion: "mathematical.v1", MetadataHash: zeroHash, Provenance: "fixture"}
	obs := func(v string) *Observation {
		return &Observation{decimal(v), 10, 10, 10, "mathematical_fixture_not_market_data", "fixture"}
	}
	q := QuoteInput{QuoteId: s.InstrumentId, Book: Book{s, []BookLevel{{p.Bid, p.Quantity}}, []BookLevel{{p.Ask, p.Quantity}}, 10, 10, 10, zeroHash}, IndexInQuote: obs("100"), IndexCurrency: ptr("USDC"), QuoteToReport: obs("1"), FxPair: ptr([2]string{"USDC", "USDC"}), MetadataKnownNs: ptr(uint64(0)), Timing: TimingPolicy{100, 0}, SourceIdentities: [][2]string{}, Assumptions: []string{"MATHEMATICAL_FIXTURE_NOT_MARKET_DATA"}}
	if p.FeeRate != nil {
		q.FeePolicy = &FeePolicy{*p.FeeRate, decimal(".125"), "USDC", "fixture"}
		q.FeeKnownNs = ptr(uint64(0))
	}
	c := Candidate{Leg: CandidateLeg{descriptor(s), "BUY", p.Quantity}, Spec: s, MetadataKnownNs: ptr(uint64(0)), Quotes: []QuoteInput{q}, SourceIdentities: [][2]string{}}
	if p.TerminalFee != nil {
		c.TerminalFee = &TerminalFee{*p.TerminalFee, 0, "EXPLICIT_SYNTHETIC_TOTAL_TERMINAL_FEE"}
	}
	meta := RecordMeta{ObjectId: "mathematical-view", Revision: 1, SchemaVersion: scenarioSchema, MethodVersion: "exact_decimal.rust.v1", ReceivedNs: 10, AvailableNs: 10, CutoffNs: 10, SourceRef: "mathematical_fixture_not_market_data", Provenance: "fixture"}
	r := Request{SchemaVersion: requestSchema, RequestId: "synthetic-curve", CutoffNs: 10, ReportCurrency: "USDC", Candidate: &c, Grid: &Grid{meta, hash(s), 1000, p.First, p.Step, p.Count}}
	if p.SignalHypothesis != nil {
		parse := func(s string) uint64 {
			if len(s) > 0 && s[0] == '+' {
				s = s[1:]
			}
			n, e := strconv.ParseUint(s, 10, 64)
			require(e == nil, "INVALID_TIMESTAMP")
			return n
		}
		known := parse(p.SignalKnownNs)
		until := parse(p.SignalValidUntilNs)
		r.Signal = &ManualSignal{"manual-scenario-signal.rust.v1", "MANUAL_HYPOTHESIS_ON_SYNTHETIC_CLOCK", sha([]byte(*p.SignalHypothesis)), known, known, until, *p.SignalHypothesis}
	}
	return r
}
