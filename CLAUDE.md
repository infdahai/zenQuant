# ZenQuant Agent Context

本文件是 `AGENTS.md` 的补充执行上下文，供 Codex / Claude 在进入仓库时快速对齐当前状态。

如果与 `AGENTS.md` 冲突，以 `AGENTS.md` 为准。

## Quick Workflow

- 先读 `AGENTS.md`、本文件，以及当前任务直接相关的 1-3 个文件。
- 优先使用已有权威状态文件，不要凭聊天记忆判断项目状态。
- 长任务、后台任务、持续采集与 campaign 状态，以仓库中的状态文件为准，不以终端输出为准。
- 当前实现、历史 Gate v2 证据与 Quant System v2 research-path 边界统一见 `docs/ARCHITECTURE.md`。
- tester / operator 的唯一交付入口始终是 `docs/LIVE_VALIDATION_HANDOFF_SPEC.md`。

## Current Product Defaults

- 资产：`BTC`
- 当前 production/handoff 基线：`4h` bar-close 决策与执行语义
- 最新 research replay：`4h` 方向、`1h` context、`5m` execution replay
- Quant System v2：`4h` 方向、risk budget、`5m` 纯执行；research path 已实施，尚未 promote
- 大周期偏置：`1d`
- 数据切分：
  - `train = 2020-01-01 ~ 2023-12-31`
  - `dev = 2024-01-01 ~ 2025-12-31`
  - `test = 2026-01-01 ~ latest`
- 当前最可信 live 候选：`current / paulwei_baseline`
- RL / Optuna：研究工具；旧 pre-contract 归因仅诊断性返回 `stop_donor_training`，当前仍未授权新搜索

## Current Research Truth

- 统一架构入口：
  - `docs/ARCHITECTURE.md`
- 历史 Gate v2：
  - `results/remediation_20260821/gate_v2_rescore/gate_v2_rescore.json`
  - `results/remediation_20260821/gate_v2_rescore/gate_v2_rescore.md`
- 权威交付规格：
  - `docs/LIVE_VALIDATION_HANDOFF_SPEC.md`
- Quant V2：
  - 架构审查：`docs/QUANT_V2_ARCHITECTURE_EXPERT_REVIEW.md`
  - 冻结实验与复跑：`docs/QUANT_V2_ACCEPTANCE_HANDOFF.md`
  - compact verdict 唯一生成入口：`scripts/build_quant_v2_acceptance_summary.py`
  - Operator evidence status：`requires_reinstrumented_30_run_rerun`
  - 旧 repaired 30/30 是 **pre-contract diagnostic only**；它缺少 **accounting-kernel and full replay-input identity**，不得恢复权威经济结论
  - Operator 必须按 `config/research/legacy_accounting_rerun_v2.json` 在 **new immutable output directory** 完整复跑，旧 artifact 和 v1 合同不得编辑或补字段
  - 冻结 replay 输入合同是 `legacy-accounting-rerun-contract.v3`：2026-08-23 13:48 CST 的只读 snapshot 全量 `5,114` 个 Parquet / `21,375,864,438` bytes / `sha256:17fe13a2b55cfd8a34fb0e9b2c0c616e7af4c38a25e9eed705eb4b9cc0d6be04`；合同文件 SHA-256 为 `c76f7c027698ac8146b549d551d38b0f6963e0d6932b1e77cabebf4fab397a19`
  - 复跑必须从指向冻结只读 snapshot 的工作树执行 `uv run python scripts/rerun_legacy_accounting_baseline.py --root data/raw/binance --legacy-input results/quant_v2_acceptance/architecture_attribution.json --output-dir results/accounting_integrity_20260823_v2`；`--root` 的真实 parent 必须与 cwd `data/raw` 相同且整个 corpus 不可写，entry 1 前还会无运行构造第一 donor context 校验 macro/options 与 formula identity
  - 历史诊断为 late-dev baseline 与额外 15bp 下均 5/5 为负、strict robustness `0/5`，attribution 为 `stop_donor_training`
  - 上述 `0/5` / `stop_donor_training` 只是否定性诊断；当前不授权 **promotion or training**，identity-complete 复跑前也没有新的权威经济 verdict
  - 下一候选假设：时空关系仅允许预注册 shadow attribution，尚未获证据支持或交易权限
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
  - 2026-08-23 只读复查仍存在 Binance 历史缺口与 Hyperliquid 部分覆盖
  - merged 数据仅达到最低研究口径，当前不可 promote
  - 实时状态仍以 `results/data_readiness/data_readiness.json` 为准
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
