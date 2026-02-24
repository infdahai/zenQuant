CREATE TABLE IF NOT EXISTS indicators.pack_v1 (
    symbol LowCardinality(String),
    tf LowCardinality(String),
    ts DateTime64(3, 'UTC'),
    cutoff_time DateTime64(3, 'UTC'),
    indicator_version LowCardinality(String),
    formula_hash String,
    quality_flags Array(String),
    coverage_venue_count UInt8,
    coverage_oi_coverage Float64,
    coverage_staleness_ms UInt64,
    values_json String,
    components_json String
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, tf, ts);

CREATE TABLE IF NOT EXISTS strategy.intents (
    portfolio LowCardinality(String),
    symbol LowCardinality(String),
    tf LowCardinality(String),
    cutoff_time DateTime64(3, 'UTC'),
    target_qty Float64,
    target_leverage Float64,
    reduce_only UInt8,
    regime LowCardinality(String),
    regime_confidence Float64,
    reason_codes Array(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(cutoff_time)
ORDER BY (portfolio, symbol, cutoff_time);

CREATE TABLE IF NOT EXISTS risk.snapshots (
    ts DateTime64(3, 'UTC'),
    equity Float64,
    high_water_mark Float64,
    drawdown Float64,
    lev_cap Float64,
    reduce_only_mode UInt8,
    flags Array(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY ts;
