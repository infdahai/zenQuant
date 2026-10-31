package paper

import (
	"bytes"
	"math"
	"math/big"
	"regexp"
	"strings"
)

const commandSchema = "manual-paper-command.rust.v1"
const journalSchema = "manual-paper-journal.rust.v1"
const previewSchema = "manual-paper-preview.rust.v1"
const previewTTL uint64 = 300000

var idPattern = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,80}$`)
var shaPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

func emptyJournal() object { return object{"schema_version": journalSchema, "entries": []any{}} }
func settings(s object) (string, error) {
	if s["schema_version"] != "manual-paper-settings.rust.v1" || rat(s["total_budget_usd"]).Cmp(big.NewRat(100000, 1)) != 0 {
		return "", failure("InvalidInput")
	}
	allocated := new(big.Rat)
	allocations := []any{s["deribit_usdc"]}
	if s["binance_usdt"] != nil {
		allocations = append(allocations, obj(s["binance_usdt"])["allocation"])
	}
	for _, v := range allocations {
		if v == nil {
			continue
		}
		a := obj(v)
		amount, cap := rat(a["allocation_usd"]), rat(a["single_intent_cap_usd"])
		if amount.Sign() <= 0 || cap.Sign() <= 0 || cap.Cmp(amount) > 0 {
			return "", failure("InvalidInput")
		}
		c := a["conversion"]
		if c == nil || obj(c)["schema_version"] != "paper-fx-assumption.rust.v1" || obj(c)["confirmed"] != true {
			return "", failure("ConversionUnconfirmed")
		}
		allocated.Add(allocated, amount)
	}
	if s["binance_usdt"] != nil {
		b := obj(s["binance_usdt"])
		if b["margin_mode"] == nil || b["max_leverage"] == nil || rat(b["max_leverage"]).Cmp(big.NewRat(1, 1)) < 0 || rat(b["max_leverage"]).Cmp(big.NewRat(125, 1)) > 0 {
			return "", failure("RiskSettingsMissing")
		}
	}
	if allocated.Cmp(big.NewRat(100000, 1)) > 0 {
		return "", failure("OverAllocated")
	}
	return formatRat(new(big.Rat).Sub(big.NewRat(100000, 1), allocated)), nil
}
func validateIntent(i, s object, now uint64) error {
	if !idPattern.MatchString(str(i, "intent_id")) || rat(i["requested_budget_usd"]).Sign() <= 0 {
		return failure("InvalidInput")
	}
	for _, v := range obj(i["evidence"]) {
		if v == nil {
			continue
		}
		r := obj(v)
		if !shaPattern.MatchString(str(r, "sha256")) || num(r, "valid_until_ms") > 8640000000000000 || len(str(r, "source")) == 0 || len(str(r, "source")) > 256 || num(r, "observed_ms") > num(r, "available_ms") || num(r, "available_ms") > now || num(r, "valid_until_ms") <= num(r, "available_ms") {
			return failure("InvalidInput")
		}
	}
	instrument := obj(i["instrument"])
	p := obj(instrument["details"])
	var a object
	if instrument["product"] == "DERIBIT_LINEAR_USDC_OPTION_BUY" {
		if p["schema_version"] != "manual-deribit-option.rust.v1" || num(p, "expiry_ms") > 8640000000000000 || num(p, "expiry_ms") <= now || (p["underlying"] != "BTC" && p["underlying"] != "ETH") || rat(p["strike_usdc"]).Sign() <= 0 || rat(p["quantity_contracts"]).Sign() <= 0 || rat(p["limit_usdc_per_base"]).Sign() <= 0 {
			return failure("InvalidInput")
		}
		parts := strings.Split(str(p, "symbol"), "-")
		if len(parts) != 4 || parts[0] != str(p, "underlying")+"_USDC" || !idPattern.MatchString(parts[1]) || parts[3] != str(p, "option_type") {
			return failure("InvalidInput")
		}
		r, _, e := decimal(parts[2])
		if e != nil || r.Cmp(rat(p["strike_usdc"])) != 0 {
			return failure("InvalidInput")
		}
		if s["deribit_usdc"] == nil {
			return failure("NotConfigured")
		}
		a = obj(s["deribit_usdc"])
	} else {
		if s["binance_usdt"] == nil {
			return failure("NotConfigured")
		}
		b := obj(s["binance_usdt"])
		if p["schema_version"] != "manual-binance-usdm-perpetual.rust.v1" || p["symbol"] != "BTCUSDT" || rat(p["quantity_btc"]).Sign() <= 0 || rat(p["limit_usdt_per_btc"]).Sign() <= 0 {
			return failure("InvalidInput")
		}
		if b["margin_mode"] != p["margin_mode"] || rat(p["leverage"]).Cmp(big.NewRat(1, 1)) < 0 || b["max_leverage"] == nil || rat(p["leverage"]).Cmp(rat(b["max_leverage"])) > 0 {
			return failure("RiskSettingsMissing")
		}
		a = obj(b["allocation"])
	}
	if rat(i["requested_budget_usd"]).Cmp(rat(a["single_intent_cap_usd"])) > 0 {
		return failure("SingleIntentLimit")
	}
	return nil
}
func blockers(i object, now uint64) []any {
	b := []any{"NO_ORDER_OR_FILL_ENGINE", "ACCOUNT_RISK_UNVERIFIED", "INTENT_BUDGET_IS_NOT_A_RESERVATION"}
	fields := []string{"specification", "quote", "fees"}
	if obj(i["instrument"])["product"] == "BINANCE_USDM_BTCUSDT_PERPETUAL" {
		fields = append(fields, "mark", "funding", "margin")
	} else {
		b = append(b, "OPTION_CONTRACT_IDENTITY_AND_SETTLEMENT_UNVERIFIED")
	}
	for _, k := range fields {
		suffix := "UNVERIFIED"
		v := obj(i["evidence"])[k]
		if v == nil {
			suffix = "MISSING"
		} else if now >= num(obj(v), "valid_until_ms") {
			suffix = "STALE"
		}
		b = append(b, strings.ToUpper(k)+"_"+suffix)
	}
	return b
}
func apply(v, c object, now uint64) error {
	a := obj(c["action"])
	value := obj(a["value"])
	switch a["kind"] {
	case "CONFIGURE":
		u, e := settings(value)
		if e != nil {
			return e
		}
		v["settings"] = value
		v["unallocated_usd"] = u
	case "RECORD_INTENT":
		if v["settings"] == nil {
			return failure("NotConfigured")
		}
		if e := validateIntent(value, obj(v["settings"]), now); e != nil {
			return e
		}
		for _, r := range v["intents"].([]any) {
			if obj(obj(r)["intent"])["intent_id"] == value["intent_id"] {
				return failure("Conflict")
			}
		}
		v["intents"] = append(v["intents"].([]any), object{"record_request_id": c["request_id"], "intent": value, "intent_sha256": digest(value), "settings_sha256": digest(v["settings"]), "status": "RECORDED_UNASSESSED", "recorded_ms": now, "withdrawn_ms": nil})
	case "WITHDRAW_INTENT":
		for _, r := range v["intents"].([]any) {
			r := obj(r)
			if obj(r["intent"])["intent_id"] != value["intent_id"] {
				continue
			}
			if r["intent_sha256"] != value["intent_sha256"] {
				return failure("Conflict")
			}
			if r["withdrawn_ms"] != nil {
				return failure("AlreadyWithdrawn")
			}
			r["withdrawn_ms"] = now
			r["status"] = "WITHDRAWN_RECORD_ONLY"
			return nil
		}
		return failure("MissingIntent")
	}
	return nil
}
func materialize(j object) (object, error) {
	v := object{"settings": nil, "unallocated_usd": "100000", "intents": []any{}, "revision": len(j["entries"].([]any)), "sha256": digest(j), "total_budget_usd": "100000", "execution_authority": "NONE", "scope": "SIMULATED_CAPITAL_ALLOCATION_AND_MANUAL_RECORDS_ONLY"}
	for _, e := range j["entries"].([]any) {
		e := obj(e)
		if err := apply(v, obj(obj(e["preview"])["command"]), num(e, "confirmed_ms")); err != nil {
			return nil, err
		}
	}
	return v, nil
}
func validateJournal(j object) error {
	if j["schema_version"] != journalSchema || len(j["entries"].([]any)) > 256 {
		return failure("Corrupt")
	}
	prior := emptyJournal()
	for _, e := range j["entries"].([]any) {
		e := obj(e)
		next, replay, err := confirm(prior, object{"preview": e["preview"], "acknowledged_record_only": true}, num(e, "confirmed_ms"))
		if err != nil || replay {
			return failure("Corrupt")
		}
		prior = next
	}
	return nil
}
func previewDigest(p object) string {
	return digest([]any{previewSchema, p["command"], p["base_sha256"], p["created_ms"], p["expires_ms"]})
}
func preview(j, c object, now uint64) (object, error) {
	if c["schema_version"] != commandSchema || !idPattern.MatchString(str(c, "request_id")) {
		return nil, failure("InvalidInput")
	}
	for _, e := range j["entries"].([]any) {
		p := obj(obj(e)["preview"])
		if obj(p["command"])["request_id"] == c["request_id"] {
			if !bytes.Equal(canonical(p["command"]), canonical(c)) {
				return nil, failure("Conflict")
			}
			return p, nil
		}
	}
	v, e := materialize(j)
	if e != nil {
		return nil, e
	}
	if e = apply(v, c, now); e != nil {
		return nil, e
	}
	if now > math.MaxUint64-previewTTL {
		return nil, failure("InvalidInput")
	}
	p := object{"schema_version": previewSchema, "command": c, "base_sha256": digest(j), "created_ms": now, "expires_ms": now + previewTTL}
	p["sha256"] = previewDigest(p)
	return p, nil
}
func confirm(j, c object, now uint64) (object, bool, error) {
	fail := func(s string) (object, bool, error) { return nil, false, failure(s) }
	if c["acknowledged_record_only"] != true {
		return fail("InvalidInput")
	}
	p := obj(c["preview"])
	created := num(p, "created_ms")
	if p["schema_version"] != previewSchema || !shaPattern.MatchString(str(p, "base_sha256")) || created > math.MaxUint64-previewTTL || created+previewTTL != num(p, "expires_ms") || p["sha256"] != previewDigest(p) {
		return fail("InvalidPreview")
	}
	cmd := obj(p["command"])
	if cmd["schema_version"] != commandSchema || !idPattern.MatchString(str(cmd, "request_id")) {
		return fail("InvalidInput")
	}
	entries := j["entries"].([]any)
	for _, e := range entries {
		old := obj(obj(e)["preview"])["command"]
		if obj(old)["request_id"] == cmd["request_id"] {
			if !bytes.Equal(canonical(old), canonical(cmd)) {
				return fail("Conflict")
			}
			return j, true, nil
		}
	}
	if len(entries) >= 256 {
		return fail("Limit")
	}
	if len(entries) > 0 && now < num(obj(entries[len(entries)-1]), "confirmed_ms") {
		return fail("ClockRegression")
	}
	if now < created || now >= num(p, "expires_ms") || p["base_sha256"] != digest(j) {
		return fail("StalePreview")
	}
	v, e := materialize(j)
	if e != nil {
		return nil, false, e
	}
	if e = apply(v, cmd, now); e != nil {
		return nil, false, e
	}
	next := append([]any{}, entries...)
	next = append(next, object{"preview": p, "confirmed_ms": now})
	return object{"schema_version": journalSchema, "entries": next}, false, nil
}
