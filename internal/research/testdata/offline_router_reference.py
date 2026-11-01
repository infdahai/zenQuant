"""Regenerate offline_router_reference.json from the Python PR55 router.

Run from the repository root: PYTHONPATH=. python3 internal/research/testdata/offline_router_reference.py
The inputs are synthetic, already-sized orders. This exercises the router's final
submission guard, session, gateway, and risk filled-position accounting, not its
strategy sizing or flip orchestration.
"""
import json
from dataclasses import asdict
from pathlib import Path

from zenquant.domain import ExecutionOrder
from zenquant.domain.quantity import add_filled_quantity
from zenquant.execution.gateway import ExecutionGateway, ExecutionGatewayConfig
from zenquant.execution.router import ExecutionKernel, ExecutionKernelConfig
from zenquant.infra.event_bus import InMemoryJetStream
from zenquant.risk.guard import RiskOverlay

bus = InMemoryJetStream()
risk = RiskOverlay(bus)
kernel = ExecutionKernel(bus, ExecutionGateway(ExecutionGatewayConfig(taker_fee_rate=0.001)), risk,
                         ExecutionKernelConfig(max_trade_qty_per_bar=1.0))
inputs = [
    (1, 'BTC', 1, 0.8, False),
    (1, 'BTC', 1, 0.8, False),  # remaining cutoff budget
    (1, 'BTC', -1, 1.0, True), # exhausted budget
    (1, 'ETH', 1, 0.4, False), # independent symbol budget
    (2, 'BTC', -1, 0.35, True), # partial close
    (2, 'BTC', 1, 0.5, True),  # reduce-only add blocked
    (3, 'BTC', -1, 2.0, True), # flatten, never reverse
    (3, 'BTC', -1, 1.0, False), # only remaining budget can open
    (4, 'BTC', 1, 0.6, False), # one-fill flip
    (5, 'BTC', -1, 0.0000999, False), # minimum after rounding
    (5, 'BTC', -1, 0.1234569, False), # decimal rounding down
]
rows = []
for index, (cutoff, symbol, side, qty, reduce_only) in enumerate(inputs):
    order = ExecutionOrder(client_order_id=str(index), symbol=symbol, side=side,
                           quantity=qty, reduce_only=reduce_only, max_slippage_bps=8.0)
    report = kernel._submit_order(order, mark_price=100.0, now_ns=cutoff)
    if report and report.filled_qty:
        kernel._positions[symbol] = add_filled_quantity(kernel._positions.get(symbol, 0.0), side * report.filled_qty)
        risk.apply_fee(report.fee)
    rows.append({'cutoff': cutoff, 'order': asdict(order), 'report': asdict(report) if report else None,
                 'position': kernel._positions.get(symbol, 0.0), 'risk_position': risk._actual_positions.get(symbol, 0.0)})
Path('internal/research/testdata/offline_router_reference.json').write_text(json.dumps({'cap': 1.0, 'rows': rows}, indent=2) + '\n')
