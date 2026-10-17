# VIX 风险观察：Rust v1

这是独立、只读的波动率观察模块。它分别报告月度 VX 期货期限结构和 VIX9D/VIX 指数期限比较，不产生意图、风控许可、交易订单、预测或回测。SPX 波动率不能自动等同于加密资产风险。本轮没有实时数据订阅，也不申请交易密钥。

## 官方定义与数据边界

2026-10-17 核实的 Cboe 一手资料：

- [Term Structure](https://www.cboe.com/tradable-products/vix/term-structure)：VIX9D 对应 9 天，VIX 对应 30 天；VX 期货反映不同未来到期日的 VIX 预期。页面上 SPX 隐含期限结构表不是 VX 两合约报价。
- [VX 合约规格](https://www.cboe.com/tradable-products/vix/vix-futures/specifications/) 与 [VX fact sheet](https://cdn.cboe.com/resources/vix_options/VIX_fact_sheet.pdf)：有月度与周度到期；到期合约交易在结算日 Chicago 时间 08:00 结束。
- [官方日结算表](https://www.cboe.com/markets/us/futures/market-statistics/settlement/futures/daily/)：本轮只核对符号和日期，`VX/V6` 2026-10-21、`VX/X6` 2026-11-18、`VX/Z6` 2026-12-16、`VX/F7` 2027-01-20，以及周度 `VX43/V6` 2026-10-28。未把网页价格采集成实时行情。

固定夹具 `rust/options-workbench/fixtures/vix/demo.json` 使用这些已知日期与显式 UTC 毫秒交易截止值；日历提供者、known 时点、完整性、报价及延迟均是 **DEMONSTRATION / UNVERIFIED**。价格 24/20、指数 27/24 等是虚构值。来源 hash 是演示声明的身份（`SHA256("ZenQuant vix demo v1: " + label)`，label 是 reference 去掉 `DEMONSTRATION: ` 前缀与 `; invented prices, no licensed feed` 后缀后的内容），不是 Cboe 响应的 hash。整份输入另有规范 hash。

没有授权数据源时页面只显示演示夹具。读取公开说明不证明实时行情授权、完整性或可交易性。2026-10-13 交易所出口 CONNECT 403 是另一个任务的历史记录，本轮没有重试那些端点，没有修改代理或绕过网络限制。

## 运行与接口

```sh
npm ci --ignore-scripts
npm ci --ignore-scripts --prefix options-ui
npm run build:options
./rust/target/release/options-ui --port 8092
```

访问 `http://127.0.0.1:8092/risk`。使用现有 loopback Rust 服务与 Svelte 构建，不需要 Python、Go、额外服务或账户设置。

- `POST /api/vix/demo`：严格对象 `{"scenario":"BACKWARDATION"}`；还支持 `CONTANGO`、`FLAT`、`MISSING`、`STALE`、`ASYNCHRONOUS`、`ROLL`，均为固定构造。
- `POST /api/vix/evaluate`：完整严格 `vix-observation-request.rust.v1` JSON；字段样例见夹具。`mode` 仅允许 `DEMONSTRATION` 或 `SUPPLIED_SNAPSHOT_UNVERIFIED`。
- HTTP 继承 Host/Origin、JSON、`X-ZenQuant-Request: 1`、长度、超时及请求头检查；不接受文件路径、任意 URL、LIVE 模式或交易动作。
- 语法、未知/重复字段及全局 schema/时间范围错误返回 422。某一面板证据不足或不一致时返回 200 的结构化 `MISSING` / `STALE` / `INVALID`，该面板 `metrics:null`，独立面板仍能计算。
- 响应含原始类型的精确 `request_json`、规范输入 hash、传输字节 hash、Rust 源码/演示夹具构建 hash、Cargo.lock hash。导出 JSON 包含全部报告、日历、报价、时区和来源。浏览器不重复计算金融公式。

## 不变量

1. **明确期限。** VX1/VX2 只是当前选择位置，必须保留真实符号。v1 仅支持 CFE 形式的月度 `VX/<月码><年份末位>`、周度 `VX01..53/<月码><年份末位>`，周度不参与选择；连续合约别名、迷你 VXM 及其他供应商符号应由未来已验证适配器转换，不能猜测。
2. **显式日历。** `first_delivery_month` 声明有效区间开始时的首个月度合约，列表必须从该月开始且连续。按月份排序后选择 `last_trade_ms > cutoff_ms` 的最早两个。缺少首月、中间月或所选合约报价不得跳到远月。合约月、到期日期和 UTC 交易截止日不能互相矛盾。普通 Gregorian 日期校验不生成期货到期规则；节假日变化必须来自输入日历。
3. **日历证据与覆盖。** source known 不晚于 valid_from；`valid_from <= cutoff < valid_until`。最多 32 合约、32 报价，月份 2000—2199。日历完整性、时区转换及官方认证仍是供应方责任并保持 UNVERIFIED；程序只校验显式输入的一致性。
4. **价格口径。** 期货只能 settlement/settlement 或 midpoint/midpoint；midpoint 从正数且 `bid <= ask` 的两边精确计算。指数必须 INDEX，身份分别为 VIX9D/VIX。价格缺失不补零，非正数不能作分母或报价。
5. **同一时点。** 两价格 observed 毫秒和 trading_session_date 必须相同。无容忍窗口、插值或跨交易日混用。每条报价 `observed <= received <= known <= cutoff`；声明延迟必须已知，不能大于 received−observed。来源时区显式，仅接受 UTC、America/Chicago、America/New_York；交易日是来源声明的交易日，不能用 UTC 日期替代跨夜 session 身份。
6. **有效性。** 年龄用 cutoff−observed，不用收到旧数据的时间刷新；年龄等于 max_age 可接受，大于则 STALE。max_age 必须显式，范围 1..86400000ms。所有时间为精确可表示的整数毫秒，不超过 JavaScript Date 上界。`READY` 只表示输入 cutoff 的纯计算条件满足，**不表示现在数据新鲜**，页面固定提示这一点。
7. **精确计算。** `spread_points = first−second`，报告同时含 `ratio = first/second`、`ratio_minus_one = (first−second)/second`。期货主显示 VX1/VX2−1；指数主显示 VIX9D/VIX。严格正的期货价差才为 BACKWARDATION，负为 CONTANGO，零为 FLAT；指数使用独立 SHORT_HORIZON_* 分类。使用现有 Dec，不使用 f64；非有限小数以精确 numerator/denominator 输出，`decimal_exact:null` 不代表比率缺失。
8. **换月可见。** 单一规则 `MONTHLY_AT_SUPPLIED_LAST_TRADE_V1`：截止时点恰好等于 last_trade 时旧合约排除。可选 previous_pair 必须属于同一日历覆盖区间且匹配当时选择，报告标记 CHANGED/UNCHANGED_FROM_SUPPLIED_PAIR。未提供则 UNKNOWN_NO_PREVIOUS_PAIR。即便新报价过期仍保留已选合约和换月标记；不会自动刷新报价。这里不存历史、不计算情绪变化、不把换月当恐慌改善。

## 实现边界与保留标准

复杂度为简单的同步、无状态基线：一个 core 分析文件、一个薄 HTTP 输入文件、一个页面。没有新增 crate、依赖、缓存、并发机制、DSL 或泛化框架。

| 必要能力 | 真实问题 | 验收与保留标准 |
|---|---|---|
| 独立可空 futures / spot | 任一数据源缺失不应阻断另一观察 | 相互移除输入的测试仍输出另一结果；不进入意图/资金模块 |
| 显式月度日历与单一换月规则 | 周度混入、假定固定星期三、跳过缺失近月会改变含义 | 周度、缺月、跨年、截止边界、错误日期测试；没有完整日历则不给数值 |
| 可选上一合约对 | 当前两价格无法判断是否换月 | 不提供时 UNKNOWN；提供时校验身份与时点；无存储和隐式历史 |
| 精确分数 | 比值可为循环小数，现有 Dec 拒绝隐式舍入 | 1/21 原生与浏览器测试；不新增共享舍入机制 |
| 严格输入与证据元数据 | 防止静默接纳未知字段、陈旧/未来/异步数据 | 原生失败路径与 HTTP 防护回归；不会为消融降低校验 |
| 固定演示与审计 UI | 尚无授权行情时仍能复验实现 | 全程显式虚构/非实时，导出完整身份与输入，桌面/移动端检查 |

旧 View 必填 v1、期权诊断、原子报告发布、证据 hash、封存数据规则、预留与人工 paper 状态均不修改。核心架构检查把 `vix_observation` 注册在分析组，交易内核仍禁止依赖分析组。新请求不调用资金账本或 paper 记录。

## 测试与下一步

```sh
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo clippy --manifest-path rust/Cargo.toml --workspace --all-targets --all-features --locked -- -D warnings
cargo test --manifest-path rust/Cargo.toml --workspace --all-features --locked
cargo build --manifest-path rust/Cargo.toml --release --locked -p options-workbench --bin options-ui --example paper_browser_fixture
npm run check --prefix options-ui
npm test --prefix options-ui
npm run build --prefix options-ui
npm run test:options
npm run test:paper
```

`risk.spec.js` 通过真实 Rust 服务验证前后端数值一致、缺失/过期/异步、换月告警、精确分数、严格输入恢复、导出和 390px 移动端；原期权与人工 paper 浏览器测试继续运行。Rust 测试覆盖符号/月度选择、精确边界、报价和时点失败路径、单独输入、版本与 HTTP 保护。CI 继续跑 Rust 原生和旧 Harness；新功能验证不依赖旧 Python 测试。

未实现实时订阅、历史回测、阈值/自动规则、恐慌预测、交易执行、账户风控联动或性能目标证明。下一最小步骤是取得一个授权且可留存原始证据的快照来源，明确日历完整性、观测/发布语义及延迟后编写单一只读适配器，并复用这些失败测试；不应先扩展为行情平台。
