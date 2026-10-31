package paper

import (
	"encoding/json"
	"path/filepath"
	"testing"
)

// Exercise the API directly: browser controls are conveniences, not authority.
func TestUIBoundaryPaperWorkflow(t *testing.T) {
	s := NewStore(filepath.Join(tempRoot(t), "journal"))
	clock := func() uint64 { return 1800000000000 }
	api := func(route string, body any) (object, error) {
		b, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		result, err := s.api("/api/paper/"+route, b, clock, hooks{})
		if err != nil {
			return nil, err
		}
		decoded, err := decode(result, maxStoreBytes)
		if err != nil {
			t.Fatal(err)
		}
		return obj(decoded), nil
	}
	allocation := object{"allocation_usd": "60000", "single_intent_cap_usd": "1000", "conversion": object{"schema_version": "paper-fx-assumption.rust.v1", "assumption": "EXPERIMENTAL_USD_STABLECOIN_1_TO_1", "confirmed": true}}
	command := object{"schema_version": commandSchema, "request_id": "configure", "action": object{"kind": "CONFIGURE", "value": object{"schema_version": "manual-paper-settings.rust.v1", "total_budget_usd": "100000", "deribit_usdc": allocation, "binance_usdt": nil}}}
	allocation["allocation_usd"] = "100001"
	if _, err := api("preview", command); err == nil || err.Error() != "OverAllocated" {
		t.Fatalf("over-allocation: %v", err)
	}
	allocation["allocation_usd"] = "60000"
	result, err := api("preview", command)
	if err != nil {
		t.Fatal(err)
	}
	p := result["preview"]
	result, err = api("state", object{})
	if err != nil || obj(result["view"])["revision"] != json.Number("0") {
		t.Fatalf("preview changed state: %v %v", result, err)
	}
	if _, err = api("confirm", object{"preview": p, "acknowledged_record_only": false}); err == nil || err.Error() != "InvalidInput" {
		t.Fatalf("unacknowledged confirm: %v", err)
	}
	result, err = api("confirm", object{"preview": p, "acknowledged_record_only": true})
	if err != nil || obj(result["view"])["revision"] != json.Number("1") {
		t.Fatalf("confirm: %v %v", result, err)
	}
	result, err = api("confirm", object{"preview": p, "acknowledged_record_only": true})
	if err != nil || result["replayed"] != true || obj(result["view"])["revision"] != json.Number("1") {
		t.Fatalf("replay: %v %v", result, err)
	}
	allocation["single_intent_cap_usd"] = "2000"
	if _, err = api("preview", command); err == nil || err.Error() != "Conflict" {
		t.Fatalf("request ID reuse: %v", err)
	}
}
