CREATE TABLE IF NOT EXISTS market_data.trades (
    symbol LowCardinality(String),
    exchange LowCardinality(String),
    trade_time DateTime64(6, 'UTC'),
    trade_date Date MATERIALIZED toDate(trade_time),
    price Decimal64(8) CODEC(T64, ZSTD(3)),
    quantity Decimal64(8) CODEC(ZSTD(3)),
    trade_id UInt64,
    side Enum8('buy' = 1, 'sell' = -1, 'unknown' = 0),
    INDEX symbol_idx symbol TYPE bloom_filter GRANULARITY 4
) ENGINE = MergeTree()
PARTITION BY (toYYYYMM(trade_date), exchange)
ORDER BY (symbol, trade_time, trade_id)
SETTINGS index_granularity = 8192;

