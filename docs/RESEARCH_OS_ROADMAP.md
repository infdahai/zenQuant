# Research OS Roadmap

This roadmap translates the lessons in
[QUADRATURE_LESSONS.md](./QUADRATURE_LESSONS.md) into a concrete next-step infra
plan for zenQuant.

This is not a strategy roadmap. It does not change:

- the flagship BTC-first scope,
- bar-close decision policy,
- futures-only `dev/test` anchor,
- risk overlay authority,
- promote truth ownership.

Instead, it defines how to make the research system more machine-like and less
manual.

## 1. Goal

Build a stronger research operating system around the existing authoritative
path:

- `scripts/run_framework_campaign.py` stays the only clean promote path
- `zenquant/research/auto_research.py` stays internal/dev-only
- `framework_optimization.json/.md` stay final historical truth

The upgrade target is:

- better traceability
- better cache discipline
- better scheduling boundaries
- faster rejection of weak candidates

## 2. Priority A: Experiment lineage

### V1 behavior

Every major research run should emit one machine-readable run manifest.

At minimum, the manifest should identify:

- run family:
  - campaign
  - compare
  - stress
  - ablation
  - loop round
- data contract id
- split contract id
- training input contract snapshot
- profile / plugin stack
- baseline family
- seed source
- code commit
- formula hash
- invalidation status and reason

### Why this matters

Current artifacts are strong, but not all run types expose the same lineage
surface. That makes it too easy for a result to look authoritative while
omitting the exact contract set it depended on.

### Existing authoritative artifacts to anchor

- `results/framework_optimization/campaign_status.json`
- `results/framework_optimization/framework_optimization.json`
- `training_input_contract`
- layered historical-extension contract
- signal independence contract

### Explicit non-goals

- not a metadata lake
- not a database migration project
- not a replacement for current final truth artifacts

## 3. Priority B: Cache topology

### V1 behavior

Promote cache hit reporting from runtime telemetry to explicit cache policy.

Define and document three cache classes:

1. `replay_result_cache`
- keyed by symbol, timeframe set, split contract, params, formula hash

2. `feature_state_cache`
- keyed by data contract, cutoff window, feature config, formula hash

3. `validation_leg_cache`
- keyed by validation window, baseline family, params, split contract

Each cache must have explicit rules for:

- identity
- invalidation
- safe reuse
- visibility in status artifacts

### Why this matters

Current `cache_hit_rate` style metrics show that reuse exists, but they do not
yet define a repo-level truth for what is safe to reuse and when.

### Existing anchor points

- `run_framework_campaign.py` stage1/rl cache hit reporting
- `compare_variants` validation legs
- formula hash / golden / parity discipline

### Explicit non-goals

- not distributed caching
- not changing result semantics
- not reusing anything across incompatible data/split contracts

## 4. Priority C: Scheduler contract

### V1 behavior

Define a single-machine research scheduler contract above the current tools.

It should standardize:

- task class:
  - promote campaign
  - internal search
  - compare
  - stress
  - backfill review
- task priority
- max concurrency
- retry / resume eligibility
- cancellation conditions
- stale-lock handling
- invalidation boundary

### Why this matters

The repo already has:

- `run_framework_research_loop`
- `run_framework_campaign`
- `watch_framework_loop`
- checkpoint / resume semantics

But these are still tool-level behaviors, not a unified scheduling contract.

### Existing anchor points

- campaign checkpointing
- loop resume logic
- stale lock handling
- campaign status heartbeat fields

### Explicit non-goals

- not a distributed cluster scheduler
- not Kubernetes
- not multi-host orchestration

## 5. Immediate implementation order

If this roadmap is executed, the recommended order is:

1. **Experiment lineage**
- lowest risk
- highest clarity gain
- improves every later subsystem

2. **Cache topology**
- next highest leverage
- reduces repeated compute
- improves correctness around reuse

3. **Scheduler contract**
- only after lineage and cache identity are explicit
- otherwise scheduling semantics become hand-wavy

## 6. Acceptance criteria

This roadmap is successful when the repo can answer these questions without
manual reconstruction:

1. What exact contracts and identities produced a given research result?
2. Which parts of a run were reused from cache, and why was that reuse valid?
3. Why did a candidate reach promote truth, get rejected, or get invalidated?

And it must do that without weakening:

- replay/live parity
- golden regression discipline
- layered-truth boundary declarations
- signal independence gates
- risk overlay authority

## 7. Defaults and constraints

Defaults for this roadmap:

- BTC remains the flagship asset
- `dev/test` remain futures-only
- `AutoResearch` remains internal/dev-only
- `framework_optimization` remains the only historical promote truth
- no AI-first or L2-first shift is implied

The governing principle is simple:

- make the research system more industrial
- do not make the strategy system less disciplined
