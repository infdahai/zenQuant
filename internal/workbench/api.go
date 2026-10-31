package workbench

import (
	"bytes"
	"encoding/json"
	"errors"
	"strings"
)

// API adapts the local store to the server's bounded same-origin JSON interface.
func (s *Store) API(route string, body []byte) ([]byte, error) {
	var value any
	switch route {
	case "/api/research/import":
		x, err := s.Import(body)
		if err != nil {
			return nil, err
		}
		value = x
	case "/api/research/list":
		var req struct {
			Kind string `json:"kind"`
		}
		if err := strictDecode(body, &req); err != nil {
			return nil, err
		}
		x, err := s.List(req.Kind)
		if err != nil {
			return nil, err
		}
		if req.Kind == "result" {
			jobs, err := s.JobsList()
			if err != nil {
				return nil, err
			}
			allowed := map[string]bool{}
			for _, j := range jobs {
				if j.Status == "succeeded" {
					allowed[j.ResultID] = true
				}
			}
			filtered := x[:0]
			for _, id := range x {
				if allowed[id] || s.legacyResult(id) {
					filtered = append(filtered, id)
				}
			}
			x = filtered
		}
		value = x
	case "/api/research/snapshot":
		var req struct {
			ID string `json:"id"`
		}
		if err := strictDecode(body, &req); err != nil {
			return nil, err
		}
		x, err := s.Snapshot(req.ID)
		if err != nil {
			return nil, err
		}
		value = x
	case "/api/research/result":
		var req struct {
			ID string `json:"id"`
		}
		if err := strictDecode(body, &req); err != nil {
			return nil, err
		}
		if err := s.resultVisible(req.ID); err != nil {
			return nil, err
		}
		x, err := s.Result(req.ID)
		if err != nil {
			return nil, err
		}
		value = x
	case "/api/research/jobs":
		var req struct{}
		if err := strictDecode(body, &req); err != nil {
			return nil, err
		}
		x, err := s.JobsList()
		if err != nil {
			return nil, err
		}
		value = x
	case "/api/research/submit":
		var req struct {
			SnapshotID string  `json:"snapshot_id"`
			FeeRate    float64 `json:"fee_rate"`
		}
		if err := strictDecode(body, &req); err != nil {
			return nil, err
		}
		x, err := s.Submit(req.SnapshotID, req.FeeRate)
		if err != nil {
			return nil, err
		}
		value = x
	case "/api/research/cancel":
		var req struct {
			ID string `json:"id"`
		}
		if err := strictDecode(body, &req); err != nil {
			return nil, err
		}
		x, err := s.CancelJob(req.ID)
		if err != nil {
			return nil, err
		}
		value = x
	case "/api/research/compare":
		var req struct {
			A string `json:"a"`
			B string `json:"b"`
		}
		if err := strictDecode(body, &req); err != nil {
			return nil, err
		}
		if err := s.resultVisible(req.A); err != nil {
			return nil, err
		}
		if err := s.resultVisible(req.B); err != nil {
			return nil, err
		}
		a, err := s.Result(req.A)
		if err != nil {
			return nil, err
		}
		b, err := s.Result(req.B)
		if err != nil {
			return nil, err
		}
		text, err := Compare(a, b)
		if err != nil {
			return nil, err
		}
		value = map[string]string{"comparison": text}
	default:
		return nil, errors.New("unknown research route")
	}
	b, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(b))
	decoder.UseNumber()
	var tree any
	if err = decoder.Decode(&tree); err != nil {
		return nil, err
	}
	return json.Marshal(exactTimestampStrings(tree, ""))
}

func (s *Store) resultVisible(id string) error {
	jobs, err := s.JobsList()
	if err != nil {
		return err
	}
	for _, j := range jobs {
		if j.Status == "succeeded" && j.ResultID == id {
			return nil
		}
	}
	if s.legacyResult(id) {
		return nil
	}
	return errors.New("result is not linked to a succeeded job")
}
func (s *Store) legacyResult(id string) bool {
	r, err := s.Result(id)
	return err == nil && (r.Engine == "fixed-buy-first-sell-last-v1" || r.Engine == "fixed-buy-first-sell-last-v2")
}

// Browser JSON must not round nanosecond timestamps through JavaScript numbers.
func exactTimestampStrings(v any, key string) any {
	switch x := v.(type) {
	case map[string]any:
		for k, val := range x {
			x[k] = exactTimestampStrings(val, k)
		}
	case []any:
		for i, val := range x {
			x[i] = exactTimestampStrings(val, key)
		}
	case json.Number:
		if strings.HasSuffix(key, "_ns") {
			return x.String()
		}
	}
	return v
}
