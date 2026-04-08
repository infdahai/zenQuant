# ZenQuant Agent Context

本文件是 `AGENTS.md` 的补充执行上下文，供 Codex / Claude 在进入仓库时快速对齐当前状态。

如果与 `AGENTS.md` 冲突，以 `AGENTS.md` 为准。

## Quick Workflow

- 先读 `AGENTS.md`、本文件，以及当前任务直接相关的 1-3 个文件。
- 优先使用已有权威状态文件，不要凭聊天记忆判断项目状态。
- 长任务、后台任务、持续采集与 campaign 状态，以仓库中的状态文件为准，不以终端输出为准。
- tester / operator 的唯一交付入口始终是 `docs/LIVE_VALIDATION_HANDOFF_SPEC.md`。

## Current Product Defaults

- 资产：`BTC`
- 执行周期：`4h`
- 大周期偏置：`1d`
- 数据切分：
  - `train = 2020-01-01 ~ 2023-12-31`
  - `dev = 2024-01-01 ~ 2025-12-31`
  - `test = 2026-01-01 ~ latest`
- 当前最可信 live 候选：`current / paulwei_baseline`
- RL 角色：辅助搜索器，不改策略骨架
- Optuna 角色：主搜索器，负责框架级参数搜索

## Current Research Truth

- 权威交付规格：
  - `docs/LIVE_VALIDATION_HANDOFF_SPEC.md`
- 数据完成度：
  - `results/data_readiness/data_readiness.json`
  - `results/data_readiness/data_readiness.md`
- 调优 campaign：
  - `results/framework_optimization/campaign_status.json`
- 调优最终报告：
  - `results/framework_optimization/framework_optimization.json`
  - `results/framework_optimization/framework_optimization.md`

## Current Status Summary

- liquidation：
  - 已从 `insufficient_data` 进入 `data_ready=true`
  - 当前是否可 promote，以 `results/data_readiness/data_readiness.json` 为准
- Deribit：
  - 独立 collector 已恢复
  - 是否达到 `observation_ready / research_ready`，以 `_collector_status.json` 与 `data_readiness.json` 为准
- 调优：
  - 高预算 campaign 已可运行并可恢复
  - 是否存在可 promote tuned winner，以最终 `framework_optimization.json` 为准
  - `scripts/run_framework_campaign.py` 是唯一权威 clean tuning 入口
  - `scripts/run_auto_research.py` 仅用于 internal/dev research，不单独产出 promote truth
  - 若状态文件标记 `invalidated`，则所有旧 tuned params 一律作废，必须从 `train/dev` 重新开始

## Practical Rules

- 不在 `AGENTS.md` 或本文件里写临时会话级结论；用结果文件承载事实。
- 参数搜索只能看 `train`；候选排序只能看 `dev`；`test` 只能做最终历史封板。
- `walk_forward_eval.py` 与 `2023-08` 现在都只算 `dev` 层证据，不再替代 `test`。
- 不把 Hyperliquid public 数据表述成 venue-native liquidation truth；它是 liquidation-compatible proxy。
- liquidation 未 `promotion_ready` 时，不把它纳入正式 promote 级搜索空间。
- 在出现新的 promote candidate 之前，不把 tuned winner 当默认 live 候选。
