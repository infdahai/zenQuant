package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/big"
	"strconv"
	"unicode/utf8"

	"github.com/infdahai/zenQuant/internal/research"
	"github.com/infdahai/zenQuant/internal/server"
)

type gateV3Options struct {
	SwitchesPerDay         *float64           `json:"switches_per_day"`
	ElapsedDays            *float64           `json:"elapsed_days"`
	MinEpisodeProfitFactor *float64           `json:"min_episode_profit_factor"`
	Policy                 *string            `json:"policy"`
	FillProfitFactor       *float64           `json:"fill_profit_factor"`
	ActionProfitFactor     map[string]float64 `json:"action_profit_factor"`
}

type gateV3Request struct {
	Episodes []research.GateV3Episode `json:"episodes"`
	Options  gateV3Options            `json:"options"`
}

// validUnicodeEscapes follows the strict JSON boundary used by options and paper.
// encoding/json otherwise replaces unpaired surrogates with the replacement rune.
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
		n, err := strconv.ParseUint(string(b[i+1:i+5]), 16, 16)
		if err != nil {
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
			low, err := strconv.ParseUint(string(b[i+3:i+7]), 16, 16)
			if err != nil || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 6
		}
	}
	return true
}

// checkJSON enforces the CLI's finite JSON boundary, including diagnostic maps.
// Unlike the in-memory evaluator, it rejects non-finite and underflowing numbers.
func checkJSON(dec *json.Decoder, depth int) error {
	if depth > 16 {
		return errors.New("JSON nesting exceeds limit")
	}
	token, err := dec.Token()
	if err != nil {
		return err
	}
	switch v := token.(type) {
	case nil:
		return errors.New("null is not allowed")
	case json.Number:
		f, err := strconv.ParseFloat(string(v), 64)
		if err != nil || math.IsInf(f, 0) || math.IsNaN(f) {
			return errors.New("number is not representable")
		}
		if f == 0 {
			exact, _, err := big.ParseFloat(string(v), 10, 64, big.ToNearestEven)
			if err != nil || exact.Sign() != 0 {
				return errors.New("number is not representable")
			}
		}
	case json.Delim:
		switch v {
		case '{':
			seen := make(map[string]bool)
			for dec.More() {
				key, err := dec.Token()
				if err != nil {
					return err
				}
				name, ok := key.(string)
				if !ok {
					return errors.New("invalid object key")
				}
				if seen[name] {
					return fmt.Errorf("duplicate key %q", name)
				}
				seen[name] = true
				if err := checkJSON(dec, depth+1); err != nil {
					return err
				}
			}
			_, err = dec.Token()
			return err
		case '[':
			for dec.More() {
				if err := checkJSON(dec, depth+1); err != nil {
					return err
				}
			}
			_, err = dec.Token()
			return err
		default:
			return errors.New("unexpected JSON delimiter")
		}
	}
	return nil
}

func objectFields(raw json.RawMessage, allowed map[string]bool, required []string) (map[string]json.RawMessage, error) {
	var object map[string]json.RawMessage
	if err := json.Unmarshal(raw, &object); err != nil || object == nil {
		return nil, errors.New("expected object")
	}
	for key := range object {
		if !allowed[key] {
			return nil, fmt.Errorf("unknown field %q", key)
		}
	}
	for _, key := range required {
		if _, ok := object[key]; !ok {
			return nil, fmt.Errorf("missing required field %q", key)
		}
	}
	return object, nil
}

func runGateV3(in io.Reader, out io.Writer) error {
	data, err := io.ReadAll(io.LimitReader(in, server.BodyLimit+1))
	if err != nil {
		return err
	}
	if len(data) > server.BodyLimit {
		return errors.New("request exceeds limit")
	}
	if !utf8.Valid(data) || !validUnicodeEscapes(data) {
		return errors.New("invalid JSON Unicode")
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	if err := checkJSON(dec, 0); err != nil {
		return fmt.Errorf("invalid request: %w", err)
	}
	if _, err := dec.Token(); err != io.EOF {
		if err == nil {
			return errors.New("trailing JSON")
		}
		return fmt.Errorf("invalid trailing JSON: %w", err)
	}
	root, err := objectFields(data, map[string]bool{"episodes": true, "options": true}, []string{"episodes"})
	if err != nil {
		return err
	}
	var episodes []json.RawMessage
	if err := json.Unmarshal(root["episodes"], &episodes); err != nil || episodes == nil {
		return errors.New("episodes must be an array")
	}
	fields := map[string]bool{"symbol": true, "side": true, "opened_at_ns": true, "net_pnl": true, "closed": true}
	for i, episode := range episodes {
		if _, err := objectFields(episode, fields, []string{"symbol", "side", "opened_at_ns", "net_pnl", "closed"}); err != nil {
			return fmt.Errorf("episode %d: %w", i, err)
		}
	}
	if options, ok := root["options"]; ok {
		_, err := objectFields(options, map[string]bool{"switches_per_day": true, "elapsed_days": true, "min_episode_profit_factor": true, "policy": true, "fill_profit_factor": true, "action_profit_factor": true}, nil)
		if err != nil {
			return fmt.Errorf("options: %w", err)
		}
	}
	var request gateV3Request
	if err := json.Unmarshal(data, &request); err != nil {
		return fmt.Errorf("invalid request: %w", err)
	}
	o := request.Options
	result, err := research.EvaluateGateV3(request.Episodes, research.GateV3Options{
		SwitchesPerDay: o.SwitchesPerDay, ElapsedDays: o.ElapsedDays,
		MinEpisodeProfitFactor: o.MinEpisodeProfitFactor, Policy: o.Policy,
		FillProfitFactor: o.FillProfitFactor, ActionProfitFactor: o.ActionProfitFactor,
	})
	if err != nil {
		return err
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		return fmt.Errorf("result is not representable: %w", err)
	}
	encoded = append(encoded, '\n')
	_, err = out.Write(encoded)
	return err
}
