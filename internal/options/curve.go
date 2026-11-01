package options

import (
	"embed"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/infdahai/zenQuant/internal/localfile"
)

// Embed native implementation bytes so provenance follows the binary, never the checkout.
//
//go:embed *.go vix_demo.json
var sources embed.FS

func sourceHash() string {
	entries, _ := sources.ReadDir(".")
	names := []string{}
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), "_test.go") {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	pairs := [][2]string{}
	for _, name := range names {
		b, _ := sources.ReadFile(name)
		pairs = append(pairs, [2]string{name, sha(b)})
	}
	// Snapshot admission uses these helpers outside the options package.
	// Keep their bytes in the same code identity as the admission/evaluation code.
	pairs = append(pairs, localfile.SourceHashes()...)
	return hash(pairs)
}

// Embedded source bytes are immutable for the lifetime of the binary.
var embeddedSourceHash = sourceHash()

type Response struct {
	Report          Report `json:"report"`
	RequestJSON     string `json:"request_json"`
	CalculationUS   int64  `json:"calculation_us"`
	TransportSHA256 string `json:"transport_sha256"`
	CodeSHA256      string `json:"code_sha256"`
	// Retained for the Rust wire contract. Null explicitly means this native binary has no Cargo lock.
	CargoLockSHA256 *string `json:"cargo_lock_sha256"`
	Implementation  string  `json:"implementation"`
}

// Evaluate is the native browser API. It admits only the two explicit curve routes,
// with no file, network, clock-dependent pricing, account or execution side effects.
func Evaluate(path string, body []byte) (result []byte, err error) {
	defer catch(&err)
	if path == "/api/vix/evaluate" {
		return vixResponse(body), nil
	}
	if path == "/api/vix/demo" {
		return vixDemo(body), nil
	}
	var r Request
	switch path {
	case "/api/curve":
		decode(body, &r)
	case "/api/synthetic":
		var p syntheticParameters
		decode(body, &p)
		r = synthetic(p)
	default:
		fail("INVALID_INPUT")
	}
	start := time.Now()
	report := analyze(r)
	elapsed := time.Since(start).Microseconds()
	pretty, e := json.MarshalIndent(r, "", "  ")
	require(e == nil, "INTERNAL")
	return rawJSON(Response{report, string(pretty), elapsed, sha(body), embeddedSourceHash, nil, "options-payoff-native.go.v1"}), nil
}

func terminal(r Request, scenarios TerminalScenarios) TerminalDiagnostic {
	c := r.Candidate
	s := c.Spec
	tr := TerminalDiagnosticRequest{"options-terminal-diagnostic-request.rust.v1", r.RequestId, r.CutoffNs, r.ReportCurrency, c.Leg, s, c.MetadataKnownNs, scenarios, c.Quotes, c.TerminalFee, c.SourceIdentities}
	out := TerminalDiagnostic{RequestId: r.RequestId, InputHash: hash(tr), SpecHash: hash(s), ScenarioHash: hash(scenarios), ModelVersion: "single_paid_long_terminal.rust.v1", Support: SupportColumns{
		status("VALID", "ADMITTED_TERMINAL_DIAGNOSTIC"), status("NOT_EVALUABLE", "NOT_EVALUATED"), status("NOT_EVALUABLE", "FUNDING_NOT_ASSESSED"), status("UNVERIFIED", "NO_ACCOUNT_EVIDENCE"), status("UNVERIFIED", "NO_EXECUTION_EVIDENCE")}, NetStatus: status("NOT_EVALUABLE", "NOT_EVALUATED"), Outcomes: []ScenarioOutcome{}, Reasons: []string{"CONDITIONAL_MATHEMATICS_NO_FUNDING_OR_FILL", "NO_PROBABILITIES_OR_EV_MODEL"}}
	if q := selectedQuote(c.Quotes, r.CutoffNs); q != nil {
		out.SelectedQuoteHash = ptr(hash(q))
		out.Cost = ptr(quoteCost(*q, c.Leg, r.CutoffNs, r.ReportCurrency))
	}
	for _, p := range scenarios.Scenarios {
		out.Outcomes = append(out.Outcomes, ScenarioOutcome{ScenarioId: p.ScenarioId, Currency: r.ReportCurrency, Reason: "NOT_EVALUATED"})
	}
	gross := []Dec{}
	model := diagnostic(func() {
		need(c.Leg.Side == "BUY", "UNSUPPORTED", "SINGLE_LONG_LEG_REQUIRED")
		need(c.MetadataKnownNs != nil && *c.MetadataKnownNs <= r.CutoffNs && s.ValidFromNs <= r.CutoffNs && (s.ValidUntilNs == nil || s.ExpiryNs < *s.ValidUntilNs), "NOT_EVALUABLE", "TERMINAL_SPEC_KNOWLEDGE_OR_VALID_DOMAIN_MISSING")
		need(s.ExpiryNs > r.CutoffNs, "UNSUPPORTED", "EXPIRED_MODEL_DOMAIN")
		need(s.Product == "linear_option" && s.ExerciseStyle == "EUROPEAN" && (s.SettlementRule == "linear_cash" || s.SettlementRule == "linear_via_future"), "UNSUPPORTED", "LINEAR_EUROPEAN_CASH_RULE_REQUIRED")
		need(s.QuoteCurrency == r.ReportCurrency && s.StrikeCurrency == r.ReportCurrency && s.SettlementCurrency == r.ReportCurrency && s.MarginCurrency == r.ReportCurrency, "UNSUPPORTED", "TERMINAL_SAME_CURRENCY_REQUIRED")
		index := strings.TrimSpace(s.SettlementIndex)
		need(index != "" && !strings.EqualFold(index, "UNKNOWN") && !strings.EqualFold(index, "UNVERIFIED"), "NOT_EVALUABLE", "SETTLEMENT_FIXING_UNVERIFIED")
		for _, p := range scenarios.Scenarios {
			need(p.AtNs == s.ExpiryNs, "UNSUPPORTED", "EVERY_SCENARIO_MUST_EQUAL_EXPIRY")
		}
		base := legalQuantity(s, c.Leg.QuantityNative)
		for _, p := range scenarios.Scenarios {
			x := p.DeliveryPrice.sub(s.Strike)
			if s.OptionType == "P" {
				x = s.Strike.sub(p.DeliveryPrice)
			}
			if x.cmp(Dec{}) < 0 {
				x = Dec{}
			}
			gross = append(gross, x.mul(base))
		}
	})
	if model.Status != "VALID" {
		out.Support.ModelSupport = model
		out.NetStatus = model
		for i := range out.Outcomes {
			out.Outcomes[i].Reason = model.Reason
		}
		return out
	}
	out.Support.ModelSupport = status("VALID", "SINGLE_LONG_TERMINAL_CONDITIONAL_MODEL")
	for i, v := range gross {
		out.Outcomes[i].GrossPayoff = ptr(v)
		out.Outcomes[i].Reason = "GROSS_TERMINAL_CONDITIONAL_MODEL"
	}
	net := []Dec{}
	out.NetStatus = diagnostic(func() {
		need(out.Cost != nil, "NOT_EVALUABLE", "MISSING_VISIBLE_EXACT_SPEC_QUOTE")
		if out.Cost.Status.Status != "VALID" {
			panic(out.Cost.Status)
		}
		need(out.Cost.TotalReportCurrency != nil, "NOT_EVALUABLE", "INCOMPLETE_OPENING_COST")
		need(c.TerminalFee != nil, "NOT_EVALUABLE", "MISSING_TERMINAL_FEE_ASSUMPTION")
		need(c.TerminalFee.KnownNs <= r.CutoffNs, "NOT_EVALUABLE", "TERMINAL_FEE_NOT_YET_KNOWN")
		paid := out.Cost.TotalReportCurrency.add(c.TerminalFee.AmountReport)
		for _, v := range gross {
			net = append(net, v.sub(paid))
		}
	})
	if out.NetStatus.Status == "VALID" {
		out.NetStatus.Reason = "CONDITIONAL_NET_ONLY_NO_FUNDING_OR_FILL"
		for i, v := range net {
			out.Outcomes[i].ConditionalModelPnl = ptr(v)
			out.Outcomes[i].Reason = "CONDITIONAL_TERMINAL_PNL_NOT_ACTUAL"
		}
	}
	return out
}
func analyze(r Request) Report {
	validate(r)
	out := Report{SchemaVersion: "options-payoff-curve-report.rust.v1", ReportCurrency: r.ReportCurrency, CutoffNs: strconv.FormatUint(r.CutoffNs, 10), InputHash: hash(r), SignalStatus: status("NOT_EVALUABLE", "NO_SIGNAL_INDEPENDENT_OPTIONS_ANALYSIS"), Missing: []string{}, Points: []Point{}, Limitations: []string{"EXPIRY_SCENARIOS_NOT_BACKTEST_OR_CURRENT_MARK", "NO_PROBABILITIES_OR_EXPECTED_RETURN", "NO_ACCOUNT_RISK_OR_FILL_AUTHORITY", "COSTS_CONDITIONAL_ON_SUPPLIED_QUOTE_NO_SLIPPAGE_FORECAST"}}
	if s := r.Signal; s != nil {
		out.SignalHash = ptr(hash(s))
		switch {
		case s.KnownNs > r.CutoffNs:
			out.SignalStatus = status("NOT_EVALUABLE", "SIGNAL_NOT_YET_KNOWN")
		case r.CutoffNs >= s.ValidUntilNs:
			out.SignalStatus = status("NOT_EVALUABLE", "SIGNAL_EXPIRED")
		default:
			out.SignalStatus = status("UNVERIFIED", "MANUAL_HYPOTHESIS_NOT_PREDICTIVE_EVIDENCE")
		}
	}
	if c := r.Candidate; c != nil {
		out.ExpiryNs = ptr(strconv.FormatUint(c.Spec.ExpiryNs, 10))
		if c.TerminalFee != nil {
			out.TerminalFeeAssumption = ptr(c.TerminalFee.AmountReport)
		}
	} else {
		out.Missing = append(out.Missing, "CANDIDATE_AND_CONTRACT")
	}
	if r.Grid != nil {
		out.GridHash = ptr(hash(r.Grid))
	} else {
		out.Missing = append(out.Missing, "EXPLICIT_PRICE_SCENARIOS")
	}
	if r.Candidate == nil || r.Grid == nil {
		return out
	}
	g := r.Grid
	price := g.First
	for len(out.Points) < g.Count {
		count := g.Count - len(out.Points)
		if count > 64 {
			count = 64
		}
		scenarios := TerminalScenarios{g.Meta, g.SpecHash, []TerminalScenario{}}
		for i := 0; i < count; i++ {
			scenarios.Scenarios = append(scenarios.Scenarios, TerminalScenario{fmt.Sprintf("price-%d", len(out.Points)+i), g.AtNs, price})
			if len(out.Points)+i+1 < g.Count {
				price = price.add(g.Step)
			}
		}
		d := terminal(r, scenarios)
		if out.Diagnostic != nil {
			require(out.Diagnostic.Support == d.Support && out.Diagnostic.NetStatus == d.NetStatus, "NUMERICAL_LIMITATION")
		}
		for i, p := range d.Outcomes {
			out.Points = append(out.Points, Point{scenarios.Scenarios[i].DeliveryPrice, p.GrossPayoff, p.ConditionalModelPnl})
		}
		if out.Diagnostic == nil {
			d.Outcomes = []ScenarioOutcome{}
			out.Diagnostic = &d
		}
	}
	return out
}
