package options

import (
	"strings"
	"testing"
)

func TestStrictUnicode(t *testing.T) {
	// Construct escape bytes explicitly to distinguish them from Unicode source text.
	slash := string(byte(92))
	prefix := `{"schema_version":"options-payoff-curve-request.rust.v1","request_id":"`
	suffix := `","cutoff_ns":10,"report_currency":"USDC","candidate":null,"grid":null,"signal":null}`
	for _, escaped := range []string{slash + "ud800", slash + "udfff", slash + "ud800" + slash + "u0041"} {
		errorCode(t, "/api/curve", []byte(prefix+"a"+escaped+"b"+suffix), "INVALID_JSON")
	}
	for _, escaped := range []string{slash + "ud83d" + slash + "ude00", slash + slash + "ud800"} {
		if _, err := Evaluate("/api/curve", []byte(prefix+escaped+suffix)); err != nil {
			t.Fatal(err)
		}
	}
	literal := slash + "u2028"
	encoded := rawJSON("x" + string(rune(0x2028)) + string(rune(0x2029)) + literal)
	if !strings.Contains(string(encoded), string(rune(0x2028))) || !strings.Contains(string(encoded), slash+slash+"u2028") {
		t.Fatalf("incorrect canonical Unicode: %s", encoded)
	}
}
