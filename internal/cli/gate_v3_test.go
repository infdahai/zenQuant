package cli

import (
	"bytes"
	"encoding/json"
	"io"
	"strings"
	"testing"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/server"
)

func TestGateV3Evaluation(t *testing.T) {
	input := `{"episodes":[{"symbol":"BTC","side":1,"opened_at_ns":1,"net_pnl":10,"closed":true},{"symbol":"BTC","side":-1,"opened_at_ns":2,"net_pnl":-5,"closed":true}],"options":{"elapsed_days":0.5,"fill_profit_factor":0.1,"action_profit_factor":{"reduce":0.2}}}`
	var out bytes.Buffer
	if err := runGateV3(strings.NewReader(input), &out); err != nil {
		t.Fatal(err)
	}
	var result research.GateV3Result
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if !result.Passed || result.EpisodeProfitFactor.Value == nil || *result.EpisodeProfitFactor.Value != 2 || result.Cadence.Severity != research.GateSeveritySoft || result.Diagnostics.PositionSwitchCount != 1 || len(result.SoftFlags) != 1 || result.Diagnostics.ActionProfitFactor["reduce"] != 0.2 {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestGateV3RejectsInvalidInput(t *testing.T) {
	validEpisode := `{"symbol":"BTC","side":1,"opened_at_ns":1,"net_pnl":1,"closed":true}`
	tests := map[string]string{
		"empty": ``, "root null": `null`, "missing episodes": `{}`, "null episodes": `{"episodes":null}`,
		"unknown root": `{"episodes":[],"Episodes":[]}`, "unknown option": `{"episodes":[],"options":{"bogus":1}}`,
		"missing field":         `{"episodes":[{"symbol":"BTC","side":1,"opened_at_ns":1,"net_pnl":1}]}`,
		"null field":            `{"episodes":[{"symbol":"BTC","side":1,"opened_at_ns":1,"net_pnl":null,"closed":true}]}`,
		"unknown episode field": `{"episodes":[{"symbol":"BTC","side":1,"opened_at_ns":1,"net_pnl":1,"closed":true,"extra":1}]}`,
		"duplicate":             `{"episodes":[],"episodes":[]}`, "nested duplicate": `{"episodes":[],"options":{"action_profit_factor":{"a":1,"a":2}}}`,
		"trailing": `{"episodes":[]} {}`, "bad trailing": `{"episodes":[]} x`,
		"overflow": `{"episodes":[],"options":{"switches_per_day":1e999}}`, "underflow": `{"episodes":[],"options":{"switches_per_day":1e-999}}`,
		"int overflow":    `{"episodes":[{"symbol":"BTC","side":1,"opened_at_ns":9223372036854775808,"net_pnl":1,"closed":true}]}`,
		"invalid side":    `{"episodes":[{"symbol":"BTC","side":0,"opened_at_ns":1,"net_pnl":1,"closed":true}],"options":{"switches_per_day":0}}`,
		"missing cadence": `{"episodes":[]}`, "options null": `{"episodes":[],"options":null}`,
		"output overflow":   `{"episodes":[` + validEpisode + `,{"symbol":"BTC","side":-1,"opened_at_ns":2,"net_pnl":-5e-324,"closed":true}],"options":{"switches_per_day":0}}`,
		"oversized":         `{"episodes":[]}` + strings.Repeat(" ", server.BodyLimit),
		"deep nesting":      strings.Repeat("[", 10000) + "0" + strings.Repeat("]", 10000),
		"invalid utf8":      `{"episodes":[{"symbol":"` + string([]byte{0xff}) + `","side":1,"opened_at_ns":1,"net_pnl":1,"closed":true}],"options":{"switches_per_day":0}}`,
		"high surrogate":    `{"episodes":[{"symbol":"\ud800","side":1,"opened_at_ns":1,"net_pnl":1,"closed":true}],"options":{"switches_per_day":0}}`,
		"low surrogate key": `{"episodes":[],"options":{"action_profit_factor":{"\udc00":1},"switches_per_day":0}}`,
	}
	for name, input := range tests {
		t.Run(name, func(t *testing.T) {
			var out bytes.Buffer
			if err := runGateV3(strings.NewReader(input), &out); err == nil {
				t.Fatal("accepted invalid input")
			}
			if out.Len() != 0 {
				t.Fatalf("partial output: %s", out.String())
			}
		})
	}
}

func TestGateV3ValidUnicode(t *testing.T) {
	input := `{"episodes":[{"symbol":"\ud83d\ude80","side":1,"opened_at_ns":1,"net_pnl":1,"closed":true},{"symbol":"🚀","side":-1,"opened_at_ns":2,"net_pnl":-1,"closed":true}],"options":{"switches_per_day":0,"policy":"\\ud800"}}`
	var out bytes.Buffer
	if err := runGateV3(strings.NewReader(input), &out); err != nil {
		t.Fatal(err)
	}
	var result research.GateV3Result
	if err := json.Unmarshal(out.Bytes(), &result); err != nil || result.Diagnostics.PositionSwitchCount != 1 {
		t.Fatalf("unexpected output %s: %v", out.String(), err)
	}
}

func TestGateV3Arguments(t *testing.T) {
	if err := Run([]string{"gate-v3", "extra"}, io.Discard, io.Discard); err == nil {
		t.Fatal("accepted extra argument")
	}
}
