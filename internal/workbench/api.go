package workbench

import (
	"encoding/json"
	"errors"
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
	return json.Marshal(value)
}
