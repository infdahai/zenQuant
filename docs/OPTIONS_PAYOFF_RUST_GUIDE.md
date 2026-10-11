# Svelte + Rust 期权情景 MVP

本轮入口是独立的 `options-ui/` Svelte 客户端与 Rust `options-ui` 二进制。旧 `web-src/` Vue 和 Python 项目保留作迁移参考，没有复制旧仪表盘或建立双主线。没有 Go、Python 运行依赖、回测、实时行情订阅、OMS、成交、账户风控或下单功能。

## 运行

需要 Rust 1.90.0（`rust/rust-toolchain.toml` 固定）、Node 24 与 npm。Node 仅用于构建和测试，页面构建后由 Rust 直接服务。

```sh
npm ci --ignore-scripts --prefix options-ui
npm run build:options
npm run start:options
# 等价运行：./rust/target/release/options-ui --port 8092
```

浏览器访问 **运行机器自身** `http://127.0.0.1:8092/`。服务仅监听 IPv4 loopback，不是公网演示地址；云容器地址不能直接在 Android 手机上打开。本轮没有部署、隧道或对外共享。静态资源在启动时读取，改前端须重新构建并重启二进制。

不要使用原 `npm run web`、`web:demo` 或原 Playwright 配置启动本 MVP，它们属于旧 Python 路径。源码、Node 构建依赖与测试工具不属于运行时计价引擎。

## 交互与金融边界

- 默认页明确标记合成数学样例：ETH/USDC、线性欧式 BUY 单腿，基础资产数量，合成截止时点 10 ns、到期 1000 ns。这两个时间不代表现在；样例不是实时信号。
- 控件可以改变 Call/Put、行权价、数量、显式双边报价、费用和价格网格。合成双边深度等于输入数量；指数固定 100；费用上限固定为权利金的 12.5%，都在页面说明。金额以字符串提交，由 Rust 精确计算。修改参数清除旧结果并在 220 ms 后发起**一次批量请求**。
- 横轴是到期标的价格，纵轴是报告币种金额。毛收益是期权到期支付，净损益是支付减输入的开仓成本和到期总费用。没有时间净值曲线、当前盯市、期望收益、概率、拟合或成交承诺；也没有额外预测滑点。
- 缺开仓或到期费用、缺有效报价时保留可计算的毛收益，净损益显示不可评估。矛盾证据、非法数量和错误身份拒绝。模型支持 VALID 仍与账户和执行 UNVERIFIED 分开。
- 可选信号是 `manual-scenario-signal.rust.v1` 的人工假设、来源身份、观察/已知/截止时点。人工输入绑定当前明确候选和价格假设，不自动将方向转换为合约或数量；即使时点有效也保持 UNVERIFIED。过期/尚未知信号显示不可评估，独立期权数学仍可计算。
- “完整证据 JSON”模式原样提交文本，保留大于 JS 安全整数的纳秒时间与精确金额。新请求 schema 是 `options-payoff-curve-request.rust.v1`；`candidate`、`grid` 为 null 时返回明确缺项，不补造默认交易。完整结构可从数学样例响应取得并显式替换；更换规格必须同步 candidate descriptor、报价规格及 grid spec hash。
- 原 Python `IndicatorPack` 有版本/formula_hash/cutoff，`DirectionalBias` 有方向/置信度；它们没有本入口要求的独立候选和情景边界。本轮未确认可用实时来源，因此没有把旧置信度当成盈利概率，也没有创建伪实时指标适配。

## 实现与契约

核心仅新增 `options-core/src/payoff_curve.rs`：精确价格网格 `first + step × i`，2–4096 点，最多 4 份报价。既有 `terminal_diagnostics` 每批最多 64 点，新增入口分批调用相同函数；没有新计价公式、缓存或可替代策略接口。数学开销实测后再决定是否值得提取一次性计算成本的准备步骤，当前不为未来优化增加抽象。

旧 View 必填 v1、旧独立终值 v1 的未知字段拒绝、64 情景上限、时点和费用验证均不放宽。新入口只允许线性现金结算；其余产品/SELL/非到期时点沿用严格模型结果。`Report.diagnostic` 是首批公共成本/支持证据，outcomes 移到完整 `points`；`input_hash` 和 `grid_hash` 绑定整个新请求及全部网格。不同批次出现不一致计算状态时整体拒绝，避免首批成功掩盖后续数值失败。

`curve_api.rs` 只做合成输入转换、严格解码、计时和身份封装。API 响应包含精确完整请求文本、规范输入/网格/信号 hash、原始传输 hash、源码 hash、Cargo.lock hash。所有 hash 表示身份或完整性，不认证市场来源。`calculation_us` 不属于确定性金融结果；每次重新算，不缓存。

`curve_http.rs` 使用 `httparse` 解析有限 HTTP/1.1 子集和标准库 TCP。最多两个固定连接处理线程，避免一个慢连接阻断交互；无每请求线程、应用任务队列或调度器。8 KiB 请求头、1 MiB body、3 秒读入总期限及写超时；严格 Host、同源 Origin、自定义请求头，拒绝分块、重复头、跨源和任意文件路径。静态构建资源启动时读入内存，仅 HTML/JS/CSS，16 MiB/128 文件上限且拒绝资源内符号链接。

这是同机开发适配器，未声称公网服务能力、硬 CPU 截止、身份认证或抗恶意本地进程。浏览器取消和序号防覆盖保护交互；已经进入 Rust 的有界计算可能继续到完成，不声称服务器抢占取消。保留两线程的标准是慢/取消连接不垄断页面、线程数固定；若去掉仍满足实际交互，可退回单线程。

Svelte 只做状态和 Canvas 视图。4096 点一次绘图，不创建每点 DOM；只显示选中情景。JS Number 仅映射像素，超范围/无法区分横轴时不画误导曲线，金额文本和导出仍使用 Rust 小数字符串。没有 SvelteKit SSR、Go 控制面、WASM、外部图表库或生产缓存。

原报告原子发布与封存来源校验路径不变。新 HTTP 是内存中的条件诊断，不写模拟资金存储、不读取封存数据集、不提供任意导入文件 API。浏览器导出 JSON 不是原 CLI 的原子报告发布，也不授予交易权限。

## 检查与扩展

```sh
cd rust
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
cargo test --workspace --all-features --locked
cargo run --release --locked -p options-workbench --example curve_baseline
cd ..
npm ci --ignore-scripts
npm --prefix options-ui run check
npm --prefix options-ui test
npm run build:options
npx playwright install chromium
npm run test:options
```

本环境 Chromium 可指定 `ZQ_BROWSER=/usr/bin/chromium`；必要时为 `XDG_CACHE_HOME`、`XDG_CONFIG_HOME` 选择可写目录。CI 使用 Playwright 自带 Chromium。浏览器证据默认 `.artifacts/payoff-ui/`，可设置 `ZQ_EVIDENCE_DIR`。只用新 `playwright.options.config.js`，不启动 Python。

新增 Rust 原生用例覆盖：每点与旧模型一致、手算值、缺费用/报价/候选/网格、手工信号时点与数学独立、精确微小步长 4096 点、旧 v1 拒绝新请求、未知/重复字段、身份/边界失败，以及实际 TCP Host/Origin/长度拒绝、纳秒往返。前端单元测试使用真实请求协调器验证乱序/取消/失败恢复；浏览器使用实际 Rust 服务检查成功、调参、缺证据、错误恢复和手机宽度。

旧 Vue `npm run test:store` 基线因已删除的 `zenquant/web/app/storeCore.mjs` 入口失败。本轮按用户最终 Svelte 范围保留旧应用，不恢复废弃实现；新验收入口是 `test:options`。没有运行旧 Python 测试来充当 Rust 验证。

下一最小步骤应由实际操作反馈确定：例如把一个有已知来源和时间的当前候选/报价快照接入严格 JSON 入口。先验证来源、币种、规格及费用完整性，再考虑自动指标适配；不要先建回测或执行平台。
