# Task 3 report — native economic diagnostics

DONE. Base0bd6728; HEADef71d17. Core-only pure modules, focused Rust tests, lib exports. No legacy Python, old artifacts, contracts, network/I/O/clock/randomness/unsafe, merge/push/deploy changes. Parent authorized one narrow producer correction: mathematical fixture template_version `1`→`v1`, separate fix commit. No version alias accepted.

## APIs

- `costs::quote_cost(&QuoteInput,&CandidateLeg,u64,&str)->CostEvidence`
- `expressions::analyze_expression(&AnalysisInput,&CandidateRequest)->ExpressionAnalysis`
- `constraints::certify_vertical(&AnalysisInput,&CandidateRequest)->ConstraintCertificate`
- Version constants: `LONG_MODEL=terminal_paid_long.rust.v1`, `CASH_MODEL=cash_no_interest.rust.v1`, `VERTICAL_MODEL=linear_vertical_payoff.rust.v1`. Exact registered template_version `v1`; exit_policy `expiry_only`. Unknown strings/version retained UNSUPPORTED; independent costs remain when available.

All money uses checked Dec operations. Errors become explicit NOT_EVALUABLE numerical diagnostics, never rounded/zeroed or panicked. Native report/input/request hashes use canonical_hash and distinct Rust identities. Library APIs consume typed AnalysisInput from Task2; they are not alternate transport admission APIs.

## Cost behavior

Exact depth walks BUY asks/SELL bids. Quantity base/contracts and quote-per-base/quote-per-contract multipliers are explicit; base quantity with non-one multiplier unsupported. Full two-sided ordered uncrossed books, every price tick, positive depth and legal candidate lot/max bounds required. Premium is visible separately when later fee/index/FX evidence fails. Fee=min(rate×index×baseQuantity, cap×premium), separately capped; BUY premium+fee, SELL premium−fee, then explicit FX. Fee currency must be quote currency; index currency and directed FX pair must match, same-currency FX must explicitly equal1. Metadata/fee known_ns and received/available cannot exceed cutoff; spec domain checked. Source ages use i128 cutoff−source against signed minAge/maxAge, tolerated source leads annotated SOURCE_CLOCK_LEAD; knowledge never leads cutoff. Missing sources/late/stale/excess clock lead/depth/units do not yield valid total.

## Expression/model behavior

Four exact all-BUY long shapes plus no-interest same-currency cash; linear European cash rule, common venue/underlying/expiry/index/window/ruleVersion/settlement currency and equal base quantity. Straddle same strike; strangle put strike below call. No inverse, short, unknown rules, path exit, IV/liquidity model or interim intrinsic shortcut. Exactly one scenario point at expiry and equal view horizon required. All scenario prices for every supplied quoted spec in the same fixing domain must agree, including non-candidate specs; conflicting price invalidates all gross/net/EV. Missing individual scenario delivery leaves unknown and prevents feasible complete-model claim.

Gross intrinsic remains visible without terminal fee; net PnL, EV, break-even and paid-loss bound remain null. Net PnL=gross−opening−explicit terminal fee; loss bound is complete paid reserve. Exact piecewise roots omit negative fixing prices and reject nonterminating rational roots as numerical limitation. Conditional EV only forecast/subjective/full nonnegative exact-one probability mass and complete net outcomes; stress/risk-neutral/empirical/incomplete/unweighted null.

Paid reserve must satisfy view capital/loss, per-intent, active+pending, tail sleeve limit and unique matching hypothetical standard-mode wallet. Accounts never pooled; ambiguity NOT_EVALUABLE. Every supplied balance must be report currency, and sum exactly initial capital (parent ruling fully allocated reference profile): missing allocation NOT_EVALUABLE, excess INVALID. Account/execution always UNVERIFIED. Descriptive modeled_candidate is not intent/order/reservation.

## Vertical proof

Exact call/put, one BUY/one SELL, equal legal base quantities, distinct strikes, common venue/fixing/rule and supported terminal spec. Signed intrinsic at knots0+sorted strikes; analytic slope for each following interval including infinite right tail, required zero tail. Full-domain extrema from these nodes give both signed directions. Displayed cost independent: unavailable costs preserve gross proof and null opening/conditional fields; explicit terminal fee enables bounds minus signed opening spend and reserve. Always SHORT_EXECUTION_UNSUPPORTED, financing/margin/account/execution unverified, no lifecycle edge or funded short intent. Uses explicitly crate-visible shared pure terminal/payoff helpers from expressions, not a funded-long approval path.

## Evidence

Observed RED with compiling zero/VALID placeholders: all initial7 integration groups failed on actual economic expectations (cost .0203, missing cost rejection, payoff/loss, reserve boundary, fixing rejection, vertical nodes and unequal/fixing rejection). A second observed RED added3 independently failing refinements: full-domain extra-spec price conflict, missing other leg preserving independent cost, and standard-mode wallet acceptance. A third observed RED specifically mixed-currency capital addition (erroneous VALID). Then implementations corrected each. Multi-case groups stop at their first failing assertion: not every subcase was independently observed RED. Probability-mode/put-vertical/per-contract regressions and numeric root subcase were added/verified GREEN and are not falsely labeled individual RED.

Final GREEN `cargo test --manifest-path rust/Cargo.toml --locked`:38 integration tests total, including13 new economics tests; crate and doc tests pass. `cargo fmt --manifest-path rust/Cargo.toml --all --check` and `cargo clippy --manifest-path rust/Cargo.toml --locked --all-targets -- -D warnings` pass.

Hand oracles: call .0203, SELL .0187; put PnL .0847; straddle .0356; subjective EV .0047/.0144; roots102.03,98.47,96.44/103.56,86.44/113.56; depth .26+.003=.263; contracts multiplier10 gives2.63; premium cap .00125; per-contract premium .04 fee .003 total .043; vertical knots0/100/110 values0/0/.1 slopes0/.01/0, opening−.0094 bounds.0094/.1094; reverse put signed nodes−.1/−.1/0 bounds−.1/0. Exact .0203 budget passes, .020299 rejects; nonzero terminal .001 independently tests view/global/tail/wallet/per-intent/capital rejection. Missing fees/spec knowledge/indexunits/FX pair/futureFX/depth/ticks/books/lots/staleness/clocklead/unsupported shapes/time/version tested. Nonterminating average-cost root returns explicit NOT_EVALUABLE.

## Commits and boundaries

735edcc fix fixture version;7a24dcb costs;1f4ba79 terminal expressions;76c284a vertical proofs;ef71d17 focused tests. Worktree clean. No financial defaults or actual returns asserted. Task4 report must retain fixed authority/null outputs; Task5 owns finite-domain Python parity/full Python gates. Panel sealed_proxy UNKNOWN specs remain model-unsupported while independently available displayed cost can still be diagnosed. This is diagnostic mathematical acceptance, no financing, trading, account or strategy acceptance.

## Review fix round 1

All3 Important findings corrected. Registered v1 call vertical requires BUY lower/SELL higher strike; registered put requires BUY higher/SELL lower. Reverse orientations UNSUPPORTED and no gross/conditional proof; independent displayed opening net cost retained. Canonical put nodes0/100/110 values.1/.1/0 slopes0/−.01/0. Previous reverse-put GREEN test was an incorrect acceptance assumption and has been corrected, not retained as an alias.

Terminal supported rules now include linear_cash and linear_via_future, still linear European same-currency/common-rule/fixing/equal quantities only. LinearViaFuture explicitly labeled CASH_EQUIVALENT_TERMINAL_ONLY_NO_FUTURE_EVENTS_OR_ACCOUNT_PERMISSION in expression/certificate reasons; real future creation, settlement chain, account/short permission remain unsupported/unverified. UNKNOWN and UNVERIFIED settlement-index sentinels are rejected case-insensitively after trimming as NOT_EVALUABLE fixing evidence; nonempty text alone no longer proves a fixing. Independent costs remain available without supported terminal fixing/proof.

Observed RED before implementation:3 separate new tests compiled and FAILED respectively on reverse orientation erroneously VALID, LinearViaFuture erroneously UNSUPPORTED, and UNKNOWN index erroneously VALID. New fixtures rebind exact descriptors/scenario hashes and pass Task2 load_analysis before model checks, proving no transport shortcut or stale identity drives those refusals. Final GREEN:41 workspace integration tests total (16 economics), doc/crate tests pass; fmt and clippy all-targets -Dwarnings clean. Extra independent opening-cost assertions were added as GREEN regressions, not individually RED.

Commits aba397d terminal fixing support;8b3b3d0 registered vertical orientation/independent opening costs;87cef6c focused review tests. HEAD87cef6c; clean worktree. No contracts/legacy paths/account/lifecycle capability added. This section supersedes the initial report's linear_cash-only and reverse-put acceptance statements.
