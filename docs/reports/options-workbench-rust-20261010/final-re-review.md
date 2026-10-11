# Scoped final re-review

Range: `3772897..bc73f82725347c2f4fc5b3dedbf4679b78c288ef`.
Read-only source review of the complete supplied fix diff and final-fix-report.md. No source changes, suite reruns, or subagents. This file is review metadata only.

## Findings disposition

1. **Cash request pipeline — ADDRESSED.** `inputs.rs:264` now admits the zero-leg cash shape. Non-cash empty legs remain invalid; unknown cash versions still reach explicit UNSUPPORTED rather than aliasing v1. New public-boundary and actual CLI tests cover decode/admission/model/report and zero conditional outcomes with authority NONE.
2. **Public causal validation bypass — ADDRESSED.** `ValidatedAnalysis` has a private borrowed input, reconstructs through the existing load_analysis admission, and checks the selected view against validated history. Public expression and vertical functions require its successful construction; handle methods enforce exact candidate membership. Failed admission precedes economic calculations and yields INVALID without funding, modeled candidate, net outcomes, EV or valid certificate. Public report render validates once before building artifacts and evaluates via the same handle. Safe Rust callers cannot mutate the borrowed input while using the handle. New tests cover late/unbound/empty scenarios, view/history mismatch, malformed metadata/specs, foreign candidates, deserialized inputs and report rejection.

## New Critical / Important findings

None in the fix diff. Economic fixture changes honestly rebind/readmit changed views and keep missing-fee knowledge consistent. They do not weaken the production validation boundary. Adapter-specific gaps remain diagnostic annotations; bytewise equality with reconstructed loader gaps is neither required nor treated as authority.

## Assessment

**D2 review acceptance: approved.** Both previously reported Important findings are addressed; no remaining Critical/Important findings from this review. Fresh final-HEAD verification and evidence handoff remain controller work. The implementer reports 70 Rust tests/fmt/clippy, 459 Python tests/1 skip, fresh 480-quote/543-pin native panel runs and preservation checks; this reviewer did not rerun those suites.

**Merge readiness: No.** Previously recorded full-harness A/E/F blockers remain; no repair waiver, merge, push, deployment or promotion is implied.

## Declined / deferred

- Previously identified Mac-corpus-dependent exporter safety coverage remains a deferred Minor; outside the two-finding fix wave.
- Preexisting full-harness failures were not re-diagnosed in this scoped review.
- Linux runtime was not executed; this review does not add Linux acceptance.
- Raw pin integrity remains distinct from projection semantic authenticity.
- Real profitability, account eligibility, fills, margin/financing, settlement completion and full-domain P1 equivalence remain outside this D2 acceptance.
