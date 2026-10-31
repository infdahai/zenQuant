# 人工 paper 设置与意图记录

默认入口已迁移至原生 Go，见 [Go 运维说明](GO_OPERATIONS.md)。下列 npm
build/start 命令现在构建并运行 Go 服务；本文件的业务边界和版本化协议继续有效。
Rust 实现保留为历史参考，文中的 crate/源码路径及 Rust 测试命令描述该参考实现。
当前默认构建需要 Go 1.25+、Node；运行不调用 Rust 或 Python。

本切片支持用户确认的两条线：Deribit 期权与 Binance USD-M BTCUSDT 永续；总模拟资本声明 **USD100000（两条线合计）**。仅人工输入，不接自动策略、实盘或真实账户。期权首个表单范围是线性 USDC、BUY 单腿，用户填写实际合约、到期、Call/Put 和数量；不自动推荐。反向期权、卖方、多腿与结算均未实现。

## 运行与使用

```sh
npm ci --ignore-scripts --prefix options-ui
npm run build:options
npm run start:options
# 运行机器本身访问 http://127.0.0.1:8092/paper
```

服务只监听 loopback；云工作区地址不是可直接从手机访问的公网链接。本轮没有部署或隧道。默认构建需要 Go 1.25+、Node；构建后的运行仅需要 Go 二进制和静态资源，不调用 Python/Rust。历史 Rust 构建仍使用其固定工具链。

1. 明确填写两个账户的 USD 分配和单笔**预算申请**上限，未配置的账户留空。合计不得超过100000，剩余为未分配预算。
2. 已分配账户必须显式选择 `paper-fx-assumption.rust.v1` 的实验1:1换算假设（USD/USDC 或 USD/USDT），否则拒绝配置。它不是市场汇率、净值或可提现抵押金。当前没有其他换算模型。
3. Binance 必须显式选择逐仓/全仓与杠杆上限；意图的模式须一致，杠杆不得超过该上限。1–125只是输入范围，不认证交易所允许的杠杆或保证金档位。没有默认许可、隐含跨账户抵押或自动追加保证金。
4. 预览完整内容，再勾选“仅记录”确认。预览不创建文件、不变更状态；新提交有效期为5分钟，参数、证据引用、当前配置/日志 hash 一起绑定。修改后重新预览。修改已用请求 ID 的参数会冲突，界面提供显式创建新请求 ID。
5. 人工意图确认后是 `RECORDED_UNASSESSED`。缺费用、规格、行情、资金费或保证金资料会显示阻断原因；即使填写了引用，也始终 `UNVERIFIED`，不授予执行权限。
6. “撤回”仅将档案标记为 `WITHDRAWN_RECORD_ONLY`，不叫撤单、不释放交易预留。原 ID 同内容重放返回已有结果；已撤回记录不会被复活。重新打开页面读取持久记录。

单笔预算申请是人工声明，不是由数量和限价计算的权利金、名义本金或保证金。当前不对多份意图累计占款，因为本轮完全不生成预留、订单或成交。不能把这个字段作为交易准入证据。

## 权威与兼容

仍是两个 crate、四个逻辑边界。`paper.rs` 在交易组，处理纯配置/记录状态转移；`paper_store.rs` 在适配组，复用已有目录锁、严格读取、fsync/rename/目录fsync。`curve_http.rs` 仅增加三个同源 POST 路由；`Paper.svelte` 不含金额或保证金计算公式。

- 新入口：`manual-paper-command.rust.v1`，动作 CONFIGURE / RECORD_INTENT / WITHDRAW_INTENT。
- 永续独立：`manual-binance-usdm-perpetual.rust.v1`，仅 BTCUSDT，数量 BTC，限价 USDT/BTC；没有虚构行权价、到期或 View。
- 新人工期权描述：`manual-deribit-option.rust.v1`。这是未核验的人工合约描述，不是旧规格的替代品；未认证合约存在、乘数、精确到期时间和结算支持。
- 旧 `simulated-trade-intent.rust.v1`、分析 v1 的 View 必填和未知字段拒绝保持不变。没有修改旧终值模型，也没有将经期货结算伪装成现金结算。
- 请求、预览、配置、换算假设、日志分别版本化；未知/重复字段、浮点金额、非法 ID 和冲突重试被拒绝。

**唯一顶层分配台账**是仓库固定路径 `.manual-paper/snapshot.json`。它声明用户如何划分100000USD，不是交易所保证金权威。它不读写 `.simulated-reservations`，不调用 `fund/reserve`，不把旧预留资金相加，也没有转账或自动迁移。现有旧现金权威继续保留，但不接到此工作流。下一阶段若接实际模拟成交/资金权威，必须显式迁移和整体切换，不能向每个产品另建100000余额或同时双写两个资金权威。

普通仓库与 linked worktree 复用 repository root；独立 clone 仍是独立模拟声明，不能用于真实账户全局协调。原始不存在状态在第一次有效配置确认后，通过私有目录准备完整快照并以不覆盖方式发布；发布前中断可重试，发布后重试会重放。已存在目录的快照丢失或损坏均 fail closed，不自动创建新预算。遇到持久性未确认，应保留原请求 ID重新读取/重试。Go 和 Rust 混用时两者都须升级到此协议；旧二进制无法保证首次发布后父目录 fsync 恢复，旧写入器并发首次初始化也不受支持。快照格式保持兼容。未承诺所有硬件掉电场景；本轮验证应用错误及进程 SIGKILL。

首次初始化的 `.paper-init-v1-*` 私有暂存目录带有目标日志名绑定标记，并由 Go/Rust 使用相同目录锁保护。后续确认只清理锁空闲且标记、目录身份和全部文件均符合协议的暂存目录；旧版无标记残留、异常内容、链接和仍在使用的目录会保留，须由操作者核查处理。标记建立前的中断可能留下空的无标记目录，但不会留下日志数据。

纸面日志的直接父目录和日志目录必须属于当前有效用户且不能被组或其他用户写入；祖先目录必须由当前用户或 root 拥有且不能被组或其他用户写入（root 拥有的 sticky 目录如 `/tmp` 除外）。快照及 pending 文件须属于当前有效用户且不可被组或其他用户写入。不满足时拒绝访问，不修改现有目录或文件权限；权限修复由操作者决定。此检查依据 Unix 所有者和模式位，不验证 ACL、挂载或同 UID 恶意进程；同 UID 进程本来就能够改写日志。

日志仅保存完整确认操作，视图从日志重建。重读会验证每个历史操作、hash、时点、配置守恒、ID及状态转移，不只比较最外层校验和。最多256次确认操作、16MiB文件；这是显式工程容量而非交易量目标。达到上限拒绝继续写入，不偷偷截断审计历史。没有缓存或后台调度；保留完整有界回放的标准是正确性与当前交互可用，未来有实测瓶颈才考虑新存储。

## 证据引用与时间

引用格式对应 `specification / quote / fees / mark / funding / margin`，值为null或以下结构：

```json
{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source":"engineering-fixture-only","kind":"SYNTHETIC_ENGINEERING_ONLY","observed_ms":1800000000000,"available_ms":1800000000001,"valid_until_ms":1800000600000}
```

示例仅说明字段，不是真实行情。另一个允许的 kind 是 `USER_SUPPLIED_UNVERIFIED`。这是证据**引用**，不是行情数据导入或认证。hash不得为空，已知时点不得晚于预览/确认，过期引用明确标为STALE；未来/矛盾时点拒绝。毫秒时间范围不超过JS可精确表示的Date上界。价格、数量、预算和杠杆都是精确小数字符串。没有任意路径读取、网络抓取或封存数据读取入口。上游报价/档位原始字节、来源认证、完整规格过滤和费用计算仍待下一个独立切片。

HTTP继续限制Host、同源Origin、自定义头、正文长度与超时，不支持分块或任意文件API。这是同机开发服务，不宣称身份认证、多用户隔离或公网安全部署。现有两个固定连接线程复用；磁盘写入有独占锁，没有额外任务队列或线程。

## Binance 官方规格调查（2026-10-12）

| 范围 | 核实结果与实现约束 |
|---|---|
| 规格 | `GET /fapi/v1/exchangeInfo` 提供产品类型、状态、币种、filters。precision不能替代tick/step；`maintMarginPercent`、`requiredMarginPercent`明确应忽略，不能作为保证金模型；该响应serverTime也应忽略，取专门time接口。[官方市场数据](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) |
| 数量 | USD-M BTCUSDT数量对应BTC基础资产，价格USDT/BTC；不能套用COIN-M面值张数。[官方产品说明](https://www.binance.com/en/support/faq/detail/85eac2bba0b342819122dc9bd4745e9b) |
| 过滤器 | PRICE_FILTER、LOT_SIZE、MARKET_LOT_SIZE分别约束价格/数量；PERCENT_PRICE基于mark；MIN_NOTIONAL字段是`notional`，MARKET用mark作名义金额参考。需要将适用过滤器全部验证后才能执行，本轮未实现。[官方过滤规则](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/common-definition) |
| Mark/index | premiumIndex的markPrice/indexPrice/time是估值证据，不是成交价。latest funding rate与nextFundingTime不是账户已扣资金费。[官方市场数据](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) |
| 资金费 | fundingRate历史有fundingTime与关联markPrice；当前还区分Regular/Special。fundingInfo仅返回被调整过cap/floor/interval的产品，缺席不代表零，也不能固定8小时。本轮不生成资金费账目。[官方市场数据](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) |
| 档位与费用 | leverageBracket是USER_DATA，需要API Key、timestamp与签名；含notionalFloor/Cap、initialLeverage（档位最大初始杠杆）、maintMarginRatio、cum及可能的用户notionalCoef。symbolConfig与commissionRate同属鉴权来源。本轮没有请求这些接口或使用密钥。[官方账户接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account) |
| 公开档位页 | 公开页面展示初始/维持保证金公式，但本次文本读取返回“No Data”，没有取得可用的BTCUSDT版本化数值快照；文档示例数值不可作为用户当前档位。[公开档位页](https://www.binance.com/en/futures/trading-parameters/perpetual/leverage-margin) |
| 强平证据 | forceOrder每个symbol每1000ms只推最新一条，不是完整强平账本，更不是本账户强平事实。公开流不能认证账户逐仓/全仓配置。[官方市场流](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market) |
| 私有账户事件 | ACCOUNT_UPDATE的FUNDING_FEE在全仓只带有关余额，不带position；逐仓带有关余额及该仓位。缺字段不能置零。MARGIN_CALL可能与强平同时到达，不能充当可靠提前通知。[官方用户流](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams) |

这些是文档核实，不代表运行时连通。此前实测限制是Binance **spot**和Deribit公开API的CONNECT403，不是已完成USD-M连通测试。本轮遵守限制，没有改域名、代理或测试网络绕过；只读官方文档。没有实时行情、账户保证金、手续费或可成交证据。

未来若接流，mark事件`E`是事件时点、`T`是下次资金费时点；不能与私有流中表示撮合交易时点的`T`混用。保留本地可获得时点，资金费按结算事件幂等记账，不因下载历史数据就声称当时已知。新文档部分端点提及UM/CM迁移，产品身份必须显式核实，不能只凭主机名认定USD-M。

## 测试

```sh
cd rust
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
cargo test --workspace --all-features --locked
cargo build --release --locked -p options-workbench --bin options-ui --example paper_browser_fixture
cd ..
npm --prefix options-ui run check
npm --prefix options-ui test
npm run build:options
npm run test:options
npm run test:paper
```

`paper_browser_fixture`只在独立临时目录服务8093端口，页面显式显示ENGINEERING_TEST_ONLY；不会污染用户的仓库分配台账。fixtures中的60000/30000、2倍杠杆和价格仅用于工程测试，不是用户已选配置。真实入口默认未配置。

覆盖精确超额边界、未确认换算、缺风险设置、单笔上限、参数/证据篡改、旧预览、重复确认、ID冲突、原ID撤回后重放、缺证据执行阻断、旧期权契约兼容、严格JSON、实际TCP同源保护、并发确认、六个写入失败阶段及四个SIGKILL阶段；浏览器覆盖实际Rust服务的桌面与390px移动界面。性能没有验收目标或达标声明。

下一最小步骤：确定一份可审计的规格/报价/费用快照与明确模拟保证金模型，再增加人工意图到单一预留/订单/部分成交/精确现金事件的最小账本。每一步须验证资金占用不是支出、手续费/资金费只入账一次、撤回与撤单分离、重启幂等；在模型缺失时继续禁止开新风险。本轮不提前实现强平、结算、自动策略或无限循环。
