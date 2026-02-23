CREATE TABLE IF NOT EXISTS market_data.orderbook_snapshots (
    symbol LowCardinality(String),
    exchange LowCardinality(String),
    snapshot_time DateTime64(6, 'UTC'),
    bid_prices Array(Decimal64(8)) CODEC(ZSTD(3)),
    bid_sizes Array(Decimal64(8)) CODEC(ZSTD(3)),
    ask_prices Array(Decimal64(8)) CODEC(ZSTD(3)),
    ask_sizes Array(Decimal64(8)) CODEC(ZSTD(3)),
    mid_price Decimal64(8) MATERIALIZED (bid_prices[1] + ask_prices[1]) / 2,
    spread Decimal64(8) MATERIALIZED ask_prices[1] - bid_prices[1],
    total_bid_size Decimal64(8) MATERIALIZED arraySum(bid_sizes),
    total_ask_size Decimal64(8) MATERIALIZED arraySum(ask_sizes)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(snapshot_time)
ORDER BY (symbol, snapshot_time)
TTL snapshot_time + INTERVAL 30 DAY TO VOLUME 'cold_storage',
    snapshot_time + INTERVAL 180 DAY DELETE;

