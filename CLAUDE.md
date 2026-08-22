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
- Quant V2：
  - 架构审查：`docs/QUANT_V2_ARCHITECTURE_EXPERT_REVIEW.md`
  - 冻结实验与复跑：`docs/QUANT_V2_ACCEPTANCE_HANDOFF.md`
  - compact verdict 唯一生成入口：`scripts/build_quant_v2_acceptance_summary.py`
  - 当前 verdict：`BLOCKED_PAUSE_PROMOTION_AND_TRAINING`
  - 原因：late-dev 额外 15bp 下 5/5 为负，strict robustness `0/5`
  - 禁止：继续调参/训练、promotion、激活 OptionsState multiplier
- 数据完成度：
  - `results/data_readiness/data_readiness.json`
  - `results/data_readiness/data_readiness.md`
  - `results/data_readiness/forward_collection_latest.json`
  - `results/data_readiness/forward_collection_health.json`
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
  - `2026-08-22T02:00:04.953009Z` 的只读核验为两者均 `true`，但 2026 options 历史与 frozen 2025 OOS 不重叠，不能据此激活 sidecar
- 调优：
  - 高预算 campaign 已可运行并可恢复
  - 是否存在可 promote tuned winner，以最终 `framework_optimization.json.final_verdict` 为准
  - `scripts/run_framework_campaign.py` 是唯一权威 clean tuning 入口
  - `scripts/run_auto_research.py` 仅用于 internal/dev research，不单独产出 promote truth
  - 若状态文件标记 `invalidated`，则所有旧 tuned params 一律作废，必须从 `train/dev` 重新开始
  - `v27 round_003` 当前不是活跃训练，而是 `stale running state`
    - `results/framework_optimization_loop_v27/loop_status.json` 仍写 `campaign_running`
    - `results/framework_optimization_loop_v27/runs/round_003/campaign_status.json` 停在 `stage2_prepare_variants progress=12/16`
    - 但对应 `run_framework_*` / campaign 进程已不存在
    - 该状态应解释为 `terminal_state_not_written`，不得解释为“仍在后台继续训练”
  - rerun benchmark 已完成，正式结论以 `results/perf_stage2_benchmark_fast_rerun/perf_stage2_benchmark_review.json` 为准
    - `stage2 reuse visible`
    - `indicator hot path not yet improved`
  - 第二阶段 CPU 热路径改动已拆成原子 commit，并已形成 benchmark 归因 artifact
- PaulWei / AHR999：
  - `paulwei_public_archive_baseline` 当前强于旧 `paulwei_baseline`，但仍是 `research-only`
  - 当前主 blocker：`drawdown_outside_current_band`
  - 严格学习结论以 `results/paulwei_research/strict_execution_learning_review.json` 为准
  - 当前值得吸收的增量：
    - 多 tranche 建仓但短侧扩张更克制
    - staged protective exits
    - drawdown 后更慢的 cooldown / ramp
    - moderate flip confirmation floor
    - 高波动期更强的风险压缩
  - 不可迁移维度固定为：
    - discretionary macro inputs
    - news / narrative interpretation
    - cross-exchange context
    - 不可观测 conviction scaling
  - `AHR999` 当前只允许用于：
    - `regime_prior`
    - `risk_budget_modifier`
    - `dca_guidance_signal`
    - `explainability_context`
  - `AHR999` 当前禁止进入：
    - `intraday_trigger`
    - `execution_signal`
    - `direction_vote`

## Practical Rules

- 不在 `AGENTS.md` 或本文件里写临时会话级结论；用结果文件承载事实。
- 参数搜索只能看 `train`；候选排序只能看 `dev`；`test` 只能做最终历史封板。
- `walk_forward_eval.py` 与 `2023-08` 现在都只算 `dev` 层证据，不再替代 `test`。
- 不把 Hyperliquid public 数据表述成 venue-native liquidation truth；它是 liquidation-compatible proxy。
- liquidation 未 `promotion_ready` 时，不把它纳入正式 promote 级搜索空间。
- 在出现新的 promote candidate 之前，不把 tuned winner 当默认 live 候选。
- `campaign_status=completed` 不等于已经通过最终历史准入；只有 `framework_optimization.json.final_verdict` 可决定是否 `eligible_for_paper_live`。
- `research loop` 不负责未来数据积累；前瞻数据靠 `zenquant-forward-collect` 的定时运行来刷新 Deribit / Hyperliquid / merged readiness。
