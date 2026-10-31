package options

import (
	"reflect"
	"strings"
)

const requestSchema = "options-payoff-curve-request.rust.v1"
const scenarioSchema = "options-terminal-scenarios.rust.v1"

func provenance(s string) {
	require(oneOf(s, "fixture", "manual_unattested", "sealed_proxy", "derived", "observed", "proxy"), "INVALID_INPUT")
}
func identities(xs [][2]string) {
	seen := map[string]bool{}
	for _, x := range xs {
		require(text(x[0]) && text(x[1]) && !seen[x[0]], "INVALID_IDENTITY")
		seen[x[0]] = true
	}
}
func descriptor(s InstrumentSpec) InstrumentDescriptor {
	return InstrumentDescriptor{s.Venue, s.Product, s.InstrumentId, s.Underlying, s.RuleVersion, "options-instrument.rust.v1", hash(s)}
}
func validateMeta(m RecordMeta, cutoff uint64) {
	provenance(m.Provenance)
	require(m.ReceivedNs <= m.AvailableNs && m.AvailableNs <= m.CutoffNs, "INVALID_TIMESTAMP")
	require(m.SchemaVersion == scenarioSchema && m.MethodVersion == "exact_decimal.rust.v1", "INVALID_SCHEMA")
	require(m.Revision > 0 && (m.Revision == 1) == (m.ParentHash == nil) && strings.TrimSpace(m.ObjectId) != "" && strings.TrimSpace(m.SourceRef) != "", "INVALID_METADATA")
	require((m.ParentHash == nil || validHash(*m.ParentHash)) && (m.SourceSha256 == nil || validHash(*m.SourceSha256)) && (m.Provenance != "sealed_proxy" || m.SourceSha256 != nil), "INVALID_IDENTITY")
	require(m.CutoffNs <= cutoff, "INVALID_TIMESTAMP")
	require(m.Revision == 1, "INVALID_METADATA")
}
func validateSpec(s InstrumentSpec) {
	require(oneOf(s.Product, "linear_option", "inverse_option") && oneOf(s.OptionType, "C", "P") && oneOf(s.ExerciseStyle, "EUROPEAN", "AMERICAN", "UNKNOWN") && oneOf(s.QuantityUnit, "base", "contracts") && oneOf(s.PriceUnit, "quote_per_base", "quote_per_contract") && oneOf(s.SettlementRule, "linear_cash", "linear_via_future", "inverse_via_future", "UNKNOWN"), "INVALID_INPUT")
	provenance(s.Provenance)
	for _, v := range []string{s.Venue, s.InstrumentId, s.Underlying, s.QuoteCurrency, s.StrikeCurrency, s.SettlementCurrency, s.MarginCurrency, s.SettlementIndex, s.RuleVersion} {
		require(text(v), "INVALID_METADATA")
	}
	require(validHash(s.MetadataHash), "INVALID_METADATA")
	z := Dec{}
	require(s.Strike.cmp(z) > 0 && s.Multiplier.cmp(z) > 0 && s.MinQuantityNative.cmp(z) > 0 && s.QuantityStepNative.cmp(z) > 0 && s.MinQuantityNative.multiple(s.QuantityStepNative) && (s.MaxQuantityNative == nil || (s.MaxQuantityNative.cmp(s.MinQuantityNative) >= 0 && s.MaxQuantityNative.multiple(s.QuantityStepNative))), "INVALID_INPUT")
	require(s.ExpiryNs > s.ValidFromNs && (s.ValidUntilNs == nil || *s.ValidUntilNs > s.ValidFromNs) && s.SettlementWindowNs > 0, "INVALID_TIMESTAMP")
	require(len(s.TickBands) > 0 && len(s.TickBands) <= 128 && s.TickBands[0][0].cmp(z) == 0, "INVALID_INPUT")
	for i, b := range s.TickBands {
		require(b[0].cmp(z) >= 0 && b[1].cmp(z) > 0 && (i == 0 || b[0].cmp(s.TickBands[i-1][0]) > 0), "INVALID_INPUT")
	}
}
func validateObservation(o *Observation) {
	if o == nil {
		return
	}
	provenance(o.Provenance)
	require(o.ReceivedNs <= o.AvailableNs, "INVALID_TIMESTAMP")
	require(o.Value.cmp(Dec{}) > 0 && text(o.SourceId), "INVALID_INPUT")
}
func validateQuotes(qs []QuoteInput, s InstrumentSpec, report string) {
	ids := map[string]bool{}
	evidence := map[[3]uint64]QuoteInput{}
	for _, q := range qs {
		validateSpec(q.Book.Spec)
		b := q.Book
		require(b.ReceivedNs <= b.AvailableNs, "INVALID_TIMESTAMP")
		require(text(q.QuoteId) && validHash(b.RawHash) && (q.Timing.MinimumSourceAgeNs < 0 || uint64(q.Timing.MinimumSourceAgeNs) <= q.Timing.MaxAgeNs), "INVALID_METADATA")
		for side, ls := range [][]BookLevel{b.Bids, b.Asks} {
			require(len(ls) <= 128, "LIMIT_EXCEEDED")
			for i, l := range ls {
				require(l.Price.cmp(Dec{}) >= 0 && l.QuantityNative.cmp(Dec{}) > 0, "INVALID_INPUT")
				if i > 0 {
					cmp := ls[i-1].Price.cmp(l.Price)
					require((side == 0 && cmp > 0) || (side == 1 && cmp < 0), "CONFLICTING_EVIDENCE")
				}
			}
		}
		if len(b.Bids) > 0 && len(b.Asks) > 0 {
			require(b.Bids[0].Price.cmp(b.Asks[0].Price) <= 0, "CONFLICTING_EVIDENCE")
		}
		validateObservation(q.IndexInQuote)
		validateObservation(q.QuoteToReport)
		require((q.IndexInQuote == nil && q.IndexCurrency == nil) || (q.IndexInQuote != nil && q.IndexCurrency != nil && *q.IndexCurrency == b.Spec.QuoteCurrency), "INVALID_INPUT")
		require((q.QuoteToReport == nil && q.FxPair == nil) || (q.QuoteToReport != nil && q.FxPair != nil && *q.FxPair == [2]string{b.Spec.QuoteCurrency, report}), "INVALID_INPUT")
		if f := q.FeePolicy; f != nil {
			provenance(f.Provenance)
			require(f.Rate.cmp(Dec{}) >= 0 && f.PremiumCap.cmp(Dec{}) >= 0 && text(f.FeeCurrency), "INVALID_INPUT")
		} else {
			require(q.FeeKnownNs == nil, "INVALID_INPUT")
		}
		identities(q.SourceIdentities)
		key := b.Spec.Venue + "\x00" + q.QuoteId
		require(!ids[key], "INVALID_IDENTITY")
		ids[key] = true
		ts := [3]uint64{b.SourceNs, b.ReceivedNs, b.AvailableNs}
		if prev, ok := evidence[ts]; ok && hash(prev.Book.Spec) == hash(b.Spec) {
			require(reflect.DeepEqual(prev, q), "CONFLICTING_EVIDENCE")
		}
		evidence[ts] = q
		require(hash(b.Spec) == hash(s), "INVALID_IDENTITY")
	}
}
func validate(r Request) {
	require(r.SchemaVersion == requestSchema, "INVALID_SCHEMA")
	require(text(r.RequestId) && text(r.ReportCurrency), "INVALID_INPUT")
	require(len(canonical(r)) <= 1048576, "LIMIT_EXCEEDED")
	if g := r.Grid; g != nil {
		require(g.Count >= 2 && g.Count <= 4096, "LIMIT_EXCEEDED")
		require(g.First.cmp(Dec{}) >= 0 && g.Step.cmp(Dec{}) > 0, "INVALID_INPUT")
		validateMeta(g.Meta, r.CutoffNs)
		require(validHash(g.SpecHash), "INVALID_METADATA")
	}
	if s := r.Signal; s != nil {
		require(s.SchemaVersion == "manual-scenario-signal.rust.v1", "INVALID_SCHEMA")
		require(text(s.SourceRef) && text(s.Hypothesis) && validHash(s.SourceSha256), "INVALID_INPUT")
		require(s.ObservedNs <= s.KnownNs && s.KnownNs < s.ValidUntilNs, "INVALID_TIMESTAMP")
	}
	// Candidate admission is deferred until both the candidate and grid exist, as in Rust.
	if r.Candidate == nil || r.Grid == nil {
		return
	}
	c := r.Candidate
	require(r.Grid.SpecHash == hash(c.Spec), "INVALID_IDENTITY")
	require(len(c.Quotes) <= 4, "LIMIT_EXCEEDED")
	require(c.Spec.SettlementRule == "linear_cash", "UNSUPPORTED")
	identities(c.SourceIdentities)
	validateSpec(c.Spec)
	require(oneOf(c.Leg.Side, "BUY", "SELL"), "INVALID_INPUT")
	require(c.Leg.Instrument == descriptor(c.Spec) && c.Leg.QuantityNative.cmp(Dec{}) > 0, "INVALID_IDENTITY")
	validateQuotes(c.Quotes, c.Spec, r.ReportCurrency)
	if f := c.TerminalFee; f != nil {
		require(f.AmountReport.cmp(Dec{}) >= 0 && text(f.SourceRef), "INVALID_INPUT")
	}
}
