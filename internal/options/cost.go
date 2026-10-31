package options

import (
	"fmt"
	"math/big"
)

func status(s, r string) StatusResult { return StatusResult{s, r} }
func need(ok bool, s, r string) {
	if !ok {
		panic(status(s, r))
	}
}
func diagnostic(run func()) (out StatusResult) {
	out = status("VALID", "")
	defer func() {
		if v := recover(); v != nil {
			switch e := v.(type) {
			case StatusResult:
				out = e
			case *Error:
				names := map[string]string{"PRECISION_LIMIT": "PrecisionLimit", "NUMERICAL_LIMITATION": "NumericalLimitation", "DIVISION_BY_ZERO": "DivisionByZero"}
				n := names[e.Code]
				if n == "" {
					n = e.Code
				}
				out = status("NOT_EVALUABLE", "NUMERICAL_LIMITATION:"+n)
			default:
				panic(v)
			}
		}
	}()
	run()
	return
}
func legalQuantity(s InstrumentSpec, q Dec) Dec {
	need(s.Multiplier.cmp(Dec{}) > 0, "INVALID", "INVALID_MULTIPLIER")
	need(s.MinQuantityNative.cmp(Dec{}) > 0 && s.QuantityStepNative.cmp(Dec{}) > 0, "INVALID", "INVALID_LOT_RULE")
	need(q.cmp(Dec{}) > 0 && q.cmp(s.MinQuantityNative) >= 0 && q.multiple(s.QuantityStepNative) && (s.MaxQuantityNative == nil || q.cmp(*s.MaxQuantityNative) <= 0), "REJECTED", "ILLEGAL_NATIVE_QUANTITY")
	if s.QuantityUnit == "base" {
		need(s.Multiplier.cmp(decimal("1")) == 0, "UNSUPPORTED", "BASE_MULTIPLIER_UNSUPPORTED")
		return q
	}
	return q.mul(s.Multiplier)
}
func knowledge(at *uint64, cutoff uint64, label string) {
	need(at != nil && *at <= cutoff, "NOT_EVALUABLE", label+"_KNOWLEDGE_UNAVAILABLE")
}
func timestamp(source, received, available, cutoff uint64, p TimingPolicy, label string, notes *[]string) {
	need(received <= available, "INVALID", "CLOCK_ORDER_INVALID")
	need(received <= cutoff && available <= cutoff, "NOT_EVALUABLE", label+"_NOT_YET_AVAILABLE")
	age := new(big.Int).Sub(new(big.Int).SetUint64(cutoff), new(big.Int).SetUint64(source))
	lo := big.NewInt(p.MinimumSourceAgeNs)
	hi := new(big.Int).SetUint64(p.MaxAgeNs)
	need(lo.Cmp(hi) <= 0, "INVALID", "INVALID_SOURCE_AGE_POLICY")
	need(age.Cmp(lo) >= 0 && age.Cmp(hi) <= 0, "NOT_EVALUABLE", label+"_SOURCE_AGE_OUTSIDE_POLICY")
	if age.Sign() < 0 {
		*notes = append(*notes, fmt.Sprintf("SOURCE_CLOCK_LEAD:%s:%s", label, new(big.Int).Neg(age).String()))
	}
}
func quoteCost(q QuoteInput, l CandidateLeg, cutoff uint64, report string) CostEvidence {
	s := q.Book.Spec
	b := q.Book
	out := CostEvidence{Status: status("NOT_EVALUABLE", "COST_NOT_EVALUATED"), InstrumentHash: l.Instrument.SpecHash, BookHash: b.RawHash, Side: l.Side, QuoteCurrency: s.QuoteCurrency, ReportCurrency: report, AsofNs: cutoff, QuantityNative: l.QuantityNative, Assumptions: append([]string{}, q.Assumptions...)}
	result := diagnostic(func() {
		out.InputHash = hash([]any{q, l, cutoff, report})
		need(descriptor(s) == l.Instrument, "INVALID", "EXACT_SPEC_BINDING_REQUIRED")
		knowledge(q.MetadataKnownNs, cutoff, "METADATA")
		need(s.ValidFromNs <= cutoff && (s.ValidUntilNs == nil || cutoff < *s.ValidUntilNs), "NOT_EVALUABLE", "SPEC_OUTSIDE_VALID_DOMAIN")
		timestamp(b.SourceNs, b.ReceivedNs, b.AvailableNs, cutoff, q.Timing, "BOOK", &out.Assumptions)
		base := legalQuantity(s, l.QuantityNative)
		need(s.QuantityUnit == "contracts" || s.PriceUnit == "quote_per_base", "UNSUPPORTED", "PRICE_QUANTITY_UNIT_UNSUPPORTED")
		need(len(b.Bids) > 0 && len(b.Asks) > 0, "NOT_EVALUABLE", "TWO_SIDED_BOOK_REQUIRED")
		for _, levels := range [][]BookLevel{b.Bids, b.Asks} {
			for _, level := range levels {
				tick := s.TickBands[0][1]
				for _, band := range s.TickBands {
					if level.Price.cmp(band[0]) >= 0 {
						tick = band[1]
					}
				}
				need(level.Price.multiple(tick), "REJECTED", "ILLEGAL_NATIVE_PRICE_TICK")
			}
		}
		need(b.Bids[0].Price.cmp(b.Asks[0].Price) < 0, "INVALID", "UNORDERED_OR_CROSSED_BOOK")
		remaining := l.QuantityNative
		total := Dec{}
		levels := b.Asks
		if l.Side == "SELL" {
			levels = b.Bids
		}
		for _, level := range levels {
			if remaining.cmp(Dec{}) == 0 {
				break
			}
			take := min(remaining, level.QuantityNative)
			priced := take
			if s.PriceUnit == "quote_per_base" && s.QuantityUnit == "contracts" {
				priced = take.mul(s.Multiplier)
			}
			total = total.add(level.Price.mul(priced))
			remaining = remaining.sub(take)
		}
		need(remaining.cmp(Dec{}) == 0, "NOT_EVALUABLE", "INSUFFICIENT_DISPLAYED_DEPTH")
		out.PremiumNative = ptr(total)
		knowledge(q.FeeKnownNs, cutoff, "FEE")
		need(q.FeePolicy != nil, "NOT_EVALUABLE", "MISSING_FEE_POLICY")
		f := q.FeePolicy
		need(f.Rate.cmp(Dec{}) >= 0 && f.PremiumCap.cmp(Dec{}) >= 0, "INVALID", "NEGATIVE_FEE_POLICY")
		need(f.FeeCurrency == s.QuoteCurrency, "UNSUPPORTED", "FEE_CURRENCY_UNSUPPORTED")
		need(q.IndexInQuote != nil, "NOT_EVALUABLE", "MISSING_INDEX")
		need(q.IndexCurrency != nil && *q.IndexCurrency == s.QuoteCurrency, "NOT_EVALUABLE", "INDEX_CURRENCY_MISMATCH")
		index := q.IndexInQuote
		need(index.Value.cmp(Dec{}) > 0, "INVALID", "INVALID_INDEX")
		timestamp(index.SourceNs, index.ReceivedNs, index.AvailableNs, cutoff, q.Timing, "INDEX", &out.Assumptions)
		fee := min(f.Rate.mul(index.Value).mul(base), f.PremiumCap.mul(total))
		out.FeeNative = ptr(fee)
		need(q.QuoteToReport != nil, "NOT_EVALUABLE", "MISSING_EXPLICIT_FX")
		need(q.FxPair != nil && *q.FxPair == [2]string{s.QuoteCurrency, report}, "NOT_EVALUABLE", "FX_PAIR_MISMATCH")
		fx := q.QuoteToReport
		need(fx.Value.cmp(Dec{}) > 0 && (s.QuoteCurrency != report || fx.Value.cmp(decimal("1")) == 0), "INVALID", "INVALID_FX_VALUE")
		timestamp(fx.SourceNs, fx.ReceivedNs, fx.AvailableNs, cutoff, q.Timing, "FX", &out.Assumptions)
		net := total.add(fee)
		if l.Side == "SELL" {
			net = total.sub(fee)
		}
		out.TotalReportCurrency = ptr(net.mul(fx.Value))
	})
	if result.Status == "VALID" {
		result.Reason = "DISPLAYED_COST_ONLY_NO_FILL"
	}
	out.Status = result
	return out
}
func selectedQuote(qs []QuoteInput, cutoff uint64) *QuoteInput {
	var selected *QuoteInput
	for i := range qs {
		q := &qs[i]
		if q.Book.ReceivedNs > cutoff || q.Book.AvailableNs > cutoff {
			continue
		}
		if selected == nil {
			selected = q
			continue
		}
		a, b := q.Book, selected.Book
		if a.SourceNs > b.SourceNs || (a.SourceNs == b.SourceNs && (a.ReceivedNs > b.ReceivedNs || (a.ReceivedNs == b.ReceivedNs && a.AvailableNs > b.AvailableNs))) {
			selected = q
		}
	}
	return selected
}
