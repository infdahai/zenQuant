package options

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"reflect"
	"strconv"
	"strings"
	"unicode/utf8"
)

// Error is the stable machine-readable admission failure returned by Evaluate.
type Error struct {
	Code string `json:"code"`
}

func (e *Error) Error() string { return e.Code }
func fail(code string)         { panic(&Error{code}) }
func require(ok bool, code string) {
	if !ok {
		fail(code)
	}
}

type Dec struct{ r *big.Rat }

func decimal(s string) Dec {
	require(len(s) <= 95, "PRECISION_LIMIT")
	u := strings.TrimPrefix(s, "-")
	parts := strings.Split(u, ".")
	require(len(parts) <= 2 && u != "", "INVALID_DECIMAL")
	whole := parts[0]
	frac := ""
	if len(parts) == 2 {
		frac = parts[1]
		require(frac != "", "INVALID_DECIMAL")
	}
	require(whole != "" || len(parts) == 2, "INVALID_DECIMAL")
	for _, c := range whole + frac {
		require(c >= '0' && c <= '9', "INVALID_DECIMAL")
	}
	require(len(frac) <= 28 && len(strings.TrimLeft(whole+frac, "0")) <= 64, "PRECISION_LIMIT")
	if whole == "" {
		whole = "0"
	}
	n, ok := new(big.Int).SetString(whole+frac, 10)
	require(ok, "INVALID_DECIMAL")
	if strings.HasPrefix(s, "-") {
		n.Neg(n)
	}
	return bounded(new(big.Rat).SetFrac(n, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(len(frac))), nil)))
}
func bounded(r *big.Rat) Dec {
	require(len(new(big.Int).Abs(r.Num()).String()) <= 1024 && len(r.Denom().String()) <= 1024, "PRECISION_LIMIT")
	d := Dec{r}
	_ = d.String()
	return d
}
func (d Dec) String() string {
	if d.r == nil || d.r.Sign() == 0 {
		return "0"
	}
	n := new(big.Int).Abs(d.r.Num())
	den := d.r.Denom()
	q, rem := new(big.Int), new(big.Int)
	q.QuoRem(n, den, rem)
	s := q.String()
	if rem.Sign() != 0 {
		s += "."
		for i := 0; i < 256 && rem.Sign() != 0; i++ {
			rem.Mul(rem, big.NewInt(10))
			q.QuoRem(rem, den, rem)
			s += q.String()
		}
		require(rem.Sign() == 0, "NUMERICAL_LIMITATION")
	}
	if d.r.Sign() < 0 {
		s = "-" + s
	}
	return s
}
func (d Dec) MarshalJSON() ([]byte, error) { return json.Marshal(d.String()) }
func (d *Dec) UnmarshalJSON(b []byte) (err error) {
	defer catch(&err)
	var s string
	if json.Unmarshal(b, &s) != nil {
		fail("INVALID_INPUT")
	}
	*d = decimal(s)
	return
}
func (d Dec) rat() *big.Rat {
	if d.r == nil {
		return new(big.Rat)
	}
	return d.r
}
func (d Dec) cmp(e Dec) int { return d.rat().Cmp(e.rat()) }
func (d Dec) add(e Dec) Dec { return bounded(new(big.Rat).Add(d.rat(), e.rat())) }
func (d Dec) sub(e Dec) Dec { return bounded(new(big.Rat).Sub(d.rat(), e.rat())) }
func (d Dec) mul(e Dec) Dec { return bounded(new(big.Rat).Mul(d.rat(), e.rat())) }
func (d Dec) multiple(e Dec) bool {
	return e.cmp(Dec{}) != 0 && new(big.Rat).Quo(d.rat(), e.rat()).IsInt()
}
func min(a, b Dec) Dec {
	if a.cmp(b) < 0 {
		return a
	}
	return b
}
func catch(err *error) {
	if v := recover(); v != nil {
		if e, ok := v.(*Error); ok {
			*err = e
		} else {
			panic(v)
		}
	}
}
func rawJSON(v any) []byte {
	var b bytes.Buffer
	e := json.NewEncoder(&b)
	e.SetEscapeHTML(false)
	if e.Encode(v) != nil {
		fail("INTERNAL")
	}
	// serde_json emits Unicode line separators literally; encoding/json escapes
	// them even with HTML escaping disabled. Preserve literal backslash sequences.
	encoded := bytes.TrimSuffix(b.Bytes(), []byte("\n"))
	out := make([]byte, 0, len(encoded))
	for i := 0; i < len(encoded); i++ {
		if encoded[i] == '\\' && i+1 < len(encoded) {
			if i+6 <= len(encoded) && encoded[i+1] == 'u' && encoded[i+2] == '2' && encoded[i+3] == '0' && encoded[i+4] == '2' && (encoded[i+5] == '8' || encoded[i+5] == '9') {
				if encoded[i+5] == '8' {
					out = append(out, []byte(" ")...)
				} else {
					out = append(out, []byte(" ")...)
				}
				i += 5
				continue
			}
			out = append(out, encoded[i], encoded[i+1])
			i++
			continue
		}
		out = append(out, encoded[i])
	}
	return out
}
func sha(b []byte) string { return fmt.Sprintf("%x", sha256.Sum256(b)) }
func canonical(v any) []byte {
	b := rawJSON(v)
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	var x any
	if d.Decode(&x) != nil {
		fail("INVALID_INPUT")
	}
	return rawJSON(x)
}
func hash(v any) string  { return sha(append([]byte("exact_decimal.rust.v1\x00"), canonical(v)...)) }
func text(s string) bool { return strings.TrimSpace(s) != "" && len(s) <= 4096 }
func validHash(s string) bool {
	if len(s) != 64 {
		return false
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

func node(d *json.Decoder, depth int) any {
	require(depth <= 64, "LIMIT_EXCEEDED")
	t, err := d.Token()
	require(err == nil, "INVALID_JSON")
	switch v := t.(type) {
	case json.Delim:
		if v == '{' {
			m := map[string]any{}
			for d.More() {
				k, e := d.Token()
				require(e == nil, "INVALID_JSON")
				s, ok := k.(string)
				require(ok, "INVALID_JSON")
				require(len(s) <= 4096 && len(m) < 4096, "LIMIT_EXCEEDED")
				_, exists := m[s]
				require(!exists, "DUPLICATE_KEY")
				m[s] = node(d, depth+1)
			}
			t, e := d.Token()
			require(e == nil && t == json.Delim('}'), "INVALID_JSON")
			return m
		}
		require(v == '[', "INVALID_JSON")
		a := []any{}
		for d.More() {
			require(len(a) < 4096, "LIMIT_EXCEEDED")
			a = append(a, node(d, depth+1))
		}
		t, e := d.Token()
		require(e == nil && t == json.Delim(']'), "INVALID_JSON")
		return a
	case string:
		require(len(v) <= 4096, "LIMIT_EXCEEDED")
	case json.Number:
		if strings.HasPrefix(string(v), "-") {
			_, e := strconv.ParseInt(string(v), 10, 64)
			require(e == nil, "INVALID_JSON")
		} else {
			_, e := strconv.ParseUint(string(v), 10, 64)
			require(e == nil, "INVALID_JSON")
		}
	}
	return t
}

// Check shape before encoding/json: Go otherwise accepts absent required fields and null scalars.
func shape(x any, t reflect.Type) {
	if t.Kind() == reflect.Pointer {
		if x == nil {
			return
		}
		shape(x, t.Elem())
		return
	}
	require(x != nil, "INVALID_INPUT")
	if t == reflect.TypeOf(VixPrice{}) {
		m, ok := x.(map[string]any)
		require(ok, "INVALID_INPUT")
		kind, ok := m["kind"].(string)
		require(ok, "INVALID_INPUT")
		switch kind {
		case "MIDPOINT":
			_, value := m["value"]
			require(!value, "INVALID_INPUT")
		case "INDEX", "SETTLEMENT":
			_, bid := m["bid"]
			_, ask := m["ask"]
			require(!bid && !ask, "INVALID_INPUT")
		default:
			fail("INVALID_INPUT")
		}
	}
	if t == reflect.TypeOf(Dec{}) {
		_, ok := x.(string)
		require(ok, "INVALID_INPUT")
		return
	}
	switch t.Kind() {
	case reflect.Struct:
		m, ok := x.(map[string]any)
		require(ok, "INVALID_INPUT")
		known := map[string]bool{}
		for i := 0; i < t.NumField(); i++ {
			f := t.Field(i)
			name := f.Tag.Get("json")
			known[name] = true
			v, exists := m[name]
			require(exists || f.Type.Kind() == reflect.Pointer, "INVALID_INPUT")
			if exists {
				shape(v, f.Type)
				if s, ok := v.(string); ok {
					switch name {
					case "product":
						require(oneOf(s, "linear_option", "inverse_option"), "INVALID_INPUT")
					case "option_type":
						require(oneOf(s, "C", "P"), "INVALID_INPUT")
					case "exercise_style":
						require(oneOf(s, "EUROPEAN", "AMERICAN", "UNKNOWN"), "INVALID_INPUT")
					case "quantity_unit":
						require(oneOf(s, "base", "contracts"), "INVALID_INPUT")
					case "price_unit":
						require(oneOf(s, "quote_per_base", "quote_per_contract"), "INVALID_INPUT")
					case "settlement_rule":
						require(oneOf(s, "linear_cash", "linear_via_future", "inverse_via_future", "UNKNOWN"), "INVALID_INPUT")
					case "side":
						require(oneOf(s, "BUY", "SELL"), "INVALID_INPUT")
					case "provenance":
						provenance(s)
					}
				}
				if name == "count" {
					n, ok := v.(json.Number)
					require(ok && !strings.HasPrefix(string(n), "-"), "INVALID_INPUT")
				}
			}
		}
		for k := range m {
			require(known[k], "INVALID_INPUT")
		}
	case reflect.Array, reflect.Slice:
		a, ok := x.([]any)
		require(ok, "INVALID_INPUT")
		if t.Kind() == reflect.Array {
			require(len(a) == t.Len(), "INVALID_INPUT")
		}
		for _, v := range a {
			shape(v, t.Elem())
		}
	case reflect.String:
		_, ok := x.(string)
		require(ok, "INVALID_INPUT")
	case reflect.Bool:
		_, ok := x.(bool)
		require(ok, "INVALID_INPUT")
	default:
		_, ok := x.(json.Number)
		require(ok, "INVALID_INPUT")
	}
}
func decode(b []byte, out any) {
	require(len(b) <= 1048576, "LIMIT_EXCEEDED")
	require(utf8.Valid(b) && validUnicodeEscapes(b), "INVALID_JSON")
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	x := node(d, 0)
	_, err := d.Token()
	require(err == io.EOF, "INVALID_JSON")
	shape(x, reflect.TypeOf(out).Elem())
	require(json.Unmarshal(rawJSON(x), out) == nil, "INVALID_INPUT")
}
func oneOf(s string, vs ...string) bool {
	for _, v := range vs {
		if s == v {
			return true
		}
	}
	return false
}
func ptr[T any](v T) *T { return &v }
