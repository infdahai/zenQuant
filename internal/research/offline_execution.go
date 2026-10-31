package research

import "fmt"

// OfflineExecutionOrder represents the Python gateway's order inputs. Risk and
// slippage controls are not modeled; this is not a live execution gateway.
type OfflineExecutionOrder struct {
	ClientOrderID  string   `json:"client_order_id"`
	Symbol         string   `json:"symbol"`
	Side           int      `json:"side"`
	Quantity       float64  `json:"quantity"`
	ReduceOnly     bool     `json:"reduce_only"`
	MaxSlippageBPS float64  `json:"max_slippage_bps"`
	OrderType      string   `json:"order_type"`
	LimitPrice     *float64 `json:"limit_price"`
}

// SimulateOfflineExecution fills at mark with the supplied taker fee rate.
// Unlike Python, the caller must provide a timestamp (zero is preserved), all
// numeric inputs, notional and fee must be finite, the fee rate must be
// nonnegative, and side must be +1 or -1. Unlike Python, reduce-only orders,
// non-market types and limit prices return errors rather than being ignored.
// Slippage is not enforced. Finite nonpositive quantity or mark retains
// Python's invalid_order report. No bid/ask, exchange or live order is involved.
func SimulateOfflineExecution(order OfflineExecutionOrder, markPrice, takerFeeRate float64, timestampNS int64) (ExecutionReport, error) {
	if !finite(order.Quantity) || !finite(order.MaxSlippageBPS) || !finite(markPrice) || !finite(takerFeeRate) || takerFeeRate < 0 {
		return ExecutionReport{}, fmt.Errorf("quantity, mark price and nonnegative taker fee rate must be finite")
	}
	if order.ReduceOnly || (order.OrderType != "" && order.OrderType != "MARKET") || order.LimitPrice != nil {
		return ExecutionReport{}, fmt.Errorf("reduce-only and limit order semantics are unsupported")
	}
	if order.Side != 1 && order.Side != -1 {
		return ExecutionReport{}, fmt.Errorf("order side must be +1 or -1")
	}
	report := ExecutionReport{ClientOrderID: order.ClientOrderID, Symbol: order.Symbol, Side: order.Side, TransactionTimeNS: timestampNS}
	if order.Quantity <= 0 || markPrice <= 0 {
		report.Status = "REJECTED"
		report.RejectReason = "invalid_order"
		return report, nil
	}
	notional := order.Quantity * markPrice
	if !finite(notional) {
		return ExecutionReport{}, fmt.Errorf("execution notional must be finite")
	}
	fee := notional * takerFeeRate
	if !finite(fee) {
		return ExecutionReport{}, fmt.Errorf("execution fee must be finite")
	}
	report.Status = "FILLED"
	report.FilledQty = order.Quantity
	report.AvgPrice = markPrice
	report.Fee = fee
	return report, nil
}
