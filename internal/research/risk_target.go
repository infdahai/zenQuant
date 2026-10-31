package research

import (
	"fmt"
	"math"
	"math/big"
	"sort"
)

// RiskTargetInput supplies the already computed risk cap, flags and effective
// price to the offline final sizing step. It does not evaluate risk conditions.
type RiskTargetInput struct {
	TargetQty       float64  `json:"target_qty"`
	TargetLeverage  float64  `json:"target_leverage"`
	CurrentPosition float64  `json:"current_position"`
	Equity          float64  `json:"equity"`
	LeverageCap     float64  `json:"leverage_cap"`
	Price           float64  `json:"price"`
	ReduceOnly      bool     `json:"reduce_only"`
	ReasonCodes     []string `json:"reason_codes"`
	Flags           []string `json:"flags"`
}

type RiskTargetResult struct {
	TargetQty      float64  `json:"target_qty"`
	TargetLeverage float64  `json:"target_leverage"`
	ReduceOnly     bool     `json:"reduce_only"`
	ReasonCodes    []string `json:"reason_codes"`
	Flags          []string `json:"flags"`
}

// ClampRiskTarget reproduces the final Python sizing step for nonnegative
// equity and leverage values and an effective price of at least 1e-9. Rounding
// can make the returned quantity exceed the unrounded quantity cap or the
// reduce-only position, and returned leverage exceed the unrounded leverage
// cap, without adding a flag. This is not a complete risk overlay or order
// authorization.
func ClampRiskTarget(in RiskTargetInput) (RiskTargetResult, error) {
	for _, value := range []float64{in.TargetQty, in.TargetLeverage, in.CurrentPosition, in.Equity, in.LeverageCap, in.Price} {
		if !sizingFinite(value) {
			return RiskTargetResult{}, fmt.Errorf("risk target inputs must be finite")
		}
	}
	if in.Equity < 0 || in.LeverageCap < 0 || in.TargetLeverage < 0 || in.Price < 1e-9 {
		return RiskTargetResult{}, fmt.Errorf("invalid risk target equity, leverage or effective price")
	}
	notional := in.Equity * in.LeverageCap
	if !sizingFinite(notional) {
		return RiskTargetResult{}, fmt.Errorf("risk target notional overflow")
	}
	limit := notional / in.Price
	if !sizingFinite(limit) {
		return RiskTargetResult{}, fmt.Errorf("risk target quantity limit overflow")
	}
	qty := math.Max(-limit, math.Min(limit, in.TargetQty))
	flags := append([]string{}, in.Flags...)
	if in.ReduceOnly {
		if in.CurrentPosition > 0 {
			qty = math.Max(0, math.Min(qty, in.CurrentPosition))
		} else if in.CurrentPosition < 0 {
			qty = math.Min(0, math.Max(qty, in.CurrentPosition))
		} else {
			qty = 0
		}
		if qty != in.TargetQty {
			flags = append(flags, "REDUCE_ONLY_BLOCK_ADD")
		}
	}
	reasons := sortedRiskStrings(append(append([]string{}, in.ReasonCodes...), flags...))
	flags = sortedRiskStrings(flags)
	if len(flags) == 0 {
		flags = []string{"OK"}
	}
	return RiskTargetResult{sizingRound(qty, 6), sizingRound(math.Min(in.TargetLeverage, in.LeverageCap), 4), in.ReduceOnly, reasons, flags}, nil
}

// ClampRiskTargetStrict applies conservative decimal quantization after the
// legacy clamp. It is a separate diagnostic contract, not order authorization.
func ClampRiskTargetStrict(in RiskTargetInput) (RiskTargetResult, error) {
	result, err := ClampRiskTarget(in)
	if err != nil {
		return RiskTargetResult{}, err
	}
	limit := in.Equity * in.LeverageCap / in.Price
	qty := math.Max(-limit, math.Min(limit, in.TargetQty))
	if in.ReduceOnly {
		if in.CurrentPosition > 0 {
			qty = math.Max(0, math.Min(qty, in.CurrentPosition))
		} else if in.CurrentPosition < 0 {
			qty = math.Min(0, math.Max(qty, in.CurrentPosition))
		} else {
			qty = 0
		}
	}
	result.TargetQty = quantizeRiskTowardZero(qty, 1000000)
	result.TargetLeverage = quantizeRiskTowardZero(math.Min(in.TargetLeverage, in.LeverageCap), 10000)
	return result, nil
}

// Use exact rational arithmetic so multiplication by the decimal scale cannot
// round a value across a boundary before truncation.
func quantizeRiskTowardZero(value float64, scale int64) float64 {
	r := new(big.Rat).SetFloat64(value)
	r.Mul(r, new(big.Rat).SetInt64(scale))
	integer := new(big.Int).Quo(r.Num(), r.Denom())
	quantized := new(big.Rat).SetFrac(integer, big.NewInt(scale))
	result, _ := quantized.Float64()
	if math.Abs(result) > math.Abs(value) {
		result = math.Nextafter(result, 0)
	}
	return result
}

func sortedRiskStrings(values []string) []string {
	sort.Strings(values)
	result := make([]string, 0, len(values))
	for _, value := range values {
		if len(result) == 0 || result[len(result)-1] != value {
			result = append(result, value)
		}
	}
	return result
}
