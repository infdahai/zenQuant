# Forward Collection Automation

This repo now contains three separate layers for hourly forward data operations:

1. collector execution
2. health-only inspection
3. failure alerting semantics

The repo provides job wrappers and health contracts. The scheduler itself is still installed separately on the host.

## Job Wrappers

- `/Users/disaster/workspace/code/zenQuant/scripts/run_forward_collection_job.sh`
  - runs `uv run zenquant-forward-collect`
  - writes `results/data_readiness/forward_collection_latest.json`
  - optionally collects Hyperliquid public whale positions when `config/hyperliquid_whales.toml` exists
- `/Users/disaster/workspace/code/zenQuant/scripts/run_forward_collection_health_job.sh`
  - runs `uv run zenquant-forward-collect --health-only`
  - writes `results/data_readiness/forward_collection_health.json`

## Failure Semantics

Health-only failures are produced by `/Users/disaster/workspace/code/zenQuant/zenquant/app/forward_data_collect.py`.

Current failure reasons include:

- `readiness_missing`
- `readiness_stale`
- `deribit_stale`
- `deribit_block_flow_stale`
- `hyperliquid_status_missing`
- `hyperliquid_stale`
- `hyperliquid_blocked`
- `merged_liquidation_missing`
- `merged_liquidation_stale`
- `readiness_blocked_persistent`

`readiness_blocked_persistent` only fires after the same readiness blocker survives two consecutive health cycles.

## Hyperliquid Whale Positions

Whale-position collection is watchlist based. Copy `config/hyperliquid_whales.example.toml` to
`config/hyperliquid_whales.toml`, add reviewed public account addresses, and keep disabled placeholders out of the
active list.

Outputs:

- `data/raw/hyperliquid/whale_positions/accounts/<date>/accounts_<timestamp>.parquet`
- `data/raw/hyperliquid/whale_positions/positions/<date>/positions_<timestamp>.parquet`
- `data/raw/hyperliquid/whale_positions/_collector_status.json`

This data is public account-state context only. It can support positioning-state research and dashboard explanation, but
it is not venue-native primary truth and should not be promoted into execution or training gates without an admission
review.

## Scheduler Installation

Preferred host scheduler on macOS:

- `launchd`

Fallback:

- `cron`

The repo intentionally does not auto-install host schedulers. Use the wrappers above with your host-specific scheduler.

Recommended cadence:

- collector job: hourly
- health-only job: hourly, offset by 10-15 minutes after collector

## Operator Checks

After scheduler installation, verify:

- `collector-latest.log` updates hourly under `logs/forward_collection`
- `results/data_readiness/forward_collection_latest.json` is fresh
- `results/data_readiness/forward_collection_health.json` is fresh
- `data_readiness.json` shows separate `deribit_status` and `deribit_block_flow_status`
