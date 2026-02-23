# ZenQuant 3.0 Reference Implementation

This repository now contains a runnable MVP that implements the core architecture defined in the original `README.md`:

- Subject-based event streaming (`InMemoryJetStream`) with stream segmentation:
  - `STREAM_MARKET_DATA`
  - `STREAM_SIGNALS`
  - `STREAM_ORDERS`
- Protocol contracts in `/proto`:
  - `market_data.proto`
  - `strategy_signal.proto`
  - `execution.proto`
- Strategy subsystem:
  - Continuous factor signal generation (momentum-like factor)
  - Price Action analyzer state machine (`H1`, `H2`, `MTR`)
- Factor Lab subsystem:
  - Rolling IC/IR evaluator
  - Correlation-based orthogonalization penalty
  - Dynamic factor weight output
- Execution subsystem:
  - Deterministic order routing and idempotent execution reports
  - Risk guard integration before execution
- Risk subsystem:
  - Drawdown kill-switch
  - Position/notional/order-size constraints
  - Reduce-only validation
- Data platform:
  - ClickHouse DDL for trades, orderbook snapshots, factors, and correlation view
  - Deterministic stream replayer for backtest/live path consistency
- Web console:
  - FastAPI + WebSocket streaming endpoint
  - Trader-style dashboard (price stream, orderbook, signals, execution, factor weights)

## Run Demo

```bash
python -m zenquant.app.demo
```

or:

```bash
zenquant-demo
```

## Run Web Console

```bash
python3 -m pip install -e .
zenquant-web
```

Then open `http://127.0.0.1:8090`.

## Run Tests

```bash
python -m unittest discover -s tests -v
```

## Optional: Generate Python Protobuf Stubs

If `grpc_tools.protoc` is installed:

```bash
python -m grpc_tools.protoc -I="./proto" --python_out="./generated" "./proto/market_data.proto" "./proto/strategy_signal.proto" "./proto/execution.proto"
```
