# Paper Trading Status

## 当前状态

当前仓库中的 paper trading **已启动并部分运行过**，但**未完成**。

现有证据包括：

- `scripts/paper_trade.py` 可运行
- `logs/paper_trading/decisions_*.jsonl` 已存在
- `logs/paper_trading/session_summary_2026-04-05_final.md` 与 `logs/paper_trading/testing_status_2026-04-05.md` 都明确写明：
  - `Paper trading: 30% complete`
  - `monitoring`

因此当前 paper trading 只能证明：

- 实时链路曾经被跑起来
- 有 paper session 样本
- 有部分日志和摘要输出

当前 **不能** 证明：

- authoritative paper-live validation 已完成
- live incremental validation 已完成
- 系统已经满足上线前的 paper gate

## 完成定义

只有同时满足以下条件，才能把 paper trading 标记为“已完成”：

- 连续运行满 `2 weeks`
- 实时系统健康稳定：
  - 无崩溃
  - bar close 正常
  - 决策持续记录
  - 审计持久化正常
- 至少完成一次正式 `paper-live` 导出：
  - `uv run python scripts/export_live_training_data.py --dsn "$ZENQUANT_PG_DSN" --symbol BTCUSDT_PERP --label paper-live`
- 至少完成一次 paper-live review，覆盖：
  - live/backtest parity
  - quality flags
  - playbook distribution
  - risk events
  - execution anomalies

在满足上述条件前，任何单次或少量 session 都只能被视为“运行样本”，不能被表述成“paper trading 已完成”。
