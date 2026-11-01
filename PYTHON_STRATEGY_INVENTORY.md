# Python strategy inventory

Snapshot of the Python strategy package and plugin registry at `fd6b94f`. This is an inventory for migration and eventual cleanup, not a declaration that Python can be removed or that any research candidate is approved. The code and current result artifacts remain authoritative; research variants are configurations of the strategy, not separate production strategies.

## Strategy and playbooks

- [`zenquant/strategy/engine.py`](zenquant/strategy/engine.py) owns `ARCStrategyEngine` and `ARCStrategyConfig`: state and indicator inputs, target intent, position/confirmation state, sizing and exits, and optional plugin composition. The production/handoff baseline is BTC, 4h bar-close; the 4h/1h/5m path is research replay, not a promotion.
- [`zenquant/strategy/playbook_engine.py`](zenquant/strategy/playbook_engine.py) selects four playbooks in order: `CAPITAL_PRESERVATION` (flat on unsafe/distorted or low-confidence state), `BULL_CONTINUATION` (long/flat trend), `BEAR_DEFENSE` (short/flat, including bearish transition override), then `RANGE_LIGHT` (lighter long/short/flat). Their limits and thresholds live in `PlaybookEngineConfig` and may be overridden by configuration.
- [`zenquant/strategy/sizing_engine.py`](zenquant/strategy/sizing_engine.py) computes legacy volatility, confidence, stress and execution-adjusted sizing; [`zenquant/strategy/exit_engine.py`](zenquant/strategy/exit_engine.py) checks invalidation, trailing stop, time decay and regime change. [`zenquant/strategy/decision_snapshot.py`](zenquant/strategy/decision_snapshot.py) creates deterministic decision audit snapshots; [`zenquant/strategy/__init__.py`](zenquant/strategy/__init__.py) exports the package API.
- Risk and execution are separate dependencies, not playbooks: [`zenquant/risk/guard.py`](zenquant/risk/guard.py), [`zenquant/domain/models.py`](zenquant/domain/models.py), and the execution implementation under [`zenquant/execution/`](zenquant/execution/) determine the contracts and downstream behavior. Market state, indicators, event ordering and filled-position feedback are also required for parity.

## Runtime consumers and variants

[`zenquant/app/pipeline.py`](zenquant/app/pipeline.py) and [`zenquant/app/server.py`](zenquant/app/server.py) instantiate the legacy engine without a registry. [`zenquant/historical/replay.py`](zenquant/historical/replay.py) optionally builds a registry from a profile; [`scripts/paper_trade.py`](scripts/paper_trade.py) also optionally loads one. [`zenquant/research/comparison.py`](zenquant/research/comparison.py), [`zenquant/research/framework_search.py`](zenquant/research/framework_search.py), and [`zenquant/research/framework_params.py`](zenquant/research/framework_params.py) construct replay comparisons, search parameters and execution/risk configurations. Tests and golden generators are additional consumers; search imports and profile names before removing anything.

`current` is the current strategy comparison; `paulwei_baseline` is the frozen manual execution/risk comparison baseline. They are the trusted live-candidate baseline family, not proof of a newly promoted winner. The mature-style and public-archive configurations are research baselines in [`config/research_baselines/`](config/research_baselines/); `paulwei_public_archive_baseline` is research-only, with drawdown outside the current band. `execution_plus`, `strategy_plus`, `full_variant`, and the execution/risk architecture profiles in `comparison.py` are experimental comparisons; inspect their definitions and [`config/research/`](config/research/) for exact overrides rather than treating their names as deployed strategies. AHR999 is limited to slow regime/risk/DCA/explanation roles, not direction votes or execution triggers.

## Plugin inventory

[`zenquant/plugins/registry.py`](zenquant/plugins/registry.py) loads only profile entries enabled for each type from the corresponding Python module; it merges schema defaults with profile overrides. [`zenquant/plugins/profile.py`](zenquant/plugins/profile.py), [`zenquant/plugins/protocols.py`](zenquant/plugins/protocols.py), [`zenquant/plugins/signal_roles.py`](zenquant/plugins/signal_roles.py), and [`zenquant/plugins/orchestrator.py`](zenquant/plugins/orchestrator.py) define loading, contracts, signal roles and composition. Signals use weighted composition, filters an AND chain, sizers multiplication, and exits priority order. The plugin path is optional; plugin defaults must not be mistaken for the legacy runtime configuration.

The sole checked-in plugin TOML profile is [`config/profiles/default.toml`](config/profiles/default.toml). Names below are module stems under the indicated directory; enabled/disabled describe that profile only. Other supplied profiles can select other modules. The TOML and module schemas are the parameter authority.

| Type and path | Enabled in default | Disabled in default / unlisted |
| --- | --- | --- |
| Signals: [`zenquant/plugins/signals/`](zenquant/plugins/signals/) | `multi_momentum_signal`, `cvd_signal`, `liquidity_signal`, `crowding_signal`, `etf_flow_state_signal`, `cme_positioning_state_signal`, `options_vol_signal`, `perp_carry_stress_signal`, `vol_structure_signal`, `crash_reversal_signal`, `funding_alpha_signal` | Disabled: `macro_regime_signal`, `cross_asset_flow_signal`, `alt_risk_context_signal`, `onchain_activity_signal`, `binance_metrics_state_signal`, `vol_mean_reversion_signal`. Unlisted: `vacuum_signal`. |
| Filters: [`zenquant/plugins/filters/`](zenquant/plugins/filters/) | `quality_filter`, `trend_filter` | `bear_defense_override_filter` |
| Sizers: [`zenquant/plugins/sizers/`](zenquant/plugins/sizers/) | `vol_target_sizer`, `confidence_sizer`, `crowding_discount_sizer`, `conviction_boost_sizer` | `binance_metrics_risk_sizer`, `bear_defense_churn_sizer`, `winning_position_sizer` |
| Exits: [`zenquant/plugins/exits/`](zenquant/plugins/exits/) | `invalidation_exit`, `trailing_stop_exit`, `profit_lock_exit`, `time_decay_exit`, `regime_change_exit` | None |

Signal names identify their inputs or market hypothesis; filters gate trades, sizers modify exposure, and exits request position reduction. For exact formulas and missing-data behavior inspect each module. An enabled options signal in a research profile does not authorize an OptionsState multiplier or sidecar in live use.

## Parameter and promotion status

The authoritative clean tuning entry is [`scripts/run_framework_campaign.py`](scripts/run_framework_campaign.py); [`scripts/run_auto_research.py`](scripts/run_auto_research.py) is internal/dev research, not promotion truth. [`config/data_splits.toml`](config/data_splits.toml) defines the temporal split; train selects parameters, dev supplies diagnostics/ranking, and test is final historical acceptance.

The local, generated `results/framework_optimization/framework_optimization.json` inspected for this inventory has `best_params: null` and `final_verdict.best_variant: current`, `test_evaluated: false`, and `promote_to_paper_live_candidate: false`. Its `final_verdict` is authoritative for that run, not the campaign completion flag. `results/framework_optimization/campaign_status.json` contains `stage1_best_params_so_far` trial values and marks dev as diagnostics only; these are diagnostic trial values, not final parameters or an approved strategy. Re-read both artifacts for current state, including invalidation, before use. These result files are locally generated and absent from this clean worktree; they are not guaranteed to exist in a fresh checkout. The current Quant V2 verdict is blocked, with promotion and new training paused; see [`scripts/build_quant_v2_acceptance_summary.py`](scripts/build_quant_v2_acceptance_summary.py) and the result references in [`CLAUDE.md`](CLAUDE.md). The authoritative handoff is [`LIVE_VALIDATION_HANDOFF_SPEC.md`](LIVE_VALIDATION_HANDOFF_SPEC.md).

## Eventual removal criteria

Retain these modules and profiles until each proposed removal has all of the following evidence:

1. Identify every production, paper, replay, research, CLI, dynamic profile and test consumer; migrate or deliberately retire each one, including external profiles and stored configurations.
2. Provide an approved replacement with equivalent point-in-time inputs, bar-close decisions, target/risk/execution contracts, configuration and audit outputs. Demonstrate live/replay parity and risk behavior on representative recorded events, including missing data and position transitions.
3. Preserve reproducible historical runs, parameter provenance, pinned profiles, result artifacts and golden fixtures, or document an accessible archival path and owner. Retire research variants only after their comparison and audit uses are resolved.
4. Pass applicable repository gates, golden and plugin/legacy parity tests, relevant integration and replay tests, and the required historical, robustness and live validation for any replacement. Product-owner approval and the current handoff/promotion decision must explicitly cover the cutover.
5. Remove imports, registry/profile references, entry points and obsolete tests together only after cutover and rollback are verified. Disabled or unlisted in the default profile alone is not evidence that a plugin is unused.
