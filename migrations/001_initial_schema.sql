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

CREATE TABLE IF NOT EXISTS market_states (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    cutoff_time_ns BIGINT NOT NULL,
    directional_state TEXT NOT NULL,
    directional_confidence DOUBLE PRECISION NOT NULL,
    volatility_state TEXT NOT NULL,
    volatility_confidence DOUBLE PRECISION NOT NULL,
    positioning_state TEXT NOT NULL,
    positioning_confidence DOUBLE PRECISION NOT NULL,
    quality_state TEXT NOT NULL,
    quality_confidence DOUBLE PRECISION NOT NULL,
    regime TEXT NOT NULL,
    regime_confidence DOUBLE PRECISION NOT NULL,
    direction_signal DOUBLE PRECISION NOT NULL,
    transition_risk DOUBLE PRECISION NOT NULL,
    quality_flags JSONB NOT NULL,
    reason_codes JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_states_symbol_tf_cutoff
    ON market_states (symbol, timeframe, cutoff_time_ns DESC);

CREATE TABLE IF NOT EXISTS playbook_selections (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    cutoff_time_ns BIGINT NOT NULL,
    playbook_id TEXT NOT NULL,
    playbook_confidence DOUBLE PRECISION NOT NULL,
    allowed_directions JSONB NOT NULL,
    max_leverage DOUBLE PRECISION NOT NULL,
    trailing_stop_atr_mult DOUBLE PRECISION NOT NULL,
    allow_pyramid BOOLEAN NOT NULL,
    max_pyramid_count INTEGER NOT NULL,
    time_decay_bars INTEGER,
    selection_reason TEXT NOT NULL,
    reason_codes JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_playbook_selections_symbol_tf_cutoff
    ON playbook_selections (symbol, timeframe, cutoff_time_ns DESC);

CREATE TABLE IF NOT EXISTS sizing_decisions (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    cutoff_time_ns BIGINT NOT NULL,
    base_size DOUBLE PRECISION NOT NULL,
    confidence_multiplier DOUBLE PRECISION NOT NULL,
    stress_discount DOUBLE PRECISION NOT NULL,
    execution_haircut DOUBLE PRECISION NOT NULL,
    final_leverage DOUBLE PRECISION NOT NULL,
    reason_codes JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sizing_decisions_symbol_tf_cutoff
    ON sizing_decisions (symbol, timeframe, cutoff_time_ns DESC);

CREATE TABLE IF NOT EXISTS exit_decisions (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    cutoff_time_ns BIGINT NOT NULL,
    exit_type TEXT NOT NULL,
    exit_urgency TEXT NOT NULL,
    target_reduction_pct DOUBLE PRECISION NOT NULL,
    stop_price DOUBLE PRECISION,
    reason_code TEXT NOT NULL,
    reason_detail TEXT NOT NULL,
    reason_codes JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exit_decisions_symbol_tf_cutoff
    ON exit_decisions (symbol, timeframe, cutoff_time_ns DESC);

CREATE TABLE IF NOT EXISTS deribit_risk_packs (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    ts_ns BIGINT NOT NULL,
    atm_iv_30d DOUBLE PRECISION NOT NULL,
    term_structure_slope DOUBLE PRECISION NOT NULL,
    put_call_skew_25d DOUBLE PRECISION NOT NULL,
    oi_concentration DOUBLE PRECISION NOT NULL,
    block_flow_imbalance DOUBLE PRECISION NOT NULL,
    quality_flags JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deribit_risk_packs_symbol_ts
    ON deribit_risk_packs (symbol, ts_ns DESC);

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
