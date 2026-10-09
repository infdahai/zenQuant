# Execution rulings

Ruling: implement new D2 in native Rust with exact bounded decimals and a new schema/method identity — user explicitly changed language — no full-domain P1 arithmetic/serialization equivalence claim; Python P1 unchanged and serves as finite-domain oracle. Old panel loader reused only offline for extraction.

Ruling: worktree creation authorized by the approved implementation plan's isolation requirement — preserve dirty main — no new permission request.

Ruling: use GPT-6.1 sol workers (terra unavailable in model inventory), independent frontier final review — preserve user's requested specialist collaboration.

Ruling: keep exact arithmetic instead of rust_decimal fixed precision — Python P1 context50 cannot be reproduced by 96-bit amounts — rational division may report a limitation rather than rounding.

Ruling: raw file pin verification and exported projection attestation are separate — source SHAs plus a self-hash do not prove projected field semantics — expose RAW_PINNED_BYTES_VERIFIED only for original allowlisted seals and EXPORTED_PROJECTION_UNATTESTED for exported values; force proxy/UNKNOWN model limits and test tampered projection. Full native raw replay parser remains D1.

Ruling: optional panel transport separate16MiB limit (manual request remains1MiB); raw independently pinned seal files <=16MiB each, total<=64MiB and<=1024 entries across two roots. Existing panel has510 files/4,817,371bytes, scout31/3,820,877bytes; no need copy into worktree. P1 loader paths derive from Python source repo; real exporter run with explicit PYTHONPATH=original repository to preserve its existing hardcoded pins, never monkeypatch them in product. Rust native panel source verification uses only two known seal SHAs, not caller-authorized manifests.

Task1 implementer rust_task1_contracts: commitsc3f66fa..a2cc61f;11tests pass; review rust_task1_review requests fixes canonical generic nonfinitefloat->null and incomplete frozen report fields. Fixround1 dispatched same implementer. Ruling: recursive float rejection preferred; a narrow typed canonical API is acceptable if explicitly documented and prevents bad domain, no same-hash NaN/null acceptance.

Ruling: panel normalization may change spec hash — input descriptors/scenarios tied to pre-normalized claims are rejected, not rewritten — exporter publishes normalized sealed_proxy/UNKNOWN Rust spec hashes. Preserve original projectionSHA separately; no inferred price/spec translations.

Task2 HEAD1c9ee75 /23tests. Review rust_task2_review found2Important: clockleader erased despite signed policy; panel firstnativeID version matching order-dependent. Fixround1 same implementer dispatched. Ruling: knownreceived/available visibility separate fromsourceage; negative explicit minage retained; exactdescriptor across full spec history only, notfirstmatch.

Ruling: template_version must be exactv1 only — approved template semantics; Task1 fixtures accidentally wrote1 — authorize Task3 narrowly correcting factoryspelling in separate fix(fixtures) commit, unknownversions remainUNSUPPORTED rather than two silent aliases. Contractfields unchanged.

Ruling: preserve exact fully allocated reference capital — sum hypothetical report-currency walletcash == View.initial_capital — missing allocation NOT_EVALUABLE, excessINVALID; no inference of free unallocated cash. An explicit future unallocatedcash field can extendmodel. Cash baselinev1/expiry_only/zerointerest, no-probabilityEVnull.

Ruling forTask4 source roots: use build/repository context (including linked-worktree commonGit root) for two fixed dataset paths, never derive arbitrarytrustedroots from inputpins. Mac realroots currently known. Linux optionalsealedrootmapping absent mustfailclosed; fixtureCLIportable, nativeLinuxpublish not runtimeverified onMac. Sourcepinlist compared with reconstructed allowlistedsealentries includingseals.

Task3 HEADef71d17 /38tests; independent economicreview rust_task3_review3Important (strictorientation, LinearViaFuture scope, UNKNOWNfixing). Fixround1 HEAD87cef6c /41tests. Ruling: originalregisteredV1orientation retained, no reverse proof; terminalviafuture conditionalcash equivalent kept no actualsettlementevents; UNKNOWN/UNVERIFIEDfixing refused, costs independent. Scopedreviewpending.

Ruling: reject original caller input/output/panel ParentDir before normalization — preserve intended OS path/symlink safety — internal trusted gitdir/commondir resolution may remain separate; costs callers must provide canonical paths without parent hops.

Ruling: enforce shared semantic admission at public evaluation/report boundaries without changing frozen field semantics — preserve reusable-library PIT correctness, not only CLI correctness — costs validation overhead and legitimate test mutations need rehash/readmission.

## Impact if a ruling must change

1. **原生Rust新schema，P1保持不变**：若需要全面P1兼容，需要额外适配而不能复用新哈希。
2. **独立worktree内执行**：集成前需要处理原分支已有工作与全仓门禁。
3. **terra不可用，使用6.1 sol与独立frontier复核**：模型选择不等于研究证据，验收仍靠可重现实验。
4. **精确有界有理数，不采用固定精度舍入**：不终止除法和超界结果会明确不可评估。
5. **原始字节校验和投影语义分开**：历史投影不能成为账户或执行权限证明。
6. **1MiB请求／16MiB面板／64MiB来源及原根P1导出**：更大语料需显式版本或分块；封存根不可任意指定。
7. **递归拒绝浮点，不容忍NaN→null**：一般浮点序列化不能直接用于此哈希协议。
8. **规范化前后hash不匹配时拒绝，不静默改写描述符**：旧请求需要重绑规范化后的spec/scenario身份。
9. **received/available可见性与有符号source age分开**：采样政策改变可能改变样本覆盖，原始时钟必须保留。
10. **严格template_version=v1，不接受旧错误拼写1**：错误或未来版本显示不支持，不能靠别名继续使用。
11. **参考钱包总额必须完整等于初始资本**：需要显式分配现金；现金基线无利息，无概率EV未知。
12. **源码/仓库上下文固定来源根，不信任输入pin路径**：无可信源映射时面板不能入场，Linux根与运行需另验。
13. **规范vertical方向；via_future仅到期现金等价；未知fixing拒绝**：反向结构、真实期货交割和额外融资保证金尚未实现。
14. **拒绝原始调用路径中的..**：调用者需提供不含父目录跳转且无symlink的路径。
15. **公开计算与报告共用因果准入**：批次有一次有界验证；修改观点/候选后必须重新绑定并准入。
