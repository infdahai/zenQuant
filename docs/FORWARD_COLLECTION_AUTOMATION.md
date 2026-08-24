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

## Frozen Trend-Research Spot Repair

The TimeSpace Shadow and Trend Participation donor share one required BTC spot-daily source. This is a manual,
network-dependent repair and freeze operation; it is not part of the hourly collector. The source contract is
`BINANCE_SPOT_BTCUSDT_1D`, with fallback `NONE`.

The required half-open market-event range is exactly:

- warmup: `[2025-10-08T00:00:00Z, 2026-01-01T00:00:00Z)` (85 daily closes)
- reused diagnostic window: `[2026-01-01T00:00:00Z, 2026-08-24T00:00:00Z)`
- frozen exclusive end: `2026-08-24T00:00:00Z`

Run these commands from the repository root. Binance archive downloads require network access. The downloader's
`--end-date` is inclusive, so `2026-08-23` is intentional: specifying `2026-08-24` would download a bar at or after
the frozen exclusive end and readiness would fail closed with `INSUFFICIENT_DATA_SPOT_DAILY_POST_END`.

```bash
uv run python scripts/download_spot_data.py \
  --symbol BTCUSDT \
  --start-date 2025-10-08 \
  --end-date 2026-08-23 \
  --timeframes 1d

uv run python scripts/convert_csv_to_parquet.py \
  --source binance_spot \
  --symbol BTCUSDT \
  --start-open-date 2025-10-08 \
  --end-open-date-exclusive 2026-08-24

uv run python scripts/data_readiness_status.py \
  --trend-spot-path data/raw/binance_spot/klines/BTCUSDT/1d/data.parquet
```

Do not run either donor until `results/data_readiness/data_readiness.json` reports
`trend_research_status.status == "READY"`. Missing, malformed, gapped, stale, duplicate, or post-end data has no
neutral fallback and is a blocker when `--trend-spot-path` is supplied.

The October monthly Binance archive contains valid rows before the 85-close warmup. They remain in raw provenance,
but the strict normalizer validates the complete raw archives and then deterministically crops the normalized corpus
to the exact half-open research range. Readiness and the freezer reject any normalized row before the warmup or at or
after the frozen end.

The execution freeze binds both normalized bytes and every raw Binance CSV as provenance. The following zsh-safe
array uses C-locale filename order, expands each regular raw archive into the repeatable `--spot-raw` argument required
by the freezer, and refuses an empty inventory.

```zsh
export LC_ALL=C
trend_spot_raw_paths=(data/raw/binance_spot/klines/BTCUSDT/1d/*.csv(.N))
(( ${#trend_spot_raw_paths[@]} > 0 )) || { print -u2 "no raw BTCUSDT 1d CSV archives"; exit 1; }
trend_spot_raw_args=()
for trend_spot_raw_path in "${trend_spot_raw_paths[@]}"; do
  trend_spot_raw_args+=(--spot-raw "$trend_spot_raw_path")
done

test ! -e config/research/registrations
uv run python scripts/freeze_trend_research_inputs.py \
  --spot-1d data/raw/binance_spot/klines/BTCUSDT/1d/data.parquet \
  "${trend_spot_raw_args[@]}" \
  --perp-5m data/raw/binance/klines/BTCUSDT/5m/data.parquet \
  --funding data/raw/binance/funding/BTCUSDT/data.parquet \
  --time-space-hypothesis config/research/time_space_shadow_hypothesis_v1.json \
  --trend-hypothesis config/research/trend_participation_hypothesis_v1.json \
  --reused-start 2026-01-01T00:00:00Z \
  --reused-end 2026-08-24T00:00:00Z \
  --output-dir config/research/registrations
```

The output directory must not already exist. The two exact generated files are
`config/research/registrations/time_space_shadow_execution_v1.json` and
`config/research/registrations/trend_participation_execution_v1.json`; these are the only execution paths accepted by
the trusted registration workflow. Never overwrite or silently reuse an earlier execution envelope.
