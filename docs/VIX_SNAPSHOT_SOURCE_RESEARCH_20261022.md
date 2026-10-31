# VX 换月与只读快照接入核查（2026-10-22）

## 结论与实施状态

核查基线为 PR #23 的 `25c0d12c88036ede232c2b54a5bc62cdfe43a8c1`。该提交的 Rust options diagnostics、Harness Gates 两个 GitHub workflow 均为 success，PR 为 Draft。

10 月 21 日的到期时间正确，现有选择算法不需要修正。`demo.json` 继续保持 10 月 16 日的虚构报价和 DEMONSTRATION 身份。本次没有把网页价格改写为真实夹具，没有购买订阅、使用账户、抓取延迟行情端点或连接交易系统。

完成的是**本地证据快照导入器**，不是已接通的 Cboe / IBKR / Barchart 行情客户端。它把供应方整理的现有 Request、原始证据文件和许可声明接入原 Rust evaluator。真实源解析及线上验收仍缺少：覆盖具体用途的许可、可留存的样本、可靠时间字段和供应方符号/合约定义。不得把本次合成测试描述成真实行情验收。

## 1. 换月边界

[CFE 更正公告 2026011501](https://cdn.cboe.com/resources/product_update/2026/Correction-CFE-Futures-Contract-Expirations-for-Current-Products-to-be-Added-in-January-2026.pdf) 第 1 页明确列出 VX OCT 26 最后交易时间为 OCT 21 26 08:00 a.m. CT，最终结算日期也是 10 月 21 日。应使用 VX，而非提前一天结束的 VXT/TAS 合约。

| 判断时点 | 月度 VX1 | 月度 VX2 |
|---|---|---|
| 2026-10-21 07:59:59.999 America/Chicago | VX/V6 | VX/X6 |
| 2026-10-21 08:00:00.000 America/Chicago 起 | VX/X6 | VX/Z6 |

10 月仍为 CDT：边界为 `2026-10-21T13:00:00Z`、北京时间 21:00，Unix 毫秒 `1792587600000`。11 月已经切换 CST，11 月 18 日 08:00 CT 为 14:00 UTC，不能全年固定 UTC-5。

11 月和 12 月合约到期日分别为 2026-11-18、2026-12-16，见[官方日结算表](https://www.cboe.com/markets/us/futures/market-statistics/settlement/futures/daily/)。按显式完整月度日历、`last_trade_ms > cutoff_ms` 选择前两个合约；不能按页面顺序或最小到期日期直接取两行，因为 `VX43/V6` 等周度合约也在表中。不能用连续合约、活跃合约、VXM、VXT 或回溯调整后的序列代替。

现有边界测试已覆盖恰好到期；新增导入回归覆盖前 1ms、恰好到期、后 1ms，并断言换月后旧报价仍是 STALE，不会因选出新合约而刷新时间。

## 2. 官方结算数据能证明什么

本次读取日结算页时，页面的 **Settlement Prices for 2026-10-21** 列出：

| 合约 | 到期日 | 页面报告的结算值 |
|---|---|---:|
| VX/V6 | 2026-10-21 | 16.82 |
| VX/X6 | 2026-11-18 | 18.2382 |
| VX/Z6 | 2026-12-16 | 18.5457 |

这些是本次来源核查的页面读数，不是授权快照包，也没有导入运行时。10 月合约仍列在结算表中，不表示它在结算后仍属于可交易近月。

必须区分三个概念：未到期期货的日结算、到期合约的最终结算/SOQ、VIX/VIX9D 指数值。SOQ/VRO 不能当 VIX spot；旧合约最终结算不能与新合约日结算拼成同一时点的可交易 VX1/VX2。[最终结算入口](https://www.cboe.com/markets/us/futures/market-statistics/settlement/futures/final) 与日结算入口是两个页面。

日结算页提供交易日期、合约与结算价，并有 CSV 下载入口。本次未成功取得可检查的 CSV 字节，因而没有假定其列名或实现未经样本验证的 CSV parser。页面没有为每条价格提供可直接映射到 observed_ms 的完整观测/发布时间；不能自行填当天午夜、收盘时间或下载时间。

[CFE Daily Market Statistics](https://www.cboe.com/markets/us/futures/market-statistics/daily/futures/) 提示统计约于下一交易日 10:00 CT 可用。这不是本次已经证实的结算 CSV 发布 SLA，更不代表文件日期时刻已经可知。应记录实际收到与首次可用时间，防止回放前视。

## 3. 合法接入候选及缺口

| 来源 | 可用内容 / 方式 | 授权与技术判断 |
|---|---|---|
| Cboe 日/最终结算网站 | 手工查证、页面提供的下载入口 | 网站条款允许有限个人非商业使用，不能据此推定允许应用采集、计算、存储或分发；还需核实具体用途。日期级数据暂不符合现接口精确时点条件 |
| Cboe 延迟报价网页 | 人工查看 VX、指数 | 明确禁止软件自动提取；不编写网页或隐藏 JSON 抓取器 |
| Cboe CFE 官方数据 feed / 获授权供应商 | 实时或合同约定的延迟数据 | CFE 期货权限与指数权限分开确认；需合约定义、完整日历、源时间和快照语义 |
| Cboe DataShop / CGIF EOD | 指数 OHLC，次交易日文件 | [Main Channel EOD](https://datashop.cboe.com/main-channel-end-of-day-summary) 标明次交易日 09:30 ET 后交付、收盘时间依指数而异、禁止外部分发。须确认 VIX9D 的当前 channel 与具体产品覆盖，不能仅因有 VIX 就认为两者均包含 |
| Cboe DataShop CFE Futures Trades | 日终逐笔成交文件，可选 RTH/GTH，含成交时市场信息 | [当前产品](https://datashop.cboe.com/cfe-futures-trades)并非全量持续报价快照。不同合约最后成交时刻不能强行同步；有 TAS 标识，要排除或按文档处理。页面禁止外部分发。老 VIX Trades & Quotes 产品仅到 2018 年，不能购买它解决 2026 年问题 |
| IBKR TWS / Gateway API | 有权限的期货和指数报价；可请求 snapshot | 需要可用账户、API 行情权限、CFE 与指数订阅以及具体产品覆盖。终端能显示不保证 API 能取。只读配置、仅行情方法；普通 snapshot 可能返回过去 11 秒内的最近可用数据，不保证两合约同时，也不保证所有字段到齐 |
| Barchart OnDemand API | 文档化的 getQuote / getHistory，可含实时、延迟、EOD | 是可询价的供应商候选，尚未核实本账号权限、VX 和 VIX9D 全覆盖、留存与再分发许可。serverTimestamp 是消息生成时间，tradeTimestamp 是最后成交时间，两者均不能直接证明 bid/ask 同步；不能用 nearby/active 别名替代已选月度合约 |

来源：

- [Cboe 网站条款 §2](https://www.cboe.com/terms/)：公开可访问不等于开放数据许可。
- [延迟 futures 页面告示](https://www.cboe.com/delayed_quotes/futures/future_quotes/)：明确禁止自动提取。
- [Cboe CGIF](https://www.cboe.com/data/global-indices-feed)、[官方指数产品表](https://www.cboe.com/us/indices/indicesproducts/)：VIX 与 VIX9D 为不同指数，许可与 channel 以订购时定义表为准。
- [IBKR Streaming Data](https://www.interactivebrokers.com/docs/tws-api/doc/md/streaming-data)、[Delayed Data](https://www.interactivebrokers.com/docs/tws-api/doc/md/delayed-data)：实际 marketDataType 回调区分 live/frozen/delayed/delayed-frozen。不能把请求的类型当作实际返回类型；也不能用最后成交时间标注 bid/ask。监管美股 NBBO 的单次快照收费规则不能直接套给 VX。
- [Barchart getQuote](https://www.barchart.com/ondemand/api/getQuote)、[FAQ](https://www.barchart.com/ondemand/faq)：R/I/D 模式、服务器与交易时间、部分批量端点缓存有其各自定义；具体延迟须按所选产品核实，不能统一填零或 15 分钟。

不把任何供应商目录视为已获许可，也不把无法访问的官方数据样本视为已完成验证。

## 4. 时间、日历与原始证据约定

保留现 Request v1：

- `observed_ms`：来源明确提供的价格有效/观测时间。只有 session date 的结算文件不满足这一条件；导入 manifest 若为 `SESSION_DATE_ONLY` 直接拒绝。不得通过统一重打时间戳伪造同步。
- `received_ms`：本地实际完整收到响应/文件的时间；`source.known_ms`：该投影及其证据可用时间；`cutoff_ms` 不早于上述时间。供应商发布时间若另有字段，应原样留存在证据中。不能为历史回放倒填 known。
- 两条期货采用相同价格口径、相同 observed 和交易 session；VIX9D/VIX 单独满足同样条件。server time、HTTP Date、last trade time、bid time、ask time 不可互换。
- `declared_delay_ms`：合同/来源说明的明确延迟；未知为 null，core 输出 MISSING。传输延迟和已发生的数据年龄分别保留。冻结或上一日数据不得冒充实时。
- 日历有真实来源与 known、有效覆盖区间、起始月和连续月度记录。新收到的日历不能声称更早已知；若从 10 月 22 日开始生效，起始月应为 202611。只有已有覆盖历史时才能传 previous_pair；否则保持 UNKNOWN。
- `max_age_ms` 仍最多 24h。隔夜、周末或节假日结算可能过期；不放宽到无限期，不插值，也不混新旧日。READY 只指输入 cutoff 的条件满足。

项目审计包应留存原始响应/文件精确字节、SHA-256、来源 URL/产品名（去密钥）、脱敏请求参数及合约定义、响应头/供应商发布元数据、抓取/收到/known 时间、时区与偏移、交易 session、实际延迟/冻结模式、解析规则版本、许可/用途记录、规范 Request 与导出报告的代码及 lock hash。原始文件不能只保留抄写价格或截图，也不能仅保留哈希然后删掉文件。修订后的结算数据应追加版本及各自收到时间，不能覆盖旧版本。

[Cboe North American Data Policies，2026-09-01 生效](https://cdn.cboe.com/resources/membership/Market_Data_Policies.pdf) §2 对协议下 Data Recipient 的使用、展示、分发等合规记录规定至少三年或监管要求更长者；这不等于所有用户必须无条件保存所有 tick 三年。具体原始行情保留范围和期限按适用合同落实。延迟、历史和派生值也不自动免除许可；对外展示价差/比率须另行确认。

上述是接入和审计约定。导入器只核验本地留存字节与声明是否齐备，不判断合同法律效力、供应商真实性、声明是否覆盖每个符号，也不替用户建立长期档案。

## 5. 最小 Rust 适配与运行

新增 `options-workbench/src/vix_snapshot.rs`、`src/bin/vix-snapshot.rs`，不新增 crate。既有 core、HTTP 和 Svelte 协议不变：

1. 供应方/人工按已确认字段制作严格 Request v1；真实数据必须用 `SUPPLIED_SNAPSHOT_UNVERIFIED`，合成数据用 `DEMONSTRATION`。
2. 每个 Source.sha256 对应本地原始文件字节。日历、所有报价（含未选远月）均检查；再提供授权记录文件。
3. manifest 只接受显式用途声明和扁平文件名，核验所有字节哈希。限制 32 文件、单个 16 MiB、合计 64 MiB，拒绝重复文件名、路径穿越、符号链接和缺失文件。
4. 复用 `vix_api::evaluate` 进行计算及原有失败降级，导出 Request、传输/代码/lock 哈希和额外 admission 记录。不会修改 observed、cutoff 或 mode。

```sh
cargo build --manifest-path rust/Cargo.toml --locked -p options-workbench --bin vix-snapshot
./rust/target/debug/vix-snapshot /path/request.json /path/manifest.json /path/evidence > /path/admission.json
cargo test --manifest-path rust/Cargo.toml --locked -p options-workbench --test vix_snapshot
```

`manifest.json` 结构如下；尖括号必须替换成实际值，不能把此模板作为许可证明：

```json
{
  "schema_version": "vix-snapshot-manifest.rust.v1",
  "time_basis": "SOURCE_EFFECTIVE_TIMESTAMP",
  "rights": {
    "status": "DECLARED_AUTHORIZED_LOCAL_ONLY",
    "reference": "<许可、适用产品及本地计算/展示/原始留存范围的记录>",
    "evidence_sha256": "<授权记录文件的64位小写SHA-256>",
    "local_compute": true,
    "local_display": true,
    "retain_raw": true
  },
  "files": [
    {"filename": "calendar.json", "sha256": "<原始日历字节哈希>"},
    {"filename": "quotes.json", "sha256": "<原始行情字节哈希>"},
    {"filename": "permission.txt", "sha256": "<授权记录字节哈希>"}
  ]
}
```

合成包则声明 `SYNTHETIC_TEST_ONLY`，并用合成许可记录，不能将原 demo 的说明字符串哈希伪装成 Cboe 原始文件哈希。新增测试在临时目录内创建合成原始文件并重算 hash，不改原 demo。

成功输出 `raw_integrity=LOCAL_PINNED_BYTES_MATCH`，仍同时输出 `SUPPLIED_PROJECTION_UNVERIFIED`、`SUPPLIER_DECLARATION_NOT_INDEPENDENTLY_VERIFIED`、`NONE_GRANTED_BY_ADAPTER`。它不解析供应商原文来独立证明价格投影；若需要该证明，必须在取得真实样本后实现单一源 parser 并做逐字段测试。

CLI 只读本地文件并输出 JSON，不写交易状态，也不添加网络端点。`observation.request_json` 可作为现有 `/api/vix/evaluate` 输入，但额外 admission 证据须单独留存。原 HTTP 接口仍可接收未验证供应快照；它不宣称已通过本地证据导入，也不获得再分发权限。UI 继续用原有演示路径，不自动加载真实数据。

## 6. 验收与下一步

合成测试覆盖：字节完整性、缺失/篡改文件、授权声明与模式隔离、日期级结算拒绝、路径限制、严格 JSON、换月三点边界、保留 STALE/MISSING/INVALID，以及无外部程序的 CLI 输出。

线上验收的完成条件是：一个合法可使用且可留存的真实样本包；确认所有相关合约与指数权限；用真实样本确认符号、时间、延迟、session 和同一时点语义；再补该源 parser 与真实只读复验。当前均未声称完成，不扩大为自动交易或通用行情平台。
