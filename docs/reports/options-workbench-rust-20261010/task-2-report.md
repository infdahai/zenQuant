# Task 2 report — strict causal admission

DONE. Base9cd724f; only new codec/inputs modules, lib exports, focused tests and this report changed. No old Python, I/O, network, clock or unsafe APIs.

## APIs and schemas

`codec::decode<T:DeserializeOwned>(&[u8],limit:usize)->Result<T>` uses a recursive Serde seed/visitor before constructing any map: nested duplicate keys, all floats, trailing data, depth>64, string/key>4096 and generic collection>4096 fail. Byte cap checked first. Fixed safe errors, no input echo. Booleans remain valid JSON but typed numeric/money fields reject them. Decimal admission inherited exact Task1 string decoder.

`inputs::decode_request(&[u8])->Result<Request>` enforces1MiB, request schema and typed collection limits. `load_analysis(Request)->Result<AnalysisInput>` performs economic/causal validation and retains all incoming typed views and quotes, including future evidence. `select_view(&[View],&str,u64)->Result<View>` validates every full chain, contiguous1-based revisions, exact canonical parent links and monotone clocks, then selects latest received/available/original-cutoff-visible version. `select_quote(&[QuoteInput],&str,u64)->Result<Option<&QuoteInput>>` selects latest source/received/available-visible quote, never lowest price, rejecting tied conflicting contents. Future spec/fee/FX timestamps retained for Task3 availability diagnosis.

Admission validates schema/method, selected scenario binding, identity collisions, exact available descriptor/spec fields, clock order, structural specs/currencies, positive strike/multiplier/lot/step/observations, lot bounds, ordered zero-origin tick bands with positive ticks, ordered uncrossed book, nonnegative fee/terminal fee/risk/wallet amounts, complete normalized probabilities, scenario time/hash/value uniqueness, source and wallet identities. Missing exact spec/fee/metadata evidence stays typed NOT_EVALUABLE gaps. Unknown template strings and original candidates remain unchanged for Task3. Source/wallet labels always receive UNVERIFIED authority gap.

`decode_panel(&[u8])->Result<PanelEnvelope>` has separate16MiB cap. `apply_panel(Request,PanelEnvelope)->Result<AnalysisInput>` is pure: it does NOT verify files or grant RAW_PINNED_BYTES_VERIFIED. Accepts exact `PANEL_SCHEMA=options-workbench-panel.rust.v1`, `PANEL_EXPORTER=options-workbench-exporter.rust.v1`, validates `projection_sha256=canonical_hash(quotes)`, pins shape/size<=16MiB each,total<=64MiB,count<=1024. Fixture/panel or manual-quote/panel conflicts fail. Forces spec sealed_proxy, UNKNOWN exercise/settlement and observation/fee sealed_proxy; retains original projection SHA under source identity `panel_projection_sha256`, labels EXPORTED_PROJECTION_UNATTESTED. Root ruling: descriptor and scenario hashes must already bind normalized UNKNOWN specs; pre-normalization identities fail, never silently translated. Exporter must emit normalized specs using these exact schema/hash versions. Raw pin verification remains CLI responsibility.

## RED/GREEN evidence

Observed meaningful RED: economic_admission compiled and failed zero multiplier assertion with initial passthrough implementation. Observed additional RED: panel_tamper_conflict_and_transport failed its pre-normalization descriptor rejection assertion, fixed by exact panel descriptor/scenario matching. Codec regression tests were introduced with visitor already implemented (not independently RED; disclosed). Other negative/positive cases are GREEN regressions.

GREEN: locked workspace tests23 total (11 Task2 +12 existing); cargo fmt; cargo clippy all-targets warnings-as-errors. Cases cover recursive duplicate/float, bool integer/extra authority/schema/trailing/depth/size, zero multiplier, negatives, tick conflicts, altered descriptor, probability completeness, full future history100/120 and parent corruption, retained future fee/spec/FX/book, quote conflict versus venue-distinct native IDs, unknown template preserved, panel selfhash tamper/conflicts/16MiB, normalized proxy identity and unverified projection.

## Boundaries

Task3 must evaluate actual lot/tick fill legality, stale/max-age policies, late metadata/fee/FX, unsupported unit/product paths, same-fixing delivery conflicts, terminal outcomes and risk budgets. Admission preserves missing observations rather than inventing values. Panel semantic authenticity is deliberately unverified even after CLI verifies original pinned bytes. No account proof, report publication, promotion, merge, push or deployment claimed.

Commits552d525 implementation; f219b22 tests;1c9ee75 equivalent clippy style cleanup. Report kept in ignored SDD working directory, matching task ledger convention.

## Review fix round 1

Both Important findings corrected without interface changes. Structural observation/book clocks require received<=available; source may lead receiver. Timing minimum_source_age_ns may be explicitly negative, must be<=max_age_ns (checked via i128 to avoid narrowing). Risk FX timing same. Quote selection visibility uses received/available; source age and allowable lead remain Task3 cost checks. Both tolerated source11 and excessive source20 against cutoff10 remain admitted/selected with minAge-2, so excess is not silently hidden; index/FX timestamps retained too.

Panel descriptor matching now checks exact full descriptor against all retained versions for same venue/nativeID, independent of array order. Pre-normalized hashes continue rejected; scenario hash validation unchanged.

RED: both newly added tests compiled and FAILED on lead admission (INVALID_INPUT) and second-version panel binding (INVALID_IDENTITY). GREEN:25 locked workspace tests (13 inputs), fmt and warnings-as-errors clippy pass. Source lead regression includes invalid minAge101>maxAge100 rejection; panel two-version regression runs both orders.

Commits5fdd106 source-clock fix; d7aac11 full-history descriptor fix;0bd6728 focused tests.
