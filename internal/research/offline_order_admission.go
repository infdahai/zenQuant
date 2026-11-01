package research

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
)

// offlineOrderAdmission applies the Python router's final post-sizing guard.
// The simulator remains stateless; only validated reports advance inventory and budget.
type offlineOrderAdmission struct {
	maxQty    float64
	positions map[string]float64
	filled    map[admissionKey]float64
}
type admissionKey struct {
	symbol string
	cutoff int64
}

func decimalRat(v float64) *big.Rat {
	r, _ := new(big.Rat).SetString(strconv.FormatFloat(v, 'f', -1, 64))
	return r
}
func decimalAdd(a, b float64) float64 {
	r := new(big.Rat).Add(decimalRat(a), decimalRat(b))
	v, _ := r.Float64()
	if (a > 0 && b < 0 || a < 0 && b > 0) && math.Abs(v) <= 4*math.Max(quantityULP(a), quantityULP(b)) {
		return 0
	}
	return v
}

// quantityULP matches math.ulp for finite Python floats, including subnormals.
func quantityULP(v float64) float64 {
	_, exponent := math.Frexp(math.Abs(v))
	return math.Max(math.SmallestNonzeroFloat64, math.Ldexp(1, exponent-53))
}

func (a *offlineOrderAdmission) admit(order OfflineExecutionOrder, cutoff int64) (OfflineExecutionOrder, bool, error) {
	if !finite(order.Quantity) || !finite(order.MaxSlippageBPS) || (order.Side != 1 && order.Side != -1) {
		return order, false, fmt.Errorf("invalid order quantity, slippage or side")
	}
	if (order.OrderType != "" && order.OrderType != "MARKET") || order.LimitPrice != nil {
		return order, false, fmt.Errorf("limit order semantics are unsupported")
	}
	if order.Quantity <= 0 {
		return order, false, nil
	}
	requested := order.Quantity
	remaining := math.Max(0, decimalAdd(a.maxQty, -a.filled[admissionKey{order.Symbol, cutoff}]))
	requested = math.Min(requested, remaining)
	if order.ReduceOnly {
		position := a.positions[order.Symbol]
		if position*float64(order.Side) >= 0 {
			return order, false, nil
		}
		requested = math.Min(requested, math.Abs(position))
	}
	if requested <= 0 {
		return order, false, nil
	}
	// Decimal ROUND_DOWN to Python's default six quantity places.
	scaled := new(big.Rat).Mul(decimalRat(requested), big.NewRat(1000000, 1))
	units := new(big.Int).Quo(scaled.Num(), scaled.Denom())
	quantized := new(big.Rat).SetFrac(units, big.NewInt(1000000))
	order.Quantity, _ = quantized.Float64()
	if order.Quantity < .0001 {
		return order, false, nil
	}
	return order, true, nil
}
func (a *offlineOrderAdmission) record(report ExecutionReport, cutoff int64) {
	if report.FilledQty <= 0 {
		return
	}
	a.positions[report.Symbol] = decimalAdd(a.positions[report.Symbol], float64(report.Side)*report.FilledQty)
	key := admissionKey{report.Symbol, cutoff}
	a.filled[key] = decimalAdd(a.filled[key], report.FilledQty)
}
