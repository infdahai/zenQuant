package options

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/big"
	"sort"
	"strings"
	"time"
)

const vixMaxTime uint64 = 8640000000000000

type VixSource struct {
	Reference string `json:"reference"`
	SHA256    string `json:"sha256"`
	Timezone  string `json:"timezone"`
	Known     uint64 `json:"known_ms"`
}
type VixContract struct {
	Symbol     string `json:"symbol"`
	Tenor      string `json:"tenor"`
	Month      uint32 `json:"delivery_month"`
	Last       uint64 `json:"last_trade_ms"`
	Settlement string `json:"settlement_date"`
}
type VixCalendar struct {
	Source    VixSource     `json:"source"`
	Timezone  string        `json:"exchange_timezone"`
	From      uint64        `json:"valid_from_ms"`
	Until     uint64        `json:"valid_until_ms"`
	First     uint32        `json:"first_delivery_month"`
	Contracts []VixContract `json:"contracts"`
}
type VixPrice struct {
	Kind  string `json:"kind"`
	Value *Dec   `json:"value"`
	Bid   *Dec   `json:"bid"`
	Ask   *Dec   `json:"ask"`
}

func (p VixPrice) MarshalJSON() ([]byte, error) {
	if p.Kind == "MIDPOINT" {
		return json.Marshal(struct {
			Kind string `json:"kind"`
			Bid  *Dec   `json:"bid"`
			Ask  *Dec   `json:"ask"`
		}{p.Kind, p.Bid, p.Ask})
	}
	return json.Marshal(struct {
		Kind  string `json:"kind"`
		Value *Dec   `json:"value"`
	}{p.Kind, p.Value})
}

type VixQuote struct {
	Symbol   string    `json:"symbol"`
	Price    VixPrice  `json:"price"`
	Source   VixSource `json:"source"`
	Observed uint64    `json:"observed_ms"`
	Received uint64    `json:"received_ms"`
	Session  string    `json:"trading_session_date"`
	Delay    *uint64   `json:"declared_delay_ms"`
}
type VixPrevious struct {
	Cutoff uint64 `json:"cutoff_ms"`
	Front  string `json:"front"`
	Next   string `json:"next"`
}
type VixFutures struct {
	Rule     string       `json:"roll_rule"`
	Calendar VixCalendar  `json:"calendar"`
	Quotes   []VixQuote   `json:"quotes"`
	Previous *VixPrevious `json:"previous_pair"`
}
type VixSpot struct {
	Vix9d *VixQuote `json:"vix9d"`
	Vix   *VixQuote `json:"vix"`
}
type VixRequest struct {
	Schema  string      `json:"schema_version"`
	Mode    string      `json:"mode"`
	Cutoff  uint64      `json:"cutoff_ms"`
	MaxAge  uint64      `json:"max_age_ms"`
	Futures *VixFutures `json:"futures"`
	Spot    *VixSpot    `json:"spot"`
}
type vixRatio struct {
	Numerator   Dec     `json:"numerator"`
	Denominator Dec     `json:"denominator"`
	Exact       *string `json:"decimal_exact"`
}
type vixMetrics struct {
	First          Dec      `json:"first_value"`
	Second         Dec      `json:"second_value"`
	Spread         Dec      `json:"spread_points"`
	Ratio          vixRatio `json:"ratio"`
	Minus          vixRatio `json:"ratio_minus_one"`
	Classification string   `json:"classification"`
}
type vixPanel struct {
	Status   string        `json:"status"`
	Reason   string        `json:"reason"`
	Metrics  *vixMetrics   `json:"metrics"`
	Selected []VixContract `json:"selected_contracts"`
	Roll     string        `json:"roll"`
}
type vixReport struct {
	Schema   string   `json:"schema_version"`
	Model    string   `json:"model_version"`
	Mode     string   `json:"mode"`
	Evidence string   `json:"evidence_status"`
	Cutoff   uint64   `json:"cutoff_ms"`
	MaxAge   uint64   `json:"max_age_ms"`
	Hash     string   `json:"input_sha256"`
	Futures  vixPanel `json:"futures"`
	Spot     vixPanel `json:"spot"`
}

func vixUnavailable(status, reason string) vixPanel {
	return vixPanel{status, reason, nil, []VixContract{}, "UNKNOWN_NO_PREVIOUS_PAIR"}
}
func vixSource(s VixSource, cutoff uint64) bool {
	_, err := hex.DecodeString(s.SHA256)
	return strings.TrimSpace(s.Reference) != "" && len(s.Reference) <= 512 && len(s.SHA256) == 64 && err == nil && oneOf(s.Timezone, "UTC", "America/Chicago", "America/New_York") && s.Known <= cutoff
}
func vixDate(s string) bool {
	t, e := time.Parse("2006-01-02", s)
	return e == nil && t.Year() >= 2000 && t.Year() <= 2199 && t.Format("2006-01-02") == s
}
func vixMonth(n uint32) int {
	y, m := n/100, n%100
	if y < 2000 || y > 2199 || m < 1 || m > 12 {
		return -1
	}
	return int(y*12 + m - 1)
}
func vixQuoteValue(q VixQuote, r VixRequest) (Dec, string, string) {
	bad := func(reason string) (Dec, string, string) { return Dec{}, "INVALID", reason }
	if !vixSource(q.Source, r.Cutoff) {
		return bad("INVALID_SOURCE_OR_FUTURE_KNOWLEDGE")
	}
	if q.Observed > q.Received || q.Received > q.Source.Known || !vixDate(q.Session) {
		return bad("INVALID_QUOTE_TIME")
	}
	if q.Delay == nil {
		return Dec{}, "MISSING", "UNKNOWN_SOURCE_DELAY"
	}
	if *q.Delay > q.Received-q.Observed {
		return bad("INCONSISTENT_SOURCE_DELAY")
	}
	if r.Cutoff-q.Observed > r.MaxAge {
		return Dec{}, "STALE", "QUOTE_EXPIRED"
	}
	positive := func(p *Dec) (Dec, string, string) {
		if p == nil {
			return Dec{}, "MISSING", "MISSING_PRICE"
		}
		if p.cmp(Dec{}) <= 0 {
			return bad("NONPOSITIVE_PRICE")
		}
		return *p, "", ""
	}
	if q.Price.Kind == "MIDPOINT" {
		a, s, e := positive(q.Price.Bid)
		if s != "" {
			return a, s, e
		}
		b, s, e := positive(q.Price.Ask)
		if s != "" {
			return b, s, e
		}
		if a.cmp(b) > 0 {
			return bad("CROSSED_MARKET")
		}
		return bounded(new(big.Rat).Quo(new(big.Rat).Add(a.rat(), b.rat()), big.NewRat(2, 1))), "", ""
	}
	return positive(q.Price.Value)
}
func vixFraction(n, d Dec) vixRatio {
	rat := new(big.Rat).Quo(n.rat(), d.rat())
	den := new(big.Int).Set(rat.Denom())
	for _, p := range []int64{2, 5} {
		x := big.NewInt(p)
		for new(big.Int).Mod(den, x).Sign() == 0 {
			den.Quo(den, x)
		}
	}
	var result *string
	if den.Cmp(big.NewInt(1)) == 0 {
		s := bounded(rat).String()
		result = &s
	}
	return vixRatio{n, d, result}
}
func vixCalculate(a, b VixQuote, r VixRequest, future bool) vixPanel {
	x, s, e := vixQuoteValue(a, r)
	if s != "" {
		return vixUnavailable(s, e)
	}
	y, s, e := vixQuoteValue(b, r)
	if s != "" {
		return vixUnavailable(s, e)
	}
	if a.Observed != b.Observed || a.Session != b.Session {
		return vixUnavailable("INVALID", "ASYNCHRONOUS_OR_DIFFERENT_SESSION_QUOTES")
	}
	if (future && (a.Price.Kind != b.Price.Kind || !oneOf(a.Price.Kind, "SETTLEMENT", "MIDPOINT"))) || (!future && (a.Price.Kind != "INDEX" || b.Price.Kind != "INDEX")) {
		return vixUnavailable("INVALID", "INCOMPATIBLE_PRICE_BASIS")
	}
	spread := x.sub(y)
	c := "FLAT"
	if future {
		if spread.cmp(Dec{}) < 0 {
			c = "CONTANGO"
		} else if spread.cmp(Dec{}) > 0 {
			c = "BACKWARDATION"
		}
	} else {
		c = "EQUAL_HORIZON_VALUES"
		if spread.cmp(Dec{}) < 0 {
			c = "SHORT_HORIZON_LOWER"
		} else if spread.cmp(Dec{}) > 0 {
			c = "SHORT_HORIZON_HIGHER"
		}
	}
	p := vixUnavailable("READY", "AT_INPUT_CUTOFF_ONLY")
	p.Metrics = &vixMetrics{x, y, spread, vixFraction(x, y), vixFraction(spread, y), c}
	return p
}
func vixFuture(r VixRequest) vixPanel {
	f := r.Futures
	if f == nil {
		return vixUnavailable("MISSING", "FUTURES_INPUT_REQUIRED")
	}
	c := f.Calendar
	invalid := func(reason string) vixPanel { return vixUnavailable("INVALID", reason) }
	if !vixSource(c.Source, r.Cutoff) {
		return invalid("INVALID_SOURCE_OR_FUTURE_KNOWLEDGE")
	}
	if f.Rule != "MONTHLY_AT_SUPPLIED_LAST_TRADE_V1" || c.Timezone != "America/Chicago" || c.From >= c.Until || c.Until > vixMaxTime || r.Cutoff < c.From || c.Source.Known > c.From {
		return invalid("INVALID_CALENDAR_OR_ROLL_RULE")
	}
	if r.Cutoff >= c.Until {
		return vixUnavailable("STALE", "CALENDAR_EXPIRED")
	}
	if len(c.Contracts) > 32 || len(f.Quotes) > 32 {
		return invalid("INPUT_LIMIT")
	}
	symbols := map[string]bool{}
	monthly := []VixContract{}
	for _, v := range c.Contracts {
		m := vixMonth(v.Month)
		if m < 0 {
			return invalid("INVALID_DELIVERY_MONTH")
		}
		parts := strings.Split(v.Symbol, "/")
		prefix := ""
		if len(parts) == 2 {
			prefix = parts[0]
		}
		weekly := false
		if len(prefix) == 4 && strings.HasPrefix(prefix, "VX") && prefix[2] >= '0' && prefix[2] <= '9' && prefix[3] >= '0' && prefix[3] <= '9' {
			w := int(prefix[2]-'0')*10 + int(prefix[3]-'0')
			weekly = w >= 1 && w <= 53
		}
		expected := fmt.Sprintf("%s/%c%d", prefix, "FGHJKMNQUVXZ"[m%12], (v.Month/100)%10)
		if symbols[v.Symbol] || v.Symbol != expected || !(v.Tenor == "MONTHLY" && prefix == "VX" || v.Tenor == "WEEKLY" && weekly) || !vixDate(v.Settlement) || v.Last > vixMaxTime || v.Last <= c.From {
			return invalid("INVALID_CONTRACT_CALENDAR")
		}
		symbols[v.Symbol] = true
		d, _ := time.Parse("2006-01-02", v.Settlement)
		if v.Last/86400000 != uint64(d.Unix()/86400) || uint32(d.Year()*100+int(d.Month())) != v.Month {
			return invalid("CONFLICTING_EXPIRY_DATE")
		}
		if v.Tenor == "MONTHLY" {
			monthly = append(monthly, v)
		}
	}
	sort.Slice(monthly, func(i, j int) bool { return monthly[i].Month < monthly[j].Month })
	if len(monthly) == 0 || monthly[0].Month != c.First {
		return vixUnavailable("MISSING", "FIRST_MONTHLY_CONTRACT_REQUIRED")
	}
	for i := 1; i < len(monthly); i++ {
		if vixMonth(monthly[i].Month) != vixMonth(monthly[i-1].Month)+1 || monthly[i-1].Last >= monthly[i].Last {
			return invalid("NONCONSECUTIVE_MONTHLY_CALENDAR")
		}
	}
	pair := func(t uint64) []VixContract {
		a := []VixContract{}
		for _, v := range monthly {
			if v.Last > t {
				a = append(a, v)
				if len(a) == 2 {
					break
				}
			}
		}
		return a
	}
	selected := pair(r.Cutoff)
	if len(selected) < 2 {
		return vixUnavailable("MISSING", "TWO_UNEXPIRED_MONTHLY_CONTRACTS_REQUIRED")
	}
	roll := "UNKNOWN_NO_PREVIOUS_PAIR"
	if p := f.Previous; p != nil {
		if p.Cutoff < c.From || p.Cutoff >= r.Cutoff {
			return invalid("INVALID_PREVIOUS_PAIR_TIME")
		}
		prev := pair(p.Cutoff)
		if len(prev) < 2 {
			return vixUnavailable("MISSING", "TWO_UNEXPIRED_MONTHLY_CONTRACTS_REQUIRED")
		}
		if prev[0].Symbol != p.Front || prev[1].Symbol != p.Next {
			return invalid("PREVIOUS_PAIR_DISAGREES_WITH_CALENDAR")
		}
		roll = "CHANGED_FROM_SUPPLIED_PAIR"
		if selected[0].Symbol == p.Front && selected[1].Symbol == p.Next {
			roll = "UNCHANGED_FROM_SUPPLIED_PAIR"
		}
	}
	seen := map[string]bool{}
	quotes := map[string]VixQuote{}
	var result vixPanel
	for _, q := range f.Quotes {
		if seen[q.Symbol] || !symbols[q.Symbol] {
			result = invalid("DUPLICATE_OR_UNKNOWN_QUOTE_SYMBOL")
			result.Selected = selected
			result.Roll = roll
			return result
		}
		seen[q.Symbol] = true
		quotes[q.Symbol] = q
	}
	a, ok := quotes[selected[0].Symbol]
	b, ok2 := quotes[selected[1].Symbol]
	if !ok || !ok2 {
		result = vixUnavailable("MISSING", "SELECTED_MONTHLY_QUOTE_REQUIRED")
	} else {
		result = vixCalculate(a, b, r, true)
	}
	result.Selected = selected
	result.Roll = roll
	return result
}
func vixAnalyze(r VixRequest) vixReport {
	require(r.Schema == "vix-observation-request.rust.v1", "INVALID_SCHEMA")
	require(oneOf(r.Mode, "DEMONSTRATION", "SUPPLIED_SNAPSHOT_UNVERIFIED"), "INVALID_INPUT")
	require(r.Cutoff <= vixMaxTime && r.MaxAge > 0 && r.MaxAge <= 86400000, "INVALID_TIMESTAMP")
	spot := vixUnavailable("MISSING", "SPOT_INPUT_REQUIRED")
	if r.Spot != nil {
		if r.Spot.Vix9d == nil || r.Spot.Vix == nil {
			spot = vixUnavailable("MISSING", "BOTH_SPOT_INDICES_REQUIRED")
		} else if r.Spot.Vix9d.Symbol != "VIX9D" || r.Spot.Vix.Symbol != "VIX" {
			spot = vixUnavailable("INVALID", "WRONG_SPOT_INDEX_IDENTITY")
		} else {
			spot = vixCalculate(*r.Spot.Vix9d, *r.Spot.Vix, r, false)
		}
	}
	return vixReport{"vix-observation-report.rust.v1", "monthly-vx-and-spot-horizons.exact.v1", r.Mode, "UNVERIFIED", r.Cutoff, r.MaxAge, hash(r), vixFuture(r), spot}
}
func decodeVix(b []byte) VixRequest { var r VixRequest; decode(b, &r); validateVixPrices(r); return r }
func validateVixPrices(r VixRequest) {
	if r.Futures != nil {
		for _, c := range r.Futures.Calendar.Contracts {
			require(oneOf(c.Tenor, "MONTHLY", "WEEKLY"), "INVALID_INPUT")
		}
	}
	check := func(q VixQuote) {
		require(oneOf(q.Price.Kind, "SETTLEMENT", "INDEX", "MIDPOINT"), "INVALID_INPUT")
		if q.Price.Kind == "MIDPOINT" {
			require(q.Price.Value == nil, "INVALID_INPUT")
		} else {
			require(q.Price.Bid == nil && q.Price.Ask == nil, "INVALID_INPUT")
		}
	}
	if r.Futures != nil {
		for _, q := range r.Futures.Quotes {
			check(q)
		}
	}
	if r.Spot != nil {
		if r.Spot.Vix9d != nil {
			check(*r.Spot.Vix9d)
		}
		if r.Spot.Vix != nil {
			check(*r.Spot.Vix)
		}
	}
}
func vixResponse(b []byte) []byte { return vixRespond(decodeVix(b), b) }
func vixDemo(b []byte) []byte {
	var choice struct {
		Scenario string `json:"scenario"`
	}
	decode(b, &choice)
	data, e := sources.ReadFile("vix_demo.json")
	require(e == nil, "INTERNAL")
	r := decodeVix(data)
	f := r.Futures
	switch choice.Scenario {
	case "BACKWARDATION":
	case "CONTANGO":
		f.Quotes[0].Price.Value = ptr(decimal("18"))
	case "FLAT":
		f.Quotes[0].Price.Value = ptr(decimal("20"))
	case "MISSING":
		f.Quotes = f.Quotes[1:]
	case "STALE":
		r.Cutoff += r.MaxAge
	case "ASYNCHRONOUS":
		f.Quotes[1].Observed--
	case "ROLL":
		cutoff := f.Calendar.Contracts[0].Last
		f.Previous = &VixPrevious{r.Cutoff, "VX/V6", "VX/X6"}
		r.Cutoff = cutoff
		qs := []*VixQuote{}
		for i := range f.Quotes {
			qs = append(qs, &f.Quotes[i])
		}
		qs = append(qs, r.Spot.Vix9d, r.Spot.Vix)
		for _, q := range qs {
			q.Observed = cutoff - 1000
			q.Received = cutoff
			q.Source.Known = cutoff
			q.Session = "2026-10-21"
		}
	default:
		fail("INVALID_INPUT")
	}
	return vixRespond(r, b)
}
func vixRespond(r VixRequest, b []byte) []byte {
	report := vixAnalyze(r)
	pretty, e := json.MarshalIndent(r, "", "  ")
	require(e == nil, "INTERNAL")
	return rawJSON(struct {
		Report         vixReport `json:"report"`
		Request        string    `json:"request_json"`
		Transport      string    `json:"transport_sha256"`
		Code           string    `json:"code_sha256"`
		CargoLock      *string   `json:"cargo_lock_sha256"`
		Implementation string    `json:"implementation"`
	}{report, string(pretty), sha(b), embeddedSourceHash, nil, "vix-observation-native.go.v1"})
}
