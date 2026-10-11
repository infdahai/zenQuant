# Svelte / Rust 条件盈亏 MVP 验收

本轮从 `622512493e73a34707691cbd43eb907ba3b4ff0a` 开始，原开发分支 `codex/options-rust-iteration-1`。实现及 CI 定义提交为 `d02221e`（完整 SHA 见 `validated-code-commit.txt`），后续本报告提交只包含文档和证据。最终发布提交及其精确 SHA CI 结果由交付回复提供。本轮没有 PR、合并、部署、账户或订单操作。

用户中途取消回放方向后，其未提交原型已从工作树完整移除，保存在本云 clone 的 stash `da20691523a6a88367cc989c11af22ed69095bb6`，没有推送回放代码。后续 Vue 暂定方案未产生代码；最终新增 Svelte 入口，旧 Vue 应用未改。此 stash 是本地可恢复记录，不是交付模块。

## 可见交付

真实 Rust 服务加实际 Chromium 渲染的截图，不是设计稿：

- [默认数学样例](browser/01-default.png)：1001 点；到期价格 100，毛收益 0，条件净损益 -0.0213 USDC。
- [修改行权价及报价](browser/02-changed.png)：行权价 110、卖一价 3；对应情景净损益 -0.0313。
- [缺到期费用](browser/03-missing-fee.png)：仍显示毛收益，净损益 NOT_EVALUABLE。
- [390px 手机尺寸](browser/04-mobile.png)：缺费用与过期人工信号可见，无横向溢出。这是桌面 Chromium 的尺寸模拟，未在实体 Android 上运行。
- [4096 点曲线](browser/05-large-curve.png)：快速连续编辑后最终卖一价 6，旧响应不能覆盖新结果。

启动方式见 [运行指南](../../OPTIONS_PAYOFF_RUST_GUIDE.md)。云容器 loopback 不是手机可访问地址。没有生成公网预览 URL，也没有向 Library 上传。

## 测试

| 命令 / 检查 | 结果 |
|---|---|
| 基线 `cargo test --workspace --all-features --locked` | 155 个原生测试通过；见 rust-baseline.log |
| `cargo fmt --all -- --check` | 通过；fmt.log 为空表示无格式差异 |
| `cargo clippy --workspace --all-targets --all-features --locked -- -D warnings` | 通过 |
| `cargo test --workspace --all-features --locked` | 165 个原生测试通过，0 失败 |
| `cargo test -p options-workbench --test curve_http --locked` | 4 个实际 TCP / 适配回归通过 |
| `npm --prefix options-ui run check` | svelte-check 0 错误、0 警告，包含组件 TypeScript 检查 |
| `npm --prefix options-ui test` | 2 个乱序、取消、失败恢复用例通过 |
| `npm run build:options` | Svelte 检查、Vite 生产构建和 Rust release 二进制构建通过 |
| `ZQ_BROWSER=/usr/bin/chromium npx playwright test --config playwright.options.config.js` | 3 个实际浏览器测试通过，最终 5.6 秒；见 browser-final.log |
| `env PATH=/nonexistent ./rust/target/release/options-ui --port 8092` | 成功启动，实际浏览器测试连接此进程；服务不依赖外部命令或 Python |
| 既有四组封存证据 `sha256sum --check --status SHA256SUMS` | 全部通过 |
| `git diff --check` | 通过 |

所有 Rust 命令在 `rust/` 中运行，工具链 Rust/Cargo 1.90.0。Node 24.19.0，npm 11.9.0。云环境 Playwright 使用可写 XDG 缓存/配置目录；这只是浏览器环境配置。

初次测试揭示并修复：合成适配器未提供双边盘口导致净结果被旧规则拒绝；缺开仓费率时残留 fee_known_ns 导致严格解码拒绝。现已显式接收买一价，并同步删除缺失费率的已知时点，没有削弱证据校验。浏览器的一处 textarea 文本断言改为 value 断言。中途失败日志仍保存在 `/workspace/zenquant-payoff-validation/`，本目录保留最终通过日志。

新增 httparse 改变 Cargo.lock 身份。旧三个报告 golden 先断言当前 lock hash 等于实际 Cargo.lock，再将 code/lock 身份归一为原封存基线比较，既有金融结果和证据语义 hash 不变；没有修改旧封存报告、manifest 或放宽原 View-required v1。

## 性能实测

AMD EPYC 9V74 云容器；可见 5 个逻辑 CPU，cgroup 配额相当于 4 CPU，内存上限 16 GiB。标准 release 优化，没有 CPU 绑核、专属实例或真实行情。独立 Rust 基线先预热一次，再测 15 次完整纯 `payoff_curve::analyze`；**包含精确运算、验证和核心 hash，不含传输 JSON、HTTP 或绘图**。

| 点数 | 中位耗时 | 最小–最大 | 约点/秒 |
|---:|---:|---:|---:|
| 2 | 0.278 ms | 0.229–2.585 ms | 7,194 |
| 64 | 0.590 ms | 0.560–1.863 ms | 108,474 |
| 512 | 4.582 ms | 4.101–5.734 ms | 111,741 |
| 4096 | 36.195 ms | 35.381–38.940 ms | 113,164 |

命令：`cargo run --release --locked -p options-workbench --example curve_baseline`；最后稳定采样直接执行 `./rust/target/release/examples/curve_baseline`。原始值见 rust-benchmark.csv。这是当前简单分批基线，不宣称生产吞吐 SLA。`/usr/bin/time -v` 不可用，所以没有报告峰值 RSS，也没有用估算替代内存实测。

独立浏览器单次 4096 点样本（Chromium 154，无 CPU 降速）：Rust 37.676 ms，HTTP 响应读取 46.2 ms，345,171 字节，JSON.parse 1.3 ms，Canvas 绘制 2.6 ms。连续输入至第二动画帧代理值约 19.7–30.2 ms，**不是标准 INP 指标**。首次加载观测到 61 ms 长任务；其他开发轮次有约 99–122 ms，不能宣称整页无卡顿。浏览器原始记录见 [browser-performance.json](browser/browser-performance.json)，与 Rust 吞吐分开报告。

## 改动、克制与剩余范围

代码清单见 `code-files.txt`，主要新增 `payoff_curve`、`curve_api`、`curve_http`、Rust `options-ui` 二进制、`options-ui/src` Svelte 客户端、新原生/浏览器测试及 CI。文档更新为 `docs/RUST_MODULE_BOUNDARIES.md`、`docs/OPTIONS_PAYOFF_RUST_GUIDE.md` 和本报告目录。

没有预计算缓存、异步运行时、微服务、通用信号 DSL、多交易所适配、SvelteKit、WASM 或 Go。仅保留两个固定连接线程，处理慢连接时不让单连接垄断 UI；纯引擎仍同步。新 Canvas 是为了大曲线不产生大量 DOM。220 ms 防抖与 abort/序号是为了输入流畅和旧结果不覆盖，不是改变金融逻辑。保留/移除标准见运行指南。

没有回测、净值曲线、当前盯市、真实市场馈送、盈利概率、实盘风控/成交/账本或账户接入。人工信号只是版本化情景假设；下一最小步骤是依实际用户反馈，接入一个有明确来源/时点的候选和报价快照并验证费用及规格。

未运行：旧 Python 测试、旧 Vue 端到端环境、实体 Android、跨公网与恶意负载测试、真实交易所性能测试。旧 Vue 的 `npm run test:store` 基线仍因废弃 storeCore.mjs 入口缺失失败；按最终 Svelte 范围没有重建废弃实现，新入口的测试是 `npm run test:options`。没有阻塞本 MVP 的未完成开发项；对用户开放远程交互演示仍需单独决定展示或部署方式。
