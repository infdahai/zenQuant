CREATE TABLE IF NOT EXISTS indicator_packs (
    id BIGSERIAL PRIMARY KEY,
    ts_ns BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    cutoff_time_ns BIGINT NOT NULL,
    indicator_version TEXT NOT NULL,
    formula_hash TEXT NOT NULL,
    quality_flags JSONB NOT NULL,
    values JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_indicator_packs_symbol_tf_cutoff
    ON indicator_packs (symbol, timeframe, cutoff_time_ns DESC);

CREATE TABLE IF NOT EXISTS strategy_intents (
    id BIGSERIAL PRIMARY KEY,
    intent_id TEXT NOT NULL UNIQUE,
    portfolio TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    cutoff_time_ns BIGINT NOT NULL,
    target_qty DOUBLE PRECISION NOT NULL,
    target_leverage DOUBLE PRECISION NOT NULL,
    reduce_only BOOLEAN NOT NULL,
    regime TEXT NOT NULL,
    regime_confidence DOUBLE PRECISION NOT NULL,
    reason_codes JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategy_intents_symbol_cutoff
    ON strategy_intents (symbol, cutoff_time_ns DESC);

CREATE TABLE IF NOT EXISTS risk_snapshots (
    id BIGSERIAL PRIMARY KEY,
    portfolio TEXT NOT NULL,
    ts_ns BIGINT NOT NULL,
    equity DOUBLE PRECISION NOT NULL,
    high_water_mark DOUBLE PRECISION NOT NULL,
    drawdown DOUBLE PRECISION NOT NULL,
    lev_cap DOUBLE PRECISION NOT NULL,
    reduce_only_mode BOOLEAN NOT NULL,
    flags JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_snapshots_ts
    ON risk_snapshots (ts_ns DESC);

CREATE TABLE IF NOT EXISTS execution_reports (
    id BIGSERIAL PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side SMALLINT NOT NULL,
    status TEXT NOT NULL,
    filled_qty DOUBLE PRECISION NOT NULL,
    avg_price DOUBLE PRECISION NOT NULL,
    fee DOUBLE PRECISION NOT NULL,
    transaction_time_ns BIGINT NOT NULL,
    reject_reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_execution_reports_symbol_time
    ON execution_reports (symbol, transaction_time_ns DESC);

CREATE TABLE IF NOT EXISTS position_states (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    snapshot_time_ns BIGINT NOT NULL,
    direction DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    current_qty DOUBLE PRECISION NOT NULL,
    trailing_stop DOUBLE PRECISION NOT NULL,
    bars_held INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_position_states_symbol_tf_time
    ON position_states (symbol, timeframe, snapshot_time_ns DESC);

CREATE TABLE IF NOT EXISTS data_quality_log (
    id BIGSERIAL PRIMARY KEY,
    ts_ns BIGINT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    quality_flag TEXT NOT NULL,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_data_quality_log_ts
    ON data_quality_log (ts_ns DESC);
