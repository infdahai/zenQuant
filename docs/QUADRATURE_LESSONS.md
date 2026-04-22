# Quadrature Lessons for zenQuant

This note captures what is worth learning from Quadrature Capital for this
repository. It is not a strategy memo and it is not a justification for moving
the flagship BTC framework toward AI-first or microstructure-first trading.

The purpose is narrower:

- separate verified public facts from narrative,
- identify which lessons transfer to zenQuant,
- state which lessons do not transfer now,
- anchor the next infra layer to repo reality.

## 1. Verified public facts

These are the facts this document treats as reliable inputs.

### Official company positioning

Quadrature publicly describes itself as a systematic trading firm that combines
technology with quantitative research to build automated trading systems on its
own capital.

Source:
- [Quadrature Home](https://www.quadrature.ai/)

### Official technology posture

Quadrature publicly states that it has grown from "Linux on a few machines we
built ourselves" to "thousands of servers for research, simulation and testing,
all coordinated by our own grid scheduling system."

That matters more than any headline hardware number because it explicitly
signals a research-platform mindset:

- research,
- simulation,
- testing,
- coordinated by internal scheduling,
- not discretionary seat-of-the-pants trading.

Source:
- [Quadrature Culture](https://quadrature.ai/working-here/our-culture)

### Legal entity and reporting window

The relevant regulated UK entity is `QUADRATURE CAPITAL LIMITED`, company number
`09516131`, with accounts filed for the year ended `31 January 2025`.

Source:
- [Companies House overview](https://find-and-update.company-information.service.gov.uk/company/09516131)
- [Companies House filing history](https://find-and-update.company-information.service.gov.uk/company/09516131/filing-history)

### Scale signals from the 2025 accounts

The 2025 filed accounts support the broad conclusion that Quadrature is a very
high-output, infrastructure-heavy research organization:

- average employees during period: `173`
- profit on ordinary activities before tax: about `GBP 553.9m`
- net book value of equipment and software: about `GBP 162.0m`

These facts support a narrow inference:

- their edge is not just "better predictions"
- it is also the machinery that lets them generate, test, and operationalize
  ideas at scale

Source:
- [Companies House 2025 filing history](https://find-and-update.company-information.service.gov.uk/company/09516131/filing-history)

## 2. Directional signals that are not design inputs

The following claims may be directionally plausible, but they are not treated
as hard inputs for zenQuant design unless independently verified.

- `20,000 CPU / 2,000 GPU / 500TB / 10PB`
- specific DeepMind or Gemini-platform hiring claims
- media descriptions of internal model stack or exact alpha mix

Handling rule:

- acceptable as narrative context
- not acceptable as architecture requirements
- not acceptable as justification for adding GPU-heavy systems here

## 3. What transfers to zenQuant

The useful lesson is not "be bigger." The useful lesson is "treat research as a
production system."

For this repo, the highest-value transferable ideas are:

### Research and production must stay separated

This repo already has the correct split:

- authoritative promote path: `scripts/run_framework_campaign.py`
- internal/dev harness: `zenquant/research/auto_research.py`
- final truth artifacts: `framework_optimization.json/.md`

What to learn from Quadrature:

- keep this separation strict
- let experimentation move fast without polluting promote truth

### Data must behave like products, not raw files

This repo already moved in this direction with:

- readiness contracts
- source admission reviews
- source independence reviews
- layered historical-extension contracts

What to learn from Quadrature:

- every experiment should consume data through explicit contracts
- no training job should infer source semantics ad hoc

### Experimentation needs scheduling, caching, and lineage

Quadrature's public culture page explicitly highlights research, simulation,
testing, and grid scheduling. That is the strongest transferable infra signal.

For zenQuant, the equivalent lesson is:

- track every run as a first-class object
- bind caches to data/split/formula identity
- make invalidation explicit
- shorten the path from "candidate generated" to "candidate rejected"

### High output comes from systems, not from more opinions

This repo should keep optimizing:

- candidate triage quality
- experiment reuse
- run reproducibility
- artifact consistency

It should not mistake "more ideas" for "better throughput."

## 4. What does not transfer now

The following would be category errors for the current stage of zenQuant.

### Not "AI-first"

Quadrature being technologically sophisticated does not imply that this repo
should become:

- deep-model-first,
- end-to-end black-box,
- L2-first,
- GPU-led in problem selection.

Current repo constraints still dominate:

- BTC first
- bar-close decisions
- replay parity
- risk overlay authority
- auditability

### Not "hardware-first"

The current bottlenecks are not obviously GPU bottlenecks. The repo's problems
are still closer to:

- hostile-window fragility,
- candidate stability,
- experiment efficiency,
- contract clarity,
- promotion discipline.

That means:

- better research OS first
- bigger model systems later, only if justified

### Not "strategy philosophy replacement"

Nothing in the verified public record justifies changing:

- BTC single-asset flagship priority
- futures-only `dev/test` anchor
- slow-variable discipline for `MVRV` / `AHR999`
- bar-close regime-aware strategy philosophy

## 5. Direct guidance for this repo

The correct adaptation target is:

- **Data OS**
  - readiness, admission, independence, layered-truth contracts consumed
    uniformly
- **Experiment OS**
  - campaign, compare, stress, ablation, and loop artifacts made consistent
- **Promotion OS**
  - unambiguous candidate lineage and invalidation into final truth artifacts

This means the next infra work should focus on:

1. experiment lineage
2. cache topology
3. scheduler contract

Those are defined in the companion roadmap:
- [RESEARCH_OS_ROADMAP.md](./RESEARCH_OS_ROADMAP.md)

## 6. Bottom line

Quadrature is useful here as a reminder that top quantitative trading firms are
better understood as research machines than as "market opinion machines."

For zenQuant, the right response is not to imitate their scale. The right
response is to harden the repo into a more coherent research operating system
without weakening:

- determinism
- parity
- risk authority
- auditability
- BTC-first scope discipline
