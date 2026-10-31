// Package paper implements manual declarations only; it has no execution authority.
package paper

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

type object = map[string]any

func failure(s string) error { return errors.New(s) }

// encoding/json replaces unpaired UTF-16 surrogates; Rust rejects them.
func validUnicodeEscapes(b []byte) bool {
	for i := 0; i < len(b); i++ {
		if b[i] != '\\' {
			continue
		}
		i++
		if i >= len(b) {
			return false
		}
		if b[i] != 'u' {
			continue
		}
		if i+4 >= len(b) {
			return false
		}
		n, e := strconv.ParseUint(string(b[i+1:i+5]), 16, 16)
		if e != nil {
			return false
		}
		i += 4
		if n >= 0xdc00 && n <= 0xdfff {
			return false
		}
		if n >= 0xd800 && n <= 0xdbff {
			if i+6 >= len(b) || b[i+1] != '\\' || b[i+2] != 'u' {
				return false
			}
			lo, e := strconv.ParseUint(string(b[i+3:i+7]), 16, 16)
			if e != nil || lo < 0xdc00 || lo > 0xdfff {
				return false
			}
			i += 6
		}
	}
	return true
}
func decode(b []byte, limit int) (any, error) {
	if len(b) > limit || !utf8.Valid(b) || !validUnicodeEscapes(b) {
		return nil, failure("InvalidInput")
	}
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	var read func(int) (any, error)
	read = func(depth int) (any, error) {
		if depth > 64 {
			return nil, failure("Limit")
		}
		t, e := d.Token()
		if e != nil {
			return nil, e
		}
		switch v := t.(type) {
		case json.Delim:
			if v == '{' {
				m := object{}
				for d.More() {
					k, e := d.Token()
					if e != nil {
						return nil, e
					}
					s, ok := k.(string)
					if !ok || len(s) > 4096 {
						return nil, failure("InvalidInput")
					}
					if _, ok = m[s]; ok {
						return nil, failure("DuplicateField")
					}
					x, e := read(depth + 1)
					if e != nil {
						return nil, e
					}
					m[s] = x
				}
				_, e = d.Token()
				return m, e
			}
			if v == '[' {
				a := []any{}
				for d.More() {
					x, e := read(depth + 1)
					if e != nil {
						return nil, e
					}
					a = append(a, x)
				}
				_, e = d.Token()
				return a, e
			}
			return nil, failure("InvalidInput")
		case string:
			if len(v) > 4096 {
				return nil, failure("Limit")
			}
			return v, nil
		case json.Number:
			if _, e := strconv.ParseUint(string(v), 10, 64); e != nil {
				return nil, failure("InvalidInput")
			}
			return v, nil
		default:
			return t, nil
		}
	}
	v, e := read(0)
	if e != nil {
		return nil, e
	}
	if _, e = d.Token(); e != io.EOF {
		return nil, failure("InvalidInput")
	}
	return v, nil
}

// canonical deliberately does not use encoding/json's HTML or U+2028 escaping.
func canonical(v any) []byte {
	var b bytes.Buffer
	var emit func(any)
	quote := func(s string) {
		b.WriteByte('"')
		for _, r := range s {
			switch r {
			case '"', '\\':
				b.WriteByte('\\')
				b.WriteRune(r)
			case '\b':
				b.WriteString(`\b`)
			case '\f':
				b.WriteString(`\f`)
			case '\n':
				b.WriteString(`\n`)
			case '\r':
				b.WriteString(`\r`)
			case '\t':
				b.WriteString(`\t`)
			default:
				if r < 32 {
					fmt.Fprintf(&b, `\u%04x`, r)
				} else {
					b.WriteRune(r)
				}
			}
		}
		b.WriteByte('"')
	}
	emit = func(v any) {
		switch x := v.(type) {
		case nil:
			b.WriteString("null")
		case bool:
			b.WriteString(strconv.FormatBool(x))
		case string:
			quote(x)
		case json.Number:
			b.WriteString(string(x))
		case uint64:
			b.WriteString(strconv.FormatUint(x, 10))
		case int:
			b.WriteString(strconv.Itoa(x))
		case object:
			keys := make([]string, 0, len(x))
			for k := range x {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			b.WriteByte('{')
			for i, k := range keys {
				if i > 0 {
					b.WriteByte(',')
				}
				quote(k)
				b.WriteByte(':')
				emit(x[k])
			}
			b.WriteByte('}')
		case []any:
			b.WriteByte('[')
			for i, v := range x {
				if i > 0 {
					b.WriteByte(',')
				}
				emit(v)
			}
			b.WriteByte(']')
		default:
			panic("internal canonical type")
		}
	}
	emit(v)
	return b.Bytes()
}
func digest(v any) string {
	b := append([]byte("exact_decimal.rust.v1\x00"), canonical(v)...)
	return fmt.Sprintf("%x", sha256.Sum256(b))
}
func decimal(s string) (*big.Rat, string, error) {
	if len(s) > 95 {
		return nil, "", failure("InvalidDecimal")
	}
	u := strings.TrimPrefix(s, "-")
	p := strings.Split(u, ".")
	if len(p) > 2 || u == "" {
		return nil, "", failure("InvalidDecimal")
	}
	frac := ""
	if len(p) == 2 {
		frac = p[1]
		if frac == "" {
			return nil, "", failure("InvalidDecimal")
		}
	}
	digits := p[0] + frac
	if digits == "" || len(frac) > 28 || len(strings.TrimLeft(digits, "0")) > 64 {
		return nil, "", failure("InvalidDecimal")
	}
	for _, c := range digits {
		if c < '0' || c > '9' {
			return nil, "", failure("InvalidDecimal")
		}
	}
	n, _ := new(big.Int).SetString(digits, 10)
	if strings.HasPrefix(s, "-") {
		n.Neg(n)
	}
	r := new(big.Rat).SetFrac(n, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(len(frac))), nil))
	return r, formatRat(r), nil
}
func formatRat(r *big.Rat) string {
	s := r.FloatString(28)
	s = strings.TrimRight(strings.TrimRight(s, "0"), ".")
	if s == "-0" {
		s = "0"
	}
	return s
}
func rat(v any) *big.Rat {
	r, _, e := decimal(v.(string))
	if e != nil {
		panic(e)
	}
	return r
}
func obj(v any) object              { return v.(object) }
func str(m object, k string) string { return m[k].(string) }
func num(m object, k string) uint64 {
	switch v := m[k].(type) {
	case uint64:
		return v
	case int:
		return uint64(v)
	default:
		n, _ := strconv.ParseUint(string(v.(json.Number)), 10, 64)
		return n
	}
}

// Shapes describe required fields; a leading ? permits null or omission, as Rust Option does.
var shapes = map[string]map[string]string{
	"command":      {"schema_version": "s", "request_id": "s", "action": "action"},
	"action":       {"kind": "CONFIGURE|RECORD_INTENT|WITHDRAW_INTENT", "value": "value"},
	"settings":     {"schema_version": "s", "total_budget_usd": "d", "deribit_usdc": "?allocation", "binance_usdt": "?account"},
	"allocation":   {"allocation_usd": "d", "single_intent_cap_usd": "d", "conversion": "?conversion"},
	"conversion":   {"schema_version": "s", "assumption": "EXPERIMENTAL_USD_STABLECOIN_1_TO_1", "confirmed": "b"},
	"account":      {"allocation": "allocation", "margin_mode": "?ISOLATED|CROSS", "max_leverage": "?d"},
	"intent":       {"intent_id": "s", "source": "MANUAL", "requested_budget_usd": "d", "instrument": "instrument", "evidence": "evidence"},
	"instrument":   {"product": "DERIBIT_LINEAR_USDC_OPTION_BUY|BINANCE_USDM_BTCUSDT_PERPETUAL", "details": "details"},
	"option":       {"schema_version": "s", "symbol": "s", "underlying": "s", "expiry_ms": "n", "option_type": "C|P", "strike_usdc": "d", "quantity_contracts": "d", "limit_usdc_per_base": "d"},
	"perpetual":    {"schema_version": "s", "symbol": "s", "side": "BUY|SELL", "quantity_btc": "d", "limit_usdt_per_btc": "d", "margin_mode": "ISOLATED|CROSS", "leverage": "d", "reduce_only": "b"},
	"evidence":     {"specification": "?ref", "quote": "?ref", "fees": "?ref", "mark": "?ref", "funding": "?ref", "margin": "?ref"},
	"ref":          {"sha256": "s", "source": "s", "kind": "USER_SUPPLIED_UNVERIFIED|SYNTHETIC_ENGINEERING_ONLY", "observed_ms": "n", "available_ms": "n", "valid_until_ms": "n"},
	"withdrawal":   {"intent_id": "s", "intent_sha256": "s"},
	"preview":      {"schema_version": "s", "command": "command", "base_sha256": "s", "created_ms": "n", "expires_ms": "n", "sha256": "s"},
	"confirmation": {"preview": "preview", "acknowledged_record_only": "b"},
	"journal":      {"schema_version": "s", "entries": "entries"},
	"entry":        {"preview": "preview", "confirmed_ms": "n"},
	"envelope":     {"journal": "journal", "sha256": "s"}, "empty": {},
}

func normalize(v any, kind string) (any, error) {
	if strings.HasPrefix(kind, "?") {
		if v == nil {
			return nil, nil
		}
		kind = kind[1:]
	}
	bad := failure("InvalidInput")
	switch kind {
	case "s":
		if _, ok := v.(string); ok {
			return v, nil
		}
	case "b":
		if _, ok := v.(bool); ok {
			return v, nil
		}
	case "n":
		if _, ok := v.(json.Number); ok {
			return v, nil
		}
	case "d":
		if s, ok := v.(string); ok {
			_, s, e := decimal(s)
			return s, e
		}
	case "entries":
		if a, ok := v.([]any); ok {
			if len(a) > 256 {
				return nil, failure("Limit")
			}
			for i, x := range a {
				n, e := normalize(x, "entry")
				if e != nil {
					return nil, e
				}
				a[i] = n
			}
			return a, nil
		}
	default:
		if shape, ok := shapes[kind]; ok {
			m, ok := v.(object)
			if !ok {
				return nil, bad
			}
			for k := range m {
				if _, ok := shape[k]; !ok {
					return nil, bad
				}
			}
			for k, t := range shape {
				if t == "value" {
					switch m["kind"] {
					case "CONFIGURE":
						t = "settings"
					case "RECORD_INTENT":
						t = "intent"
					case "WITHDRAW_INTENT":
						t = "withdrawal"
					default:
						return nil, bad
					}
				}
				if t == "details" {
					switch m["product"] {
					case "DERIBIT_LINEAR_USDC_OPTION_BUY":
						t = "option"
					case "BINANCE_USDM_BTCUSDT_PERPETUAL":
						t = "perpetual"
					default:
						return nil, bad
					}
				}
				n, e := normalize(m[k], t)
				if e != nil {
					return nil, e
				}
				m[k] = n
			}
			return m, nil
		}
		if s, ok := v.(string); ok {
			for _, allowed := range strings.Split(kind, "|") {
				if s == allowed {
					return s, nil
				}
			}
		}
	}
	return nil, bad
}
func parse(b []byte, limit int, kind string) (object, error) {
	v, e := decode(b, limit)
	if e != nil {
		return nil, e
	}
	v, e = normalize(v, kind)
	if e != nil {
		return nil, e
	}
	return obj(v), nil
}
