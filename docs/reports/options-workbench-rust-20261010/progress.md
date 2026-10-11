# SDD ledger — plan: docs/superpowers/plans/2026-10-10-options-workbench-rust.md

Base 99d3292; isolated branch codex/options-workbench-rust. User approved original plan, then requested Rust; subagent method retained. Planning amendment b6258bf.

Ruling: implement new D2 in native Rust with exact bounded decimals and a new schema/method identity — user explicitly changed language — no full-domain P1 arithmetic/serialization equivalence claim; Python P1 unchanged and serves as finite-domain oracle. Old panel loader reused only offline for extraction.
Ruling: worktree creation authorized by the approved implementation plan's isolation requirement — preserve dirty main — no new permission request.
Ruling: use GPT-6.1 sol workers (terra unavailable in model inventory), independent frontier final review — preserve user's requested specialist collaboration.
Ruling: keep exact arithmetic instead of rust_decimal fixed precision — Python P1 context50 cannot be reproduced by 96-bit amounts — rational division may report a limitation rather than rounding.

## Preflight interface/self-consistency table

| Producer / consumer | Shared interface | Finding / ruling |
|---|---|---|
| 1 / 2 | Request, view hashes, Dec serde | Rust schema distinct from old plan; freeze in Task1 report, preserve all original semantic fields |
| 1 / 3 | specs/quotes/status/result shapes | No P1 RiskApproval or ledger port; new ModeledFunding descriptive only |
| 1 / 4 | report inputs and serde | All identity/version fields frozen; null authority fields cannot come from input |
| 2 / 3 | AnalysisInput cutoff/history/scenario selection | retain full history; reject fixing conflicts at model layer, not transport ambiguity |
| 2 / 4,5 | sealed panel projection | exporter loads independently pinned P1 sources; Rust validates source bytes before admission; no authenticity claim |
| 3 / 4 | native evidence and constraint certificate | costs and mathematical validity separate from wallet/execution; no actual performance |
| 4 / 5 | CLI / parity harness | no Python runtime in Rust; Python only export/oracle tool; own Rust fixtures |
| 1 / self | arithmetic/struct limits/tests | reject exponents in new schema; no promise old general Decimal parsing; exact finite math |
| 2 / self | strict JSON/PIT tests | future rows retained but never selected; all nested duplicate keys rejected |
| 3 / self | payout and budget tests | same fixing consistency, terminal reserve and global/tail budget required beyond legacy opening-only checks |
| 4 / self | publication and deterministic report | no native rename fallback to overwrite; deterministic artifact identities exclude wall clock |
| 5 / self | old Python compatibility/full gates | full gate record may fail preexisting tests; no merge/repair waiver |

Baseline: isolated checkout Python options suite 427 passed, 1 skipped; one skip was reported; no Rust tests existed at baseline.

## Tasks

Task 1: pending (BASE b6258bf).
Task 2: pending.
Task 3: pending.
Task 4: pending.
Task 5: pending.

Ruling: raw file pin verification and exported projection attestation are separate — source SHAs plus a self-hash do not prove projected field semantics — expose RAW_PINNED_BYTES_VERIFIED only for original allowlisted seals and EXPORTED_PROJECTION_UNATTESTED for exported values; force proxy/UNKNOWN model limits and test tampered projection. Full native raw replay parser remains D1.

Preflight independent review completed (rust_plan_independent): raw/projection trust separation; both normalized numerator and denominator <=1024 decimal digits, guard before parse/operations, terminating output <=256fraction; bad numeric transport rejects request, candidate arithmetic limits preserve NOT_EVALUABLE; native parity includes cap/SELL/multiplier/wallet; no-replace publication fsync parent and anchored parent descriptor, post-rename fsync failure exit1 PUBLISHED_DURABILITY_UNCONFIRMED preserving output.

Ruling: optional panel transport separate16MiB limit (manual request remains1MiB); raw independently pinned seal files <=16MiB each, total<=64MiB and<=1024 entries across two roots. Existing panel has510 files/4,817,371bytes, scout31/3,820,877bytes; no need copy into worktree. P1 loader paths derive from Python source repo; real exporter run with explicit PYTHONPATH=original repository to preserve its existing hardcoded pins, never monkeypatch them in product. Rust native panel source verification uses only two known seal SHAs, not caller-authorized manifests.

Task1 implementer rust_task1_contracts: commitsc3f66fa..a2cc61f;11tests pass; review rust_task1_review requests fixes canonical generic nonfinitefloat->null and incomplete frozen report fields. Fixround1 dispatched same implementer. Ruling: recursive float rejection preferred; a narrow typed canonical API is acceptable if explicitly documented and prevents bad domain, no same-hash NaN/null acceptance.

Task 1: complete. HEAD9cd724f. Review fixround1 approved by rust_task1_review; canonical recursively rejects f32/f64 before toValue and frozen fields corrected.12tests green, fmt/clippy clean. Original individual RED process gap disclosed, no false evidence. Task2 BASE9cd724f.

Ruling: panel normalization may change spec hash — input descriptors/scenarios tied to pre-normalized claims are rejected, not rewritten — exporter publishes normalized sealed_proxy/UNKNOWN Rust spec hashes. Preserve original projectionSHA separately; no inferred price/spec translations.

Task2 HEAD1c9ee75 /23tests. Review rust_task2_review found2Important: clockleader erased despite signed policy; panel firstnativeID version matching order-dependent. Fixround1 same implementer dispatched. Ruling: knownreceived/available visibility separate fromsourceage; negative explicit minage retained; exactdescriptor across full spec history only, notfirstmatch.

Task 2: complete. HEAD0bd6728,25tests;2Important fixes approved. Fullhistory and signed sourceclock policy preserved; exact panel matching order independent. Task3 BASE0bd6728.

Ruling: template_version must be exactv1 only — approved template semantics; Task1 fixtures accidentally wrote1 — authorize Task3 narrowly correcting factoryspelling in separate fix(fixtures) commit, unknownversions remainUNSUPPORTED rather than two silent aliases. Contractfields unchanged.

Ruling: preserve exact fully allocated reference capital — sum hypothetical report-currency walletcash == View.initial_capital — missing allocation NOT_EVALUABLE, excessINVALID; no inference of free unallocated cash. An explicit future unallocatedcash field can extendmodel. Cash baselinev1/expiry_only/zerointerest, no-probabilityEVnull.
Ruling forTask4 source roots: use build/repository context (including linked-worktree commonGit root) for two fixed dataset paths, never derive arbitrarytrustedroots from inputpins. Mac realroots currently known. Linux optionalsealedrootmapping absent mustfailclosed; fixtureCLIportable, nativeLinuxpublish not runtimeverified onMac. Sourcepinlist compared with reconstructed allowlistedsealentries includingseals.

Task3 HEADef71d17 /38tests; independent economicreview rust_task3_review3Important (strictorientation, LinearViaFuture scope, UNKNOWNfixing). Fixround1 HEAD87cef6c /41tests. Ruling: originalregisteredV1orientation retained, no reverse proof; terminalviafuture conditionalcash equivalent kept no actualsettlementevents; UNKNOWN/UNVERIFIEDfixing refused, costs independent. Scopedreviewpending.

Task 3: complete. HEAD87cef6c,41tests, all3Important fixes independentlyapproved. Task4 BASE87cef6c.

Task4 HEAD96c0b1a /58 tests =41core+7unit+10CLI, fmt/clippy reported clean. Independent rust_task4_review dispatched; real exporter/full-panel path remains Task5.

Task4 review preliminary: original path ParentDir normalization may erase symlink component; heading newline may escape HTML block. Await final task-scoped findings before fix round. No completion/Task5 dispatch yet.

Task4 review rust_task4_review:3Important (heading blankline Markdown injection; lexical ParentDir erases symlink; operational publish errors propagate Invalid afterdirs). Same implementer fixround1 dispatched BASE96c0b1a.
Ruling: reject original caller input/output/panel ParentDir before normalization — preserve intended OS path/symlink safety — internal trusted gitdir/commondir resolution may remain separate; costs callers must provide canonical paths without parent hops.

Task4 fixround1 HEADb2ef3f0 /62tests, fmt/clippy reportedclean. 3 separate compiling RED/GREEN recorded. Scoped re-review rust_task4_review dispatched96c0b1a..b2ef3f0.

Task 4: complete. HEADb2ef3f0 /62tests. Scoped rust_task4_review approved all3findings, no newCritical/Important; Task5 BASEb2ef3f0.

Task5 running rust_task5_compat: actual oldmainloaderexport + nativefullpanel admitted480quotes/48specs/543pins; fixedunsupported model semantics retained, missingfeeclock/FX/indexcurrencyNone. Root independently3CLI demos/5artifact checks under results/options_platform/rust_d2_execution_20261010/root_demos withauthoritynull and exactmath. No new performance evidence.

Task5 HEAD3772897 delivered22newtests, Python459passed/1skip, Rust62/fmt/clippyclean reported. Independent rust_task5_review pending. Fullharnessone-run0/B/C/Dpass,A3oldformats/E1oldrootdiscoveryoptionerror/FmissingBTCparquet; exclusioncontrol reproducesEwithnewmoduleuncollected. Root checked21artifactSHAs+lengths and actualfullnativepanel480quotes543pins, outputsbyteidentical toTask5. No merge/promotion.

Task 5: complete. HEAD3772897; rust_task5_review approved noCritical/Important. DeferredMinor: actualpanel and relatedexporterfilesystemtests hardcodedMacroot skipelsewhere; requestedMac verified, independent portablecore/CLItests remain. Finalwholebranch reviewer willtriage.

Root freshverification HEAD3772897: Cargo fmt/test--locked(62)/clippyalltargets-Dwarnings,newRuffcheck/format,Pythonoptions+boundaries+parity459passed1skip—all exit0. logsresults/options_platform/rust_d2_execution_20261010/root_verification. Repeat23P1+110mainprotectedSHAunchanged,6oldCLIartifactsbyteidentical. Finalwholebranch review rust_final_review pending.

Finalwholebranchreview rust_final_review HEAD3772897:2Important — zero-leg cashblocked byadmission despitecashmodel; publicmutableAnalysisInput bypassPIT/history/scenariobinding/nonemptyat analyzer/certifier/report. ONEfinalfixwave freshfrontier rust_final_fixes dispatched BASE3772897. DeferredMinor Maccorpus-tiedexporter safetytests; accepted userMacscope, portableRusttests retained.
Ruling: enforce shared semantic admission at public evaluation/report boundaries without changing frozen field semantics — preserve reusable-library PIT correctness, not only CLI correctness — costs validation overhead and legitimate test mutations need rehash/readmission.
Finalreviewdeclined: oldfullharnessred ismergeblock, Linuxruntimeunverified, projectionsemanticattestation D1, realPnL/accounts/fills/margin/completedsettlementunknown, fullP1equivalenceunsupported, archivalcontrollerpending. Allremain explicitlimits.

Finalwholebranchscoped re-review approved3772897..bc73f82,2ImportantADDRESSED,no newCritical/Important. D2acceptanceapproved; fullharnessA/E/Fmergeblocked, no waiver. Root finalfreshverificationbc73f82 cargo70/fmt/clippy, Python459passed1skip,23P1+110mainSHAunchanged. Preservebranch/worktreeperapprovedplan, no merge/push/deploy.
