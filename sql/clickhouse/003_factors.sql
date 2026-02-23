CREATE TABLE IF NOT EXISTS factors.signals (
    symbol LowCardinality(String),
    factor_id LowCardinality(String),
    timestamp DateTime64(3, 'UTC'),
    factor_value Float64 CODEC(Gorilla, ZSTD(3)),
    forward_return_1m Float64,
    forward_return_5m Float64
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (factor_id, symbol, timestamp);

CREATE OR REPLACE VIEW factors.correlation_matrix AS
SELECT
    symbol,
    arrayMap(x -> round(x, 4), arrayJoin(corrMatrix(factor_pa, factor_mom, factor_cvd, factor_fr))) AS correlation_row
FROM (
    SELECT
        symbol,
        timestamp,
        maxIf(factor_value, factor_id = 'PA_MTR') AS factor_pa,
        maxIf(factor_value, factor_id = 'MOM_15M') AS factor_mom,
        maxIf(factor_value, factor_id = 'CVD_SPOT') AS factor_cvd,
        maxIf(factor_value, factor_id = 'FUNDING_RATE') AS factor_fr
    FROM factors.signals
    WHERE timestamp >= now() - INTERVAL 7 DAY
    GROUP BY symbol, timestamp
)
GROUP BY symbol;

