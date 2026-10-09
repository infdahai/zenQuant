# Options Portfolio Kernel P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可离线复核的期权组合核算、资金约束、盘口成本与诊断回放，不授予实盘、策略晋级或自动开仓权限。

**Architecture:** 新建纯计算 `zenquant/options/`，由隔离的 research adapter 读取封存资料并输出报告。先实现全额付款的线性欧式长期权；三平台币本位/线性产品可做带来源的报价诊断，执行核算范围外产品明确拒绝。所有报告区分数学核算、模型可行性、真实账户资格与成交证据。

**Tech Stack:** Python >=3.11；标准库 dataclasses、Decimal、hashlib、json、argparse；已有 pytest/ruff。无新运行依赖、网络请求、账户凭证或 GPU。

**Spec:** [已审阅架构规格](../specs/2026-10-09-options-platform-design.md)，基线 commit `6e37ddabd7d35b9addcfbb288f05d4fe1bb653c2`。用户在展示该书面规格后回复“开始处理”，随后再次回复“可以去处理”，本计划承接规格第 8 节 P1。

**Status:** Task 1–8 产品实现与局部验收已交付；Task 1–7 的逐项独立审查和修复已完成。Task 8 完整 gate 已执行一次，结果见本页交付记录；全分支独立审查由 controller 安排，仍待完成。局部通过不表示全仓、实盘或 promotion 通过。执行沿用用户授权的顺序 subagent 方法；未声称使用不可用的 Terra。

## Global Constraints

- “第一实现只允许明确标准保证金模式下、全额权利金买入的线性欧式期权；不借币、不卖腿、不做 perp。”
- “系统产生的瞬时结算头寸不授予用户 futures 交易权限。”
- “所有组合的 `pending_cash_reserve + active_at_risk_capital` 合计最多 50 U”；1,000 U 工程示例每 intent 权利金与交易费最多 10 U，滚动 30 天 tail premium/fee 最多 20 U；3,000 U 示例按相同比例独立运行。它们是模拟配置，不是已激活账户 policy。
- “VALID 只修饰已检查的输入或模型可行性，不能合并成‘真实账户可交易’。”
- “没有可达成交证据不产出可排名 PnL”；`actual_pnl / paper_pnl / sharpe / capacity` 全部 null。
- “`options` 核心不依赖 research、UI、exchange client 或历史 backtest”。不改变 legacy、TargetPosition、旧风险、OptionsState、插件组合规则、黄金结果或冻结准入状态。
- 当前 production 仍为 confirmed 4h；本计划不新增方向信号或新时钟。手工情景是诊断输入，不伪装成自动方向开仓。
- 不改封存研究目录，不导入一次性 `probe.py / analyze.py` 当产品库，不运行采集器或访问凭证。
- 金额/数量用有限 Decimal，固定 Context(prec=50, rounding=ROUND_HALF_EVEN)；序列/时间用严格 int，拒绝 bool。所有状态集合用 tuple/frozen dataclass。
- 首批手算资产 BTC/ETH；资产名不硬编码到核心支持白名单。产品与账户模式显式准入。
- 各任务只提交所属文件；测试、实现、格式与文档按概念拆分。禁止 `git add .`、清理已有脏文件、合入无关改动。

## Review Focus

1. 收到重复/迟到/内容冲突回执：不双扣款，迟到实际成交保留，冲突冻结并携带未核算证据；Task 4/5 测试。
2. 撤单确认后仍收到成交：资金与库存照实入账，不能因预留已释放而忽略或重新下单；Task 4/6 测试。
3. 币本位报价、新旧指数和不同合约乘数混用：保留原单位及代理假设，过时换算不能制造低成本；Task 1/2/7 测试。
4. 合约到期产生系统 future：过渡和现金结算可追溯，费用一次，期间不可消费待结算价值；Task 3/5 测试。
5. 多个 intent 共享钱包与小预算：成交预留转为 active risk，不能反复使用同一 50 U 上限；Task 4/6 测试。

---

## 工作区与文件地图

执行前再次记录 `git status --short --branch`、HEAD 与 index；当前 checkout 已有大量未提交工作。P1 采用新增模块与独立新增测试，保留现有资料。不为了建立干净分支提交、搬运或丢弃用户改动。若另建 worktree，必须解决封存资料及现有未提交证据工具的只读访问，不能宣称新 worktree 自带它们。

| 新建路径 | 单一责任 |
| --- | --- |
| `zenquant/options/__init__.py`、`contracts.py` | 不可变规格、单位、来源与核心 intent |
| `zenquant/options/costs.py` | 本币 VWAP、费用、输入时效与报告币换算 |
| `zenquant/options/payoffs.py` | 欧式线性到期现金流及系统结算过渡 |
| `zenquant/options/ledger.py` | 实际现金、库存、订单预留与事件归约 |
| `zenquant/options/risk.py` | 小账户资金与长期权风险预算 |
| `zenquant/research/options_platform/__init__.py`、`panel.py` | 封存资料校验及三平台元数据/盘口转换 |
| `zenquant/research/options_platform/replay.py`、`reporting.py` | 手算情景回放、独立身份、诊断/偏差报告 |
| `scripts/analyze_options_portfolio.py` | 离线 CLI、输出目录与退出码 |
| `tests/options/__init__.py`、`factories.py`、九个 `test_*.py` | 真实行为与独立已知答案，不复用实现算期望值 |

不修改 `pyproject.toml`，不新增常驻服务。新增 AST 边界测试放在 `tests/options/test_boundaries.py`，随 Gate E 执行；现有 Gate B 不会自动扫描新域，不能把它已有通过当新域隔离证明。

## 统一接口约定

函数全为 keyword-only 或显式不可变参数。测试构造器都使用 keyword 参数，避免不同任务依赖字段顺序。

- `WalletKey(venue: str, account: str, account_mode: str, currency: str)`：只接受非空身份；P1 可执行模型仅 `account_mode="standard"`。
- `EventKey(event_id: str, source: str, event_ns: int, received_ns: int, available_ns: int, journal_sequence: int)`：economic time 与 knowledge time 分开；`available_ns >= received_ns`。
- `Observation(value: Decimal, source_ns: int, received_ns: int, available_ns: int, source_id: str, provenance: str)`：`provenance` 仅 `fixture / observed / proxy`。观察值和假设不可混为 verified。
- `BookLevel(price: Decimal, quantity_native: Decimal)`；`Book(spec: InstrumentSpec, bids: tuple[BookLevel, ...], asks: tuple[BookLevel, ...], source_ns: int, received_ns: int, available_ns: int, raw_hash: str)`。
- `LegIntent(spec: InstrumentSpec, wallet: WalletKey, quantity_native: Decimal)`：P1 quantity 为正，未来方向不靠负数暗中启用。
- `PayoffLeg(spec: InstrumentSpec, signed_quantity_native: Decimal)`：只作静态现金流分析，可以为负；不能作为订单、风险批准或执行回放输入。
- `CombinationIntent(intent_id: str, legs: tuple[LegIntent, ...], template: str, sleeve: str, decision_cutoff_ns: int, valid_until_ns: int, policy_hash: str)`：模板 P1 仅 `long_call / long_put / long_straddle / long_strangle`，sleeve仅 `directional / volatility / tail`。数学结构可以描述其它腿，但 risk/replay 不执行。
- `StatusResult(status: str, reason: str)`：status 为 `VALID / REJECTED / NOT_EVALUABLE / UNSUPPORTED / INVALID`，不授予权限。
- 所有类型与 strict validator 属于 Task 1 `contracts.py`；每个对象创建时拒绝非法值。日期窗口统一 UTC、整数 ns，禁止 float timestamp。Decimal 转换只接受 Decimal/十进制字符串，不使用二进制 float 隐式构造。

### Task 1: 合约身份、单位与不可变核心契约

**Files:** Create `zenquant/options/__init__.py`、`zenquant/options/contracts.py`；Test `tests/options/__init__.py`、`factories.py`、`test_contracts.py`。

**Interfaces:** Consumes 标准库；Produces 上节类型、`InstrumentSpec`、`base_quantity(spec, quantity_native) -> Decimal`、`premium_cash(spec, price, quantity_native) -> Decimal`、`validate_quantity(spec, quantity_native) -> StatusResult`、`canonical_payload(value) -> dict | list | str | int | bool | None`、`fingerprint(value) -> str`。

InstrumentSpec 字段必须完整：

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class InstrumentSpec:
    venue: str
    instrument_id: str
    product: str                  # linear_option / inverse_option
    underlying: str
    quote_currency: str
    strike_currency: str
    settlement_currency: str
    margin_currency: str
    expiry_ns: int
    strike: Decimal
    option_type: str              # C / P
    exercise_style: str           # european / unsupported style
    quantity_unit: str            # base / contracts
    multiplier: Decimal          # underlying units per native quantity
    price_unit: str               # quote_per_base / quote_per_contract
    min_quantity_native: Decimal
    quantity_step_native: Decimal
    max_quantity_native: Decimal | None
    tick_bands: tuple[tuple[Decimal, Decimal], ...]  # lower bound, tick
    settlement_index: str
    settlement_window_ns: int
    settlement_rule: str          # linear_cash / linear_via_future / inverse_via_future
    rule_version: str
    metadata_hash: str
    valid_from_ns: int
    valid_until_ns: int | None
    provenance: str
```

- [x] **Step 1: 写独立单位测试与 fixture 构造器。** `factories.py` 的 `linear_spec(*, underlying="BTC", option_type="C", strike="100", expiry_ns=1000, settlement_rule="linear_cash", **changes) -> InstrumentSpec` 使用 FIXTURE/standard/USDT、native base、multiplier 1、min/step .01、tick band `(0,.01)`、rule `fixture.v1`、有效期 `[0,None)`；changes 通过 dataclasses.replace。`wallet(currency="USDT", venue="FIXTURE") -> WalletKey`、`key(n, *, event_ns=None, event_id=None) -> EventKey` 的 received/available/journal 都取 n，原事件时间默认 n。

```python
def test_contract_quantity_is_not_underlying_quantity():
    spec = linear_spec(quantity_unit="contracts", multiplier=D("1000"))
    assert base_quantity(spec, D("2")) == D("2000")
    assert premium_cash(spec, D(".2"), D("2")) == D("400")

def test_family_and_rule_change_identity():
    spec = linear_spec()
    assert fingerprint(spec) != fingerprint(replace(spec, product="inverse_option"))
    assert fingerprint(spec) != fingerprint(replace(spec, rule_version="fixture.v2"))
```

- [x] **Step 2: 运行 RED。** `uv run pytest tests/options/test_contracts.py -q`，预期新模块导入缺失；不能依赖无关断言失败获得 RED。
- [x] **Step 3: 实现上述 frozen 类型、严格字段检查与单位函数。** 正数、finite、时间、枚举、min/step/max、tick band 顺序全部检查；base 数量模式必须 multiplier=1。原生数量先校验再换算，不能向下取整偷偷变更 intent。

```python
def base_quantity(spec, quantity_native):
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        return quantity_native * spec.multiplier

def premium_cash(spec, price, quantity_native):
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        units = base_quantity(spec, quantity_native) if spec.price_unit == "quote_per_base" else quantity_native
        return price * units
```

`canonical_payload` 递归展开 dataclass 字段、tuple 和具有 str key 的 mapping；Decimal 用固定十进制字符串，数值零统一为 `"0"`，拒绝 float/nonfinite/未知对象。`fingerprint` 为 sorted-key compact JSON UTF-8 的 SHA256。不 normalize 到调用者 Decimal context。
- [x] **Step 4: GREEN 与边界测试。** NaN/Infinity、bool 时间、负数量、qty step、超 max、不同 strike currency、tuple 可变性、重复钱包、tick 阈值都验证；改变全局 Decimal precision 后算术结果不变。命令同 Step 2。
- [x] **Step 5: 精确提交。** 新测试单独 `test(options): cover instrument identities and units`；实现 `feat(options): define immutable instrument and intent contracts`；只 stage 本任务路径，检查 staged paths。

### Task 2: 盘口成本、费用与因果时效

**Files:** Create `zenquant/options/costs.py`；Test `tests/options/test_costs.py`。

**Interfaces:** Consumes Task 1 Book/Observation/InstrumentSpec。Produces `FeePolicy(rate: Decimal, premium_cap: Decimal, fee_currency: str, provenance: str)`、`TimingPolicy(max_age_ns: int, minimum_source_age_ns: int)`、`CostEvidence(status: StatusResult, instrument_hash: str, book_hash: str, side: str, quote_currency: str, report_currency: str, asof_ns: int, quantity_native: Decimal, premium_native: Decimal | None, fee_native: Decimal | None, total_report_currency: Decimal | None, assumptions: tuple[str, ...], input_hash: str)`；`vwap(levels, quantity_native) -> Decimal | None`、`quote_cost(*, book, side, quantity_native, index_in_quote, quote_to_report, fee_policy, timing, asof_ns, report_currency: str) -> CostEvidence`。side 为 BUY/SELL，SELL 仅退出/价格诊断，不授予新空头权限。

- [x] **Step 1: 写测试。** Book native ask `.01@1000` 与 `.01@1100`，买 `.02` 的 premium=21；index 80,000、rate .0003、cap .07 得 fee=.48，总成本21.48，不能用首档20。fixture `book_for(spec, *, asks, bids, time_ns=10) -> Book` 将 `(price_string, quantity_string)` 转成 BookLevel；`observation(value, *, time_ns=10, provenance="fixture") -> Observation` 定义于 factories。

```python
def test_vwap_and_incomplete_depth():
    levels = (BookLevel(price=D("1000"), quantity_native=D(".01")),
              BookLevel(price=D("1100"), quantity_native=D(".01")))
    assert vwap(levels, D(".02")) == D("1050")
    assert vwap(levels, D(".03")) is None

def test_fee_cap_is_not_notional_fee_added_twice():
    premium, qty, index = D("1"), D(".01"), D("80000")
    assert min(D(".0003") * index * qty, D(".07") * premium) == D(".07")
```

- [x] **Step 2: RED。** `uv run pytest tests/options/test_costs.py -q`。
- [x] **Step 3: 实现 VWAP 和输入门槛。** 逐档数量消耗，没有足够深度返回 None；禁止 mid/mark 自动替代。按原生价格单位计算 premium。费用为 `min(rate * index_in_quote.value * base_quantity, premium_cap * premium_native)`；fee currency 未获支持时 NOT_EVALUABLE，不假定等于 quote。

```python
def vwap(levels, quantity_native):
    if not isinstance(quantity_native, D) or not quantity_native.is_finite() or quantity_native <= 0:
        raise ValueError("POSITIVE_FINITE_QUANTITY_REQUIRED")
    left, paid = quantity_native, D("0")
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        for level in levels:
            taken = min(left, level.quantity_native)
            paid += taken * level.price
            left -= taken
            if left == 0:
                return paid / quantity_native
    return None
```

所有 required input `available_ns <= asof_ns`；各源 `asof_ns-source_ns` 在 timing 区间。decision asof 取全部依赖 available 的最大值，而非单腿接收时刻。BUY显示支出为premium+fee，SELL显示净收入为premium−fee，不把后者叫新空头可执行。缺 index/FX/费用即 NOT_EVALUABLE；未知与零不同。quote 与 report 同币可用显式 exact-identity Observation；不同币必须提供 rate，proxy 进入 assumptions。native tick 使用对应档位；单平台 crossed/非正量/乱序价格 INVALID。源时钟小范围领先仅作为冻结容忍与质量标记，不能接纳未收到数据。
- [x] **Step 4: GREEN。** 加入 stale index、stale FX、未来 available、非法 lot、空/单边 book、caller Decimal context、premium cap、换算代理标签。测试缺深度后不能输出0费用/预算覆盖。
- [x] **Step 5: 提交 tests 与 costs 各自单用途 commit。** `test(options): cover displayed cost and stale dependencies` / `feat(options): calculate provenance-bound quote costs`。

### Task 3: 线性到期 payoff 与系统 future 过渡

**Files:** Create `zenquant/options/payoffs.py`；Test `tests/options/test_payoffs.py`。

**Interfaces:** Consumes InstrumentSpec/Observation/PayoffLeg。Produces `SettlementAction(kind: str, option_id: str, quantity_native: Decimal, future_quantity_base: Decimal, amount: Decimal, currency: str, link_id: str)`、`SettlementPlan(status: StatusResult, actions: tuple[SettlementAction, ...], delivery: Observation | None = None)`；`intrinsic_value(*, spec, delivery_price) -> Decimal`；`settlement_plan(*, spec, quantity_native, delivery: Observation | None, expiry_id: str) -> SettlementPlan`；`expiry_payoff(*, legs: tuple[PayoffLeg, ...], delivery_prices: tuple[tuple[str, Decimal], ...]) -> tuple[tuple[str, Decimal], ...]`（各结算币现金流，不自动汇总币种）。

- [x] **Step 1: 手算测试。** K100 long Call1 到期150给50；Put1 到期80给20；OTM为0。Call与Put组成同 K100 straddle、paid premium25，则 S70/100/140 的到期 gross 为30/0/40，net 为5/−25/15。期权成本来自独立 fixture，不能调用定价模型算期望值。

```python
def test_call_cash_and_future_transition_have_equal_final_value():
    call = linear_spec(settlement_rule="linear_via_future")
    plan = settlement_plan(spec=call, quantity_native=D("1"),
                           delivery=observation("150", time_ns=1000), expiry_id="expiry-1")
    assert [a.kind for a in plan.actions] == ["EXERCISE_TO_FUTURE", "FUTURE_DELIVERY"]
    assert plan.actions[0].future_quantity_base == D("1")
    assert plan.actions[1].amount == D("50")
```

- [x] **Step 2: RED。** `uv run pytest tests/options/test_payoffs.py -q`。
- [x] **Step 3: 实现精确现金流。** Call per-base intrinsic=max(S−K,0)，Put=max(K−S,0)，乘 base_quantity。支持条件：linear/european，quote/strike/settlement 同币、规则版本有效。不同币或 inverse 返回 UNSUPPORTED，不除以 index 近似当已支持。

```python
def intrinsic_value(*, spec, delivery_price):
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        signed = delivery_price - spec.strike if spec.option_type == "C" else spec.strike - delivery_price
        return max(signed, D("0"))
```

OTM 生成 EXPIRY；linear_cash 生成 CASH_SETTLEMENT；linear_via_future ITM 生成 linked EXERCISE_TO_FUTURE 与 FUTURE_DELIVERY。future base quantity：Call为+q_base、Put为−q_base，进入 ledger 的只读系统 claim，不产生用户可交易订单。delivery fee 不在 payoff 隐含扣费，必须由独立一次 fee receipt 提供。
- [x] **Step 4: GREEN。** 同时间不同规则版本、负/NaN delivery、future source availability、OTM 无 future、Put short future 过渡、multiplier、未到期不能生成结算、不得把 fixture delivery 称 observed。测试 stake cash 与 quote currency 不同拒绝。另测静态debit Call spread：long K100、short K120、net premium5，S70/110/140的gross为0/10/20、net为−5/5/15；PayoffLeg负腿不能转成P1 LegIntent或通过risk。静态数学有限亏损不证明保证金或途中退出安全。
- [x] **Step 5: 提交。** `test(options): pin linear option settlement cashflows` / `feat(options): model linked linear expiry settlements`。

### Task 4: 实际现金、部分成交和风险占用账本

**Files:** Create `zenquant/options/ledger.py`；Test `tests/options/test_ledger.py`。

**Interfaces:** Consumes Task 1 contracts。Produces：

| 类型/函数 | 精确字段/语义 |
| --- | --- |
| WalletBalance | `wallet: WalletKey, cash: Decimal` |
| OptionPosition | `wallet: WalletKey, instrument_id: str, intent_id: str, quantity_native: Decimal, premium_basis: Decimal, opening_fees_basis: tuple[tuple[WalletKey,Decimal],...]`；fee按币种保留，不能与premium跨币相加 |
| Reservation | `wallet: WalletKey, intent_id: str, order_id: str, remaining_cash: Decimal, remaining_quantity_native: Decimal` |
| SystemClaim | `wallet: WalletKey, instrument_id: str, link_id: str, intent_id: str, quantity_native: Decimal, future_quantity_base: Decimal, strike: Decimal, settlement_amount: Decimal, premium_basis: Decimal, opening_fees_basis: tuple[tuple[WalletKey,Decimal],...]` |
| LedgerEvent | `key: EventKey, kind: str, wallet: WalletKey, instrument_id: str | None, intent_id: str | None, order_id: str | None, sleeve: str | None, quantity_native: Decimal | None, price: Decimal | None, amount: Decimal | None, fee: Decimal, fee_wallet: WalletKey | None, parent_id: str | None, raw_hash: str` |
| LedgerState | `specs: tuple[InstrumentSpec,...], initial_balances: tuple[WalletBalance,...], balances: tuple[WalletBalance,...], positions: tuple[OptionPosition,...], reservations: tuple[Reservation,...], claims: tuple[SystemClaim,...], receipts: tuple[LedgerEvent,...], unapplied: tuple[LedgerEvent,...], last_sequence: int, last_available_ns: int, frozen_reasons: tuple[str,...]` |
| initial_state | `initial_state(*, specs: tuple[InstrumentSpec,...], balances: tuple[WalletBalance,...]) -> LedgerState` |
| apply_event | `apply_event(state: LedgerState, event: LedgerEvent) -> LedgerState`；纯归约，保存实际负余额 |
| wallet_cash | `wallet_cash(state: LedgerState, wallet: WalletKey) -> Decimal`；未知钱包报显式错误，不查网络 |
| active_risk_native | `active_risk_native(state: LedgerState, wallet: WalletKey) -> Decimal`；匹配该wallet的premium_basis及各持仓/claim的同wallet fee basis，均为该wallet本币 |

事件种类登记为 `RESERVE / CANCEL_CONFIRMED / FILL / FEE / MARK / EXPIRY / EXERCISE_TO_FUTURE / FUTURE_DELIVERY / CASH_SETTLEMENT / CORRECTION`。每种 required/forbidden payload 在模块常量 schema 内固定。RESERVE 必须 amount>0、order/intent；FILL 必须 qty非零、price非负、instrument/order/intent，费用非负；MARK 不改变现金；结算携带 expiry link；CORRECTION 保留 raw receipt 并冻结，P1 不自作主张修订现金。

- [x] **Step 1: 写完整手算 fixture。** `fill_event(n, *, spec, quantity, price, fee="0", intent_id="i1", order_id="o1", sleeve="tail", event_ns=None, fee_wallet=None) -> LedgerEvent` 定义于 factories；BUY qty正、SELL退出qty负。sleeve是由冻结intent归属的内部标签，不从交易所报价猜测。

```python
def test_partial_close_keeps_exact_remaining_basis():
    spec, w = linear_spec(), wallet()
    state = initial_state(specs=(spec,), balances=(WalletBalance(wallet=w, cash=D("1000")),))
    state = apply_event(state, fill_event(1, spec=spec, quantity="1", price="20", fee="2"))
    assert wallet_cash(state, w) == D("978")
    state = apply_event(state, fill_event(2, spec=spec, quantity="-.4", price="30", fee=".4"))
    assert wallet_cash(state, w) == D("989.6")
    assert state.positions[0].quantity_native == D(".6")
    assert state.positions[0].premium_basis == D("12")
    assert state.positions[0].opening_fees_basis == ((w, D("1.2")),)
    assert active_risk_native(state, w) == D("13.2")
```

- [x] **Step 2: RED。** `uv run pytest tests/options/test_ledger.py -q`。
- [x] **Step 3: 实现 cash/position/reservation reducer。** BUY扣 premium+fee，增加按 intent 分配的基础成本；SELL仅已存在 long 的退出，增加出售 premium、扣fee，按关闭比例移除 basis。qty单位由 Task1函数处理，不把 option notional 当本金扣除。费用由 fee_wallet 入账，不能偷偷转换币种。

```python
# BUY 的独立现金与基础成本关系；必须处于固定 Decimal context 内。
paid = premium_cash(spec, event.price, event.quantity_native)
cash_after = cash_before - paid
premium_basis_after = premium_basis_before + paid
opening_fee_basis_after_for_fee_wallet = opening_fee_basis_before_for_fee_wallet + event.fee

# SELL 部分退出，费用另扣一次；不允许形成策略空头。
fraction_closed = (-event.quantity_native) / quantity_before
remaining_premium_basis = premium_basis_before * (D("1") - fraction_closed)
remaining_fee_basis_for_each_wallet = opening_fee_basis_before_for_that_wallet * (D("1") - fraction_closed)
```

实际 FILL 超过现金或预算时照实记现金/仓位并冻结，不能当下单检查失败而丢弃。成交释放对应 order 的剩余现金预留，同时新增 active risk；未知 order 的已知合约实际成交也保留且冻结。退出数量超过 long 或未知/unsupported instrument 时保留 unapplied 原回执、标 accounting 不完整，禁止把未核算负债当零；不凭空创建可交易空头模型。

RESERVE 不改变现金，只降低 spendable；CANCEL_CONFIRMED 释放尚未成交部分，取消请求不是确认。取消后迟到成交仍入账。不同钱包现金不转移；缺 fee wallet 则保留 unresolved receipt 并冻结。
- [x] **Step 4: GREEN。** 一笔 premium 不双扣、fee不同币、两个 intent 同合约分账、部分成交、超现金负余额、取消竞争、未知合约可追溯。真实外来异常必须使 model/account evidence 非 VALID。
- [x] **Step 5: 提交。** `test(options): verify cash and partial inventory accounting` / `feat(options): account actual long option fills and reservations`。

### Task 5: 幂等、到期生命周期、知识时间与恢复

**Files:** Modify 新建的 `zenquant/options/ledger.py`；Create `zenquant/research/options_platform/__init__.py`、`replay.py`；Test `tests/options/test_replay.py`。

**Interfaces:** Consumes LedgerState/LedgerEvent/SettlementAction。Produces `valuation(*, state, marks: tuple[tuple[str, Observation],...], fx: tuple[tuple[str, Observation],...], asof_ns: int, timing: TimingPolicy, report_currency: str) -> Valuation`，`Valuation(cash_by_currency: tuple[tuple[str,Decimal],...], model_nav: Decimal | None, reason: str, spendable_by_wallet: tuple[tuple[WalletKey,Decimal],...])`；research `replay(*, initial: LedgerState, events: tuple[LedgerEvent,...]) -> tuple[LedgerState,...]`、`checkpoint_payload(*, initial, events, expected_state) -> dict`、`restore_checkpoint(payload: dict | str) -> LedgerState`、`settlement_to_ledger_events(*, plan, key, wallet, intent_id, fee_amount) -> tuple[LedgerEvent,...]`。

- [x] **Step 1: 到期手算与幂等 RED 测试。** Task4 的 `.6` Call K100在S150结算+30，delivery fee .3，cash1019.3，position/claim清零，总净变化19.3。同事件重复不双结算；future过渡期间claim30不是可下单现金。

```python
def test_known_nav_is_not_exit_cash():
    spec, w = linear_spec(), wallet()
    state = initial_state(specs=(spec,), balances=(WalletBalance(wallet=w, cash=D("1000")),))
    state = apply_event(state, fill_event(1, spec=spec, quantity="1", price="20", fee="2"))
    value = valuation(state=state, marks=((spec.instrument_id, observation("25", time_ns=2)),),
                      fx=(), asof_ns=2, timing=TimingPolicy(max_age_ns=100, minimum_source_age_ns=0),
                      report_currency="USDT")
    assert value.model_nav == D("1003")
    assert wallet_cash(state, w) == D("978")
```

- [x] **Step 2: RED。** `uv run pytest tests/options/test_replay.py -q`。
- [x] **Step 3: 实现生命周期与知识时间。** incoming journal_sequence/available time 单调；event_ns 允许迟到，不能排序后假装当时已知。幂等 identity 为 source+wallet+native event ID，经济 payload hash 不包含重传接收时间；同经济内容重复只记 dedup 证据，不重扣现金；冲突保留 receipt/unapplied 并冻结。

EXERCISE_TO_FUTURE 移除 option、建立 linked claim，保留风险占用；FUTURE_DELIVERY/CASH_SETTLEMENT 才增加可用 cash并关闭 claim/position。FEE显式一次；same link duplicate不重付；未完成过渡仍占预算。交易规则有效期、过早 expiry、缺parent或错误qty均保留失败证据；不得以其它expiry补齐。

`replay` 返回每步 state，不取系统当前时间。checkpoint保存 specs/初始余额/知识时间事件、schema版本及预期最终 hash，恢复逐事件重新归约，hash不一致拒绝；不pickle不执行代码。未知 JSON key/重复key/float金额/不完整checkpoint抛 `ValueError("INVALID_CHECKPOINT")`，报告层映射为INVALID，不能返回貌似正常LedgerState。分两段恢复结果与一次回放逐步hash一致。

```python
def replay(*, initial, events):
    states, state = [], initial
    for event in events:
        state = apply_event(state, event)
        states.append(state)
    return tuple(states)
```

valuation 按本币 cash+fresh model mark×base quantity，系统claim按可验证delivery值计数学净值但不可提现。FX不足、mark缺失、unapplied或未来可用数据时 model_nav=None；cash与库存仍展示，不填零收益。报告退出价值另由成本函数求，缺 bid 深度即未知。
- [x] **Step 4: GREEN。** duplicate/hash conflict、event_ns迟到但available后来、撤单后latefill、expiry顺序、OTM、系统future不能变用户头寸、费用一次、缺mark/FX、checkpoint截断/篡改。检查手算现金逐步正确。
- [x] **Step 5: 提交。** 生命周期修改与新增research replay分别按概念提交；tests可单列，格式不混逻辑。

### Task 6: 小资金审批、实际风险占用与情景分析

**Files:** Create `zenquant/options/risk.py`；Test `tests/options/test_risk.py`。

**Interfaces:** Consumes intent/state/cost evidence。Produces `RiskPolicy(per_intent_report: Decimal, active_and_pending_report: Decimal, rolling_tail_paid_report: Decimal, rolling_window_ns: int, report_currency: str, fx_timing: TimingPolicy | None = None)`、`RiskApproval(status: StatusResult, approved_intent: CombinationIntent | None, cash_reserve: tuple[tuple[WalletKey,Decimal],...], reasons: tuple[str,...], policy_hash: str, execution_authority: str)`；`execution_authority` 固定 `NONE`，批准只表示模型内可行。`approve_intent(*, intent, state, costs: tuple[CostEvidence,...], wallet_fx: tuple[tuple[WalletKey,Observation],...], policy, asof_ns) -> RiskApproval`；`long_expiry_scenarios(*, intent, deliveries: tuple[tuple[tuple[str,Decimal],...],...], paid_by_currency: tuple[tuple[str,Decimal],...]) -> tuple[tuple[tuple[str,Decimal],...],...]`。

- [x] **Step 1: 写拒绝和占用测试。** .01 BTC、ask1600、index80000、taker.0003/cap.07，支付16.24，10U预算拒绝，不扩大数量或假设maker。先申请30U、成交后active30，订单reserve0，第二笔25U使总55超过50，必须拒绝。所有venue现金只加总展示，不跨wallet批准。

```python
def test_active_capital_survives_reservation_release():
    pending_before, active_before = D("30"), D("0")
    pending_after, active_after = D("0"), D("30")
    proposed, cap = D("25"), D("50")
    assert pending_before + active_before == pending_after + active_after
    assert pending_after + active_after + proposed > cap
```

将此独立算术案例接入真实 `approve_intent` 测试：fixture approve、reserve、fill，再approve，检查 REJECTED 与 reason `ACTIVE_AND_PENDING_LIMIT`；不能只保留镜像算术测试。
- [x] **Step 2: RED。** `uv run pytest tests/options/test_risk.py -q`。
- [x] **Step 3: 实现审批。** 先查state frozen/unapplied、intent有效期与身份、标准账户模式、linear/european支持、模板腿关系、costs完整和数据时效。straddle同underlying/expiry/strike且C/P；strangle同underlying/expiry且put strike<call strike，qty比例明确，不混不同币终值。

检查每wallet `cash - pending_reserve >= proposed_cost`。按显式FX将pending、active和提案映射到policy report currency；proxy只给modeled feasibility，不使account eligibility变VALID。未知FX/actual费率只报告NOT_EVALUABLE相应维度。

每腿cost必须绑定相同instrument_hash、合法qty、BUY side、报价/报告币种、时效与当前policy。检查costs与legs一一对应；不能以另一个更便宜合约、SELL收入或过去cost批准新买入。预留currency必须与目标wallet一致。

```python
with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
    combined_use = pending_report + active_report + proposed_report
    tail_proposed_report = proposed_report if intent.sleeve == "tail" else D("0")
    allowed = (proposed_report <= policy.per_intent_report
               and combined_use <= policy.active_and_pending_report
               and tail_paid_report + tail_pending_report + tail_proposed_report
                   <= policy.rolling_tail_paid_report)
```

tail付费仅统计sleeve=tail的实际premium/fee，并加入尚未支付的tail预留和本次提案；按收到信息asof与经济支付时间共同限制，过去30天已支付累计不因平仓回款扣回。已收到但intent归属未知的成交照实记账并冻结预算审批，不能忽略它使tail消耗变零；未来未收到事件不可计入。风险占用按remaining原始成本与各币opening fee basis，先按wallet本币统计再显式FX映射，数学盈利不释放预算。硬拒绝不生成非零 approved_intent；持仓异常只冻结，不追加入金。

压力分析输出expiry payoff矩阵及long premium全损失上界，区分费用/币种风险。P1没有IV模型、短腿/保证金/未来可达成交模型，相应途中估值和真实最大风险项保持NOT_EVALUABLE，不用Delta近似承诺风险封顶。
- [x] **Step 4: GREEN。** 连续新intent、部分成交、取消竞争、多个钱包、过期approval、上限精确边界、paid损耗滚动窗口、不支持腿、qty太小正常NO_TRADE报告。3,000U配置单独运行不叠加净值。
- [x] **Step 5: 提交。** `test(options): pin small-account budget invariants` / `feat(options): approve funded long portfolios within explicit budgets`。

### Task 7: 封存面板适配、A01 静态成本和工程偏差报告

**Files:** Create `zenquant/research/options_platform/panel.py`、`reporting.py`；Test `tests/options/test_panel.py`。

**Interfaces:** Consumes本地Path与Task1/2核心。Produces `PanelInput(specs: tuple[InstrumentSpec,...], books: tuple[Book,...], observations: tuple[Observation,...], identities: tuple[tuple[str,str],...], assumptions: tuple[str,...], records: tuple[PanelRecord,...] = (), gaps: tuple[PanelGap,...] = (), protocol_json: str = "{}", selection_json: str = "{}", requests: int = 0, sealed_entries: int = 0, verification: PanelVerificationBinding | None = None)`；`load_panel(root: Path) -> PanelInput`、`diagnostic_panel_report(*, panel: PanelInput, quantities_base: tuple[Decimal,...]) -> dict`、`engineering_bias_report(*, panel: PanelInput) -> dict`、`manual_scenario_report(*, initial: LedgerState, events: tuple[LedgerEvent,...], policy: RiskPolicy) -> dict`。

- [x] **Step 1: 写最小三平台raw fixture与完整性失败测试。** tmp_path中各放一条真实格式 response，selection、request ledger与seal记录字节/SHA；所有hash通过不代表账户可交易。fixture不需复制完整面板。输入路径中的raw_path不得绝对路径、`..`、symlink越界；seal不能自行覆盖，未列入hash的输入不读。

```python
def test_tampered_raw_is_not_valid_panel(tmp_path):
    root = write_minimal_panel(tmp_path)  # 本任务定义 fixture writer
    raw = root / "raw" / "bybit.json"
    raw.write_text(raw.read_text() + " ")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        load_panel(root)
```

`write_minimal_panel(tmp_path) -> Path` 写 Bybit `retCode/result.s/b/a/ts`，Deribit `testnet/result.instrument_name/state/bids/asks/timestamp/index_price`，OKX `code/data[0].bids/asks/ts`；先写bytes再hash进ledger/seal。额外fixture覆盖错误API状态、symbol错、futureseq无法证明、缺族数据；每个错误保留结构化缺证原因。
- [x] **Step 2: RED。** `uv run pytest tests/options/test_panel.py -q`。
- [x] **Step 3: 实现只读适配。** 固定读取 selection/preregistration/request_ledger/rounds 与对应raw，parse_float=Decimal，禁止网络与导入throwaway代码。核对全部使用输入bytes/hash、metadata来源以及请求身份。历史固定元数据是snapshot，不宣称完整规则沿革；tick bands保留，不用单tick替代动态表。

| 原始产品 | native数量→base | native报价 | ledger权限 |
| --- | --- | --- | --- |
| Deribit inverse BTC | quantity（base），multiplier1 | BTC/base；转换报告USD需同格index | UNSUPPORTED；仅成本诊断 |
| Deribit USDC BTC | quantity（base），multiplier1 | USDC/base | standard+全额long模型可支持，账户事实未知 |
| Bybit BTC USDT | quantity（base），qtyStep/min来自lotSizeFilter | USDT/base | 同上 |
| OKX BTC-USD coin | contracts×ctVal×ctMult，本面板=.01BTC/张 | BTC/base | UNSUPPORTED；仅成本诊断 |

指数代理、USD/USDT/USDC parity和fee proxies逐条进入assumptions。Deribit两类family不能合并；实际DTE=expiry−known asof，第三组21.61天不标真30D。BTC以外元数据未采集写NOT_EVALUABLE，不生成ETH订单簿。

Deribit缺独立qty step字段时只可沿用封存面板的min_trade_amount步进代理并记录来源，不能称已确认账户交易精度。跨所诊断按preregistration的server/receive pair gap≤2秒，所有依赖在共同asof的source age为−1到3秒，包含兑换index；不同quantity重算同一pair不增加独立样本。单场所最小量cost用VWAP，排行采用共同输入asof，不能仅按先到腿的age。

报告schema `options-p1-diagnostic.v1` 至少含：独立 code/data/contracts/ledger/cost/risk/protocol hash、封存输入身份、quantity/时间过滤、总请求/盘口数、成本表、缺证、静态路由baseline、工程偏差、数学scenario、状态权限。`diagnostic_only=true`、`rankable=false`、`live_eligible=false`；actual/paper/net strategy pnl、Sharpe、容量都null。数学fixture的cash delta仅在 `math_scenarios` 下，不命名为actual收益。

工程偏差只变换离线假设：mid替代bid/ask、忽略fee、错误min/ctMult、丢掉index时效。每个变体仍nonrankable，比较假机会数量/成本偏差，不混入合法机会排行。A01只比较显示成本，不声称部署路由或成交节省已证明。
- [x] **Step 4: GREEN与封存来源集成。** 独立fixture通过后，用真实面板检验480记录与合法/时效计数、费用代理后0正交叉，保留不同统计单位。预期counts来自封存summary，不以调用旧analyze产物当新算法期望；差异必须解释口径，不能调整时效凑数。
- [x] **Step 5: 提交。** tests、panel、reporting按独立责任提交。可复用已有 strict identity_hash 仅在research方向；若依赖未提交的 `run_evidence.py`，在报告声明工作树dependency hash，不能假设已提交。核心fingerprint不得反向依赖research。

### Task 8: 离线用户入口、边界强制与交付验收

**Files:** Create `scripts/analyze_options_portfolio.py`、`tests/options/test_cli.py`、`tests/options/test_boundaries.py`；必要说明放本计划交付记录，不新开一套平行架构文档。

**Interfaces:** CLI消费Task7 report与Task5/6手算情景。入口 `main(argv: list[str] | None = None) -> int`。用户指定 `--panel-root PATH --output-dir PATH --capital 1000|3000 --scenario long-call|straddle`；未指定panel时只跑标为fixture的数学演示。无 `--live`、账户env、exchange参数或方向信号参数。

- [x] **Step 1: CLI与AST边界测试。** 运行只产生独立新目录 `report.json / REPORT.md / identity.json`；已存在output拒绝覆盖，output在panel源或父目录内拒绝。manifest包含报告bytes/hash；stderr错误原因，JSON权限字段固定false。

```python
def test_cli_never_promotes_math_scenario(tmp_path):
    out = tmp_path / "report"
    code = main(["--output-dir", str(out), "--capital", "1000", "--scenario", "long-call"])
    assert code == 0
    data = json.loads((out / "report.json").read_text())
    assert data["diagnostic_only"] is True
    assert data["rankable"] is False and data["live_eligible"] is False
    assert data["actual_pnl"] is None and data["sharpe"] is None
```

测试patch socket/HTTP入口为抛异常，证明无调用；不读账户环境。AST扫描全部options子目录，包括__init__/relative imports，只允许核心stdlib白名单（`__future__, dataclasses, decimal, enum, hashlib, json, typing, collections`）及 `zenquant.options`；研究可依赖核心，禁止legacy/app/strategy/execution/risk import新options/research options_platform。语法错误直接FAIL，不像旧helper静默返回空。
- [x] **Step 2: RED。** `uv run pytest tests/options/test_cli.py tests/options/test_boundaries.py -q`。
- [x] **Step 3: 实现CLI及错误边界。** argparse只接受上述选项；CLI创建目录在全部输入校验后，使用临时兄弟目录+原子发布，目标存在不覆盖；失败保留可识别临时产物，不能把partial当完整。退出0=诊断产物完整（可以NO_TRADE/NOT_EVALUABLE）；2=输入或身份无效；1=未完成/内部故障。不会因预算不足而非零退出，报告理由。

```python
parser = argparse.ArgumentParser(description="Offline options portfolio diagnostics")
parser.add_argument("--panel-root", type=Path)
parser.add_argument("--output-dir", type=Path, required=True)
parser.add_argument("--capital", choices=("1000", "3000"), default="1000")
parser.add_argument("--scenario", choices=("long-call", "straddle"), default="long-call")
```

禁止借argv写入封存源、secret logging或自动安装依赖。数学演示与面板假设在报告顶端注明，不展示“预计年化”或虚构Sharpe。
- [x] **Step 4: GREEN与本地可用演示。** 执行如下命令，输出目录必须新建且位于results新命名空间；两个capital报告独立。

```sh
uv run python scripts/analyze_options_portfolio.py --panel-root results/crypto_opportunity/20261009_options_liquidity_panel_v1 --output-dir results/options_platform/p1_1000 --capital 1000 --scenario long-call
uv run python scripts/analyze_options_portfolio.py --output-dir results/options_platform/p1_3000 --capital 3000 --scenario straddle
uv run pytest tests/options/ -q
uv run ruff check zenquant/options/ zenquant/research/options_platform/ tests/options/ scripts/analyze_options_portfolio.py
uv run ruff format --check zenquant/options/ zenquant/research/options_platform/ tests/options/ scripts/analyze_options_portfolio.py
```

格式修复只作用新文件，单独提交style。改报告目录名不代表重新取行情；输入hash相同保持同一资料标签。
- [x] **Step 5: 全仓验收一次。** 先记录未改P1前的必要baseline，执行后运行 `uv run zenquant-check`。实际runner包含0/A/B/C/D/E/F：Gate0因子健康、GateF现有walk-forward均需检查；不得只跑quick冒充全部通过。本轮已读取当前 `scripts/walk_forward_eval.py`：执行既有配置的train/dev回放评价，不调用optimizer或新训练；运行它不授予旧冻结候选重新寻优权限。

基础依赖/数据/数据库错误作为既有环境缺口记录；不为让全仓通过修改生产、其它未提交工作或重写golden。P1局部通过与全仓阻断分别报告，未全通过不merge/promotion。成功后，不因“加强验证”反复运行全仓。
- [ ] **Step 6: 全分支独立审查与交付（已完成初审，I1/I2/M1 已修复，待一次 scoped 复审）。** 新reviewer核对实际diff、手算现金轨迹、failures、imports、封存源未改和完整gate结果。报告写明实际运行命令、配置、结果、限制与后续P2数据缺口；不部署、不下单、不继续 optimizer。

## 自审覆盖与执行顺序

| 规格 P1 条款 | 任务/测试证据 |
| --- | --- |
| 通用元数据与三平台报价单位 | Task1、Task2、Task7 |
| long-only linear、标准模式和范围外拒绝 | Task1、Task3、Task4、Task6 |
| cash/partial/expiry/fee/mark/reserve/dedup/修订 | Task4、Task5 |
| Call/Put/双long payoff、其它策略仅静态 | Task3、Task6；风险明确拒绝范围外执行 |
| 最小数量/VWAP/FX/预算、正常NO_TRADE | Task2、Task6、Task8 |
| 独立手算与封存报价诊断、restart parity | Task3、Task5、Task7 |
| A01静态对照与乐观偏差不可晋级 | Task7、Task8 |
| fixture来源、真实账户/成交缺证分列 | Task1、Task7、Task8 |
| 新边界与旧路径全gate | Task8 |

顺序为 1→2→3→4→5→6→7→8。contracts冻结后，不临时改邻居接口；确需改动先记录契约变更、同步调用与测试，独立审查后继续。每个任务完成一个可测试交付，由fresh reviewer审查后再进入依赖任务，不安排共享文件并发写入。

计划已审阅并按用户授权执行。阶段完成以产品代码、局部行为证据、一次完整 gate 和独立审查为准；全仓失败保留阻断，不以计划落盘或局部通过代替。

## 本轮计划自审与实现前基线

- 自审已逐条映射规格第8节和五项Review Focus；补齐币种费用basis、静态PayoffLeg与可执行LegIntent的区别、cost/intent身份绑定、tail已支付+待支付预算，避免接口与计价歧义。
- 文档机械检查通过：八任务、41 checkbox步骤、16段Python示例AST可解析；本地规格链接有效，无空白占位或未闭合code fence。
- 2026-10-09、P1产品文件尚未创建时，运行 `uv run pytest tests/test_import_boundaries.py tests/test_golden_output.py tests/test_parity.py -q`：**10 passed / 1 failed**。失败为 `TestGoldenOutput::test_backtest_matches_golden_output`，当前 `net_pnl=-2639.28`，保存golden `net_pnl=11502582.13`。没有更改golden或调查性修改生产；根因未判定。
- 这份基线只绑定当前含用户未提交工作的checkout，不代表干净HEAD的结果。局部P1可以继续建设和验证；集成验收保留Gate C阻断，不能因此宣称全仓通过、merge或promotion。
- 上述 baseline 取于实现前；本轮现已新增 P1 产品模块和离线诊断入口。没有网络采集、实盘、收益策略实验、optimizer 或生产变更。


## P1 实际接口补充与裁决成本

这些说明更新本计划当前契约，不替代实现测试，也不授予账户或执行权限。

1. Ruling: Keep the approved in-place additive workspace rather than moving user dirty files into a new worktree — the plan explicitly preserves this checkout and local sealed sources — cost if wrong: new commits coexist on the same private branch and must be separated by their exact paths; no pre-existing file may change.

2. Ruling: The family-change identity test must change product and its matching settlement/currency metadata together; reject financially inconsistent InstrumentSpec combinations — the spec requires economic identity, not just enum membership — cost if wrong: metadata admission may reject a newly supported variant until its explicit rule is added.

3. Ruling: Sequential implementation and per-task review; parallel work restricted to read-only independent audits — shared factories and ledger prevent concurrent implementer writes — cost if wrong: some wall-clock speed is sacrificed to preserve interface and index safety.

4. Ruling refinement: InstrumentSpec representation enforces product/settlement-rule coherence but allows distinct quote/strike/settlement/margin currencies; operational payoff/risk admission rejects unsupported combinations. LegIntent represents a positive proposal; wallet venue/currency matching is checked in risk admission rather than preventing an auditable rejection. Evidence: approved shared contracts separate representation from supported P1 operation. Cost if wrong: unsupported proposals exist structurally and every operational entry must explicitly fail closed. This replaces the currency-restriction portion of the earlier family-identity ruling.

5. Ruling: Native quote diagnostics require quote-denominated fee/index and explicit quote-to-report FX, but do not require strike/settlement/margin equality; terminal payoff/risk enforce supported currencies later. Aggregate level cash directly instead of multiplying rounded VWAP. Evidence: Task 2 is price diagnosis and Task 3/6 own operational admission. Cost if wrong: quote evidence can exist for unsupported terminal economics and must never be interpreted as an executable spread.

6. Ruling: Extend SettlementPlan minimally with delivery: Observation | None = None; generated VALID plans retain original delivery. Settlement knowledge cutoff is delivery.available_ns (no external cutoff in interface); source must be at/after expiry and no later than receipt, rule must cover contractual expiry. Task 5 conversion requires key.available_ns >= delivery.available_ns and explicit delivery evidence. Evidence: status/actions alone lose point-in-time provenance; normal delayed final fixings must remain representable. Cost if wrong: one additive interface field and downstream validation; external historical decision freshness remains unproved without explicit event cutoff. The initial suggestion to carry knowledge time only in status.reason was superseded before implementation.

7. Ruling: Known finite actual fills below/off the order-level quantity limits use explicit native-unit arithmetic, record liabilities and freeze uncertain fill-rule evidence; never bypass pretrade quantity admission. Evidence: minimum order is not minimum fill, and actual external obligations cannot be discarded. Cost if wrong: some legitimate partial fills are conservatively frozen until independent fill-lot rules exist; their cash/basis remain correct.

8. Ruling: RiskPolicy gains optional fx_timing: TimingPolicy | None = None. Any non-report-currency wallet conversion requires explicit bounded timing; absent policy => NOT_EVALUABLE rather than implicit freshness. Same-currency accounting needs no conversion observation; provided observations still must be causally known. Evidence: approved RiskPolicy had no place to freeze FX age bounds, yet stale FX cannot justify budget approval. Cost if wrong: cross-currency proposals are more restrictive until an explicit timing policy is supplied; one additive optional field affects policy hashes.

9. Ruling: Task5 fixes settlement_plan to accept any positive finite residual quantity, separate from new-order min/step/max; supported payoff uses explicit quantity*multiplier without pretrade helper admission. Evidence: payoffs.py:109 enforces order constraints on settlement, but Task4 actual partial inventory may legally be below order minimum. Cost if wrong: mathematical settlement can represent off-order-lot residuals, which still cannot create an intent or executable order; actual ledger anomalies stay frozen. Task5 owns focused payoff regression and review.

10. Ruling: Settlement conversion scopes plan link by fingerprint((original action.link_id, wallet, intent_id)); retained plan identity alone omits ownership and collides for equal quantities in two intents. Evidence: OptionPosition is intent-specific but SystemClaim has no explicit intent field. Cost if wrong: adapter links differ from raw plan links and must preserve derivation evidence; native actual receipt links are not rewritten.

11. Ruling: restore_checkpoint accepts dict | str so raw JSON duplicate keys can be rejected before decoding via strict object-pairs hook; dict contract retained, float/nonfinite/exact schema failure => INVALID_CHECKPOINT. Evidence: duplicate keys are irretrievably lost in already-decoded plain dict. Cost if wrong: small additional parser surface; a pre-decoded dict still cannot prove raw serialization lacked duplicates and that limit is disclosed.

12. Ruling: SystemClaim additionally stores intent_id and positive original quantity_native. Evidence: delivery must reject wrong intent/native quantity without reverse-engineering ownership or dividing rounded future base units; explicit fields improve auditability. Cost if wrong: two required new fields in this newly introduced record/checkpoint schema, all consumers must use the updated contract; no existing production user is affected. Scoped link still prevents equal-quantity intent collisions.

13. Ruling: Historical non-report-currency tail outlays require payment-time reporting FX evidence; current wallet_fx cannot reconstruct arbitrary old paid value. P1 returns NOT_EVALUABLE for unsupported historical paid conversion instead of marking it down with current FX or using zero; native same-report-currency history remains supported. Evidence: later FX decline must not refund historical paid budget. Cost if wrong: foreign-currency tail history is held pending richer receipts; P1 loses some modeled feasibility to protect the rolling-paid cap.

14. Ruling: Missing settlement proof in panel becomes proxy spec with UNKNOWN exercise style, UNVERIFIED_SNAPSHOT_PROXY version, coherent hypothetical settlement rule/window1 sentinel labelled UNKNOWN, and valid_from as verified metadata knowledge bound (not true rule effective date). Built-in real seals pinned; tests may inject private fixture pins only. Evidence: price diagnostics need structural spec fields but corpus cannot prove settlement/account rules. Cost if wrong: panel specs intentionally cannot enter operational payoff/risk; unknown sentinel must never be displayed as actual 1ns settlement rule.

15. Ruling: PanelInput gains optional immutable verification binding covering actual canonical inputs plus source identities; report data hash fingerprints all actual input fields, and absent/mismatched binding downgrades integrity instead of claiming pinned verification. Evidence: public hand-built or replaced inputs inherited verification from source identity alone. Cost if wrong: one additive diagnostic contract field and larger data identity; binding records local loader integrity, not exchange-signed authenticity or protection from malicious in-process Python callers.

16. Ruling: Task8 may minimally remove itertools.pairwise from the new P1 costs module and use equivalent zip(levels, levels[1:]); core whitelist stays unchanged. Evidence: the new AST gate exposed a real Task2 import violation. Cost if wrong: one extra P1-only fix commit and cost regression run; no legacy or economic behavior is intentionally changed.

17. Ruling: CLI publishes outputs using OS no-replace atomic rename via stdlib ctypes on Darwin/Linux, fail-closed when unavailable. Evidence: ordinary rename can overwrite an existing empty destination after a precheck race. Cost if wrong: output publication is platform-limited and depends on the OS primitive; unsupported systems retain recognizable staging and cannot report completed publication.

18. Ruling refinement: Allow one explained noqa:RUF007 on the adjacent zip loop, not a global lint exemption or broader core import whitelist. Evidence: Ruff recommends forbidden itertools.pairwise for the equivalent whitelist-safe replacement. Cost if wrong: that single line no longer receives this style recommendation; cost behavior regressions and AST boundary enforcement remain active.

19. Ruling: Retain the single full harness run as non-frozen checkout diagnostics, not final-HEAD integration acceptance, after implementation changes occurred during Gate E; validate final changes with focused tests and fresh identity-bound demos and leave integration blocked. Evidence: run started at475e63d, then eb5dad9/0587247 landed during execution; baseline Gate C already failed. Cost if wrong: final whole-repo acceptance remains unavailable and requires a future frozen-snapshot run before any merge/promotion; local P1 results cannot substitute for that gate.

20. Ruling: CLI output guards read the existing private pinned-root registry as the single source of all sealed input roots, including the transitive venue-scout source and no-panel runs; resolved ancestry is protected without duplicating a fixed path list or adding a new adapter API. Evidence: protecting only panel-root allows writes into the sibling pinned metadata source. Cost if wrong: a documented CLI-to-adapter private-registry dependency must be kept synchronized; it grants no mutation or source-authentication authority.

21. Ruling: Portable tests require tracked deterministic raw fixtures; real sealed panel/scout integration has an explicit local-availability boundary and pytest-only --require-local-options-panel strict mode. Entirely absent local sources may be explicitly skipped only in normal runs; present incomplete/tampered sources and any strict missing source fail. Evidence: default tests depended on ignored workstation-only results. Cost if wrong: portable CI cannot certify the real corpus and must show the named skip; strict local source acceptance remains a separate required check, with no copying/download/reseal.

22. Ruling: Accept all14 final-review Declined-to-judge boundaries as outside the approved offline P1 scope, with the per-row reasons recorded in final-scope-adjudications.md; original actual order-budget overrun remains in scope and I1 must fix. Evidence: P1 withholds account/execution/authentication/performance/production authority and preserves unresolved receipts. Cost if wrong: these results provide no live-capital, service, security-authenticity or strategy-performance certification; the corresponding P2 evidence and integration acceptance remain outstanding.

## P1 本地交付与使用

直接运行（使用现有 uv 环境，无新依赖、凭证或服务）：

```sh
uv run python scripts/analyze_options_portfolio.py --panel-root results/crypto_opportunity/20261009_options_liquidity_panel_v1 --output-dir results/options_platform/p1_1000 --capital 1000 --scenario long-call
uv run python scripts/analyze_options_portfolio.py --output-dir results/options_platform/p1_3000 --capital 3000 --scenario straddle
uv run pytest tests/options/ -q
uv run ruff check zenquant/options/ zenquant/research/options_platform/ tests/options/ scripts/analyze_options_portfolio.py
uv run ruff format --check zenquant/options/ zenquant/research/options_platform/ tests/options/ scripts/analyze_options_portfolio.py
uv run zenquant-check
```

本轮两条 demo 均首次新建上述目录，退出0，各仅包含 `report.json / REPORT.md / identity.json`。再次运行必须给新的 output-dir；禁止覆盖，也拒绝 output 与 panel-root 互为祖先/后代、等于或经 symlink 落入封存源。退出0表示诊断完整（含 NO_TRADE/NOT_EVALUABLE），2表示参数/路径/封存身份无效，1表示内部错误或未完成发布；失败 staging 仅用于诊断，不能作为完整交付。

- 1,000 USDT long-call：BTC fixture K100/S150、.01 native/base，支付权利金.20、开仓费.02，到期现金.50、交割费.01；现金 `1000 → 999.78 → 1000.28 → 1000.27`，数学差额+.27。配置为每 intent10 / active+pending50 / 30日tail20 USDT；preflight仅fixture模型。
- 3,000 USDT straddle：相同K/S、C/P各.02，权利金各.40、开仓费各.02；Call交割1.00、Put归零，交割费各.01；现金 `3000 → 2999.58 → 2999.16 → 3000.16 → 3000.15 → 3000.15 → 3000.14`。配置每intent30 / active+pending150 / 30日tail60 USDT；独立本金，不叠加平台账户。
- 有 panel 的报告保留全部代理假设、UNKNOWN结算元数据、账户资格 NOT_EVALUABLE；封存价格仅比较显示成本。480 books、493 requests、2,640 legal-depth comparisons、2,586 joint-time-valid comparisons、fee-proxy-positive0。预算屏幕中320条 NO_TRADE（单intent超预算），840条缺显式币种FX、240条低于最小数量、40条显示价格在预算内但账户/结算未证，均不授予权限。
- 全部 actual_pnl / paper_pnl / net_strategy_pnl / sharpe / capacity 为空，diagnostic_only=true，rankable/live_eligible/execution_authority=false。fixture数学现金差额单列，不展示预期年化或虚构绩效。

修改方式：在 CLI `_fixture` 中改显式初始钱包、场景回执和 RiskPolicy，并同步独立现金预期测试；资本档位仅1000/3000。非报告币需要 `RiskPolicy.fx_timing` 和因果 FX observation，历史 tail 外币付款仍需付款时证据。新封存来源必须先在 panel adapter 经独立 pin、原始 bytes/hash 和元数据身份校验；改 output 名不产生新行情，也不改变同一 data hash 的资料标签。不得编辑源 seal 以“修复”不匹配。

P2 缺口：真实账户模式/地区/产品资格、实际费率层级、可达成交/部分成交回执、完整结算规则及官方 fixing、历史付款时FX、可执行跨平台平仓和利润现金对账、完整规则沿革、时间连续的真实OOS/paper证据。静态A01/乐观偏差诊断不能晋级策略；没有部署、真实账户policy激活、下单或live验收。

本次 demo 身份（bytes+SHA 另见每个目录的 identity.json）：

| 目录 | report.json SHA-256 | identity.json SHA-256 | fixture data hash |
| --- | --- | --- | --- |
| `results/options_platform/p1_1000` | `14e81cc117d5fcc533f8393671ab8043248030d377c085c22b7a79a9804a3254` | `76414590601d2777e4e2a39c3cc4d51506bea2205c665f37edb673129c0433ac` | `3e74c7d797c68203b907207cce883f81045ea0afeb69636adc20dcf1448bd0eb` |
| `results/options_platform/p1_3000` | `fede7c23c439ca89025b889ec9331c122908ca57d11caaf77fb63e0abc51cbda` | `0fd6781de5f53f0c692edbc14a0cd7fc891b88bb7f9d74e2bfaf1e834886a013` | `f87a422b0f6cd7968fa852003bbfe6cfddd06e16b72fc3d9bd76fb1373baf10e` |

`p1_1000` panel canonical input hash 为 `ec0d4f1ed8c962459d679f8e6c081ef2d8004b7ab36190ddee62eb19afdeb4ca`；PINNED_BYTES_VERIFIED 仅指 loader 的本地内容binding，不能当作交易所签名、真实账户或可达成交证据。

补充实现说明（不另编号）：CLI 数学 fixture 完成输入校验和序列化后写临时兄弟目录，两个报告文件的 bytes/SHA 纳入 identity，再以不覆盖原语原子发布。数学 `policy_enforced=false`，模型 preflight 单列且 execution NONE。argparse 不回显无效 argv 原值，仅保留安全错误类别，代价是输入诊断精度略低；未增加账户或凭证参数。

## 最终 Task 8 覆盖与全仓阻断

原始 p1_1000/p1_3000 演示在 CLI 输入分类/源目录保护自审修复前产生，保留不覆写。Task 8 当时可复核产物来自以下 fresh 目录（r2 保留历史身份；本次费用修复后的当前产物另见 r3），两条命令均退出0（并未重新取行情）：

```sh
uv run python scripts/analyze_options_portfolio.py --panel-root results/crypto_opportunity/20261009_options_liquidity_panel_v1 --output-dir results/options_platform/p1_1000_r2 --capital 1000 --scenario long-call
uv run python scripts/analyze_options_portfolio.py --output-dir results/options_platform/p1_3000_r2 --capital 3000 --scenario straddle
```

最终重点验证 `uv run pytest tests/options/test_cli.py tests/options/test_boundaries.py tests/options/test_costs.py -q`：96 passed in0.70s（CLI30、边界12、costs54）；随后仅测试排版提交，无行为变化。此前全 options 实际运行374 passed in2.50s，此后新增边界/错误用例只执行对应 focused 验证，不能把推算的最终总数说成再次全 options 通过。最终 scoped ruff check 通过，format22 files already formatted。105份受保护既有脏/未跟踪文件与 baseline-workspace.json SHA逐项一致；封存 panel/scout 均通过最终loader原始bytes+seal pins验证。

唯一完整 `uv run zenquant-check` 启于475e63d，UTC2026-10-09 11:10:14.044654，结束11:16:11.726041，总 wrapper357.681s（runner357.4s），退出1。运行中非冻结：边界-only eb5dad9 与 CLI 输入分类的 test5aaedcd/fix0587247（含随后排版）在 Gate E 时更改工作树。该完整运行仅为非冻结 checkout 诊断，不是最终HEAD集成验收；最终源目录/错误回显修复在runner结束后完成，只有上面的 focused/ruff/r2身份能证明该最终P1局部状态。不重复full runner、不改golden、不merge/promotion。

| Gate | 实际结果 | gate耗时 | 证据/限制 |
| --- | --- | --- | --- |
| 0 | PASS | 45.1s | 因子健康7 passed；不是新期权因子收益证明 |
| A lint | PASS | .1s | 全目录ruff check |
| A format | FAIL | .0s | 既有 `frontier_lab/experiments/d10_events.py`、`d11_gold.py`，未修改 |
| B | PASS | .2s | 旧import boundary4 passed；新域边界另有focused/GateE快照 |
| C | FAIL | .8s | 已知golden net_pnl -2639.28 vs11502582.13，未重写 |
| D | PASS | .7s | plugin/legacy parity6 passed |
| E | FAIL | 255.3s | 2489 passed、5 failed、4 skipped、38 subtests passed；非冻结collect快照 |
| F | PASS | 55.1s | 既有4-window train/dev回放判据无OVERFIT；Avg Test Sharpe -.41，不能当作有效收益或新训练准入 |

Gate E 失败为 `tests/test_backtest_invariants.py` 的 final_equity_exceeds_starting/net_pnl_is_positive/sharpe_is_positive，以及 `tests/test_execution_sensitivity.py` 的 dca_toggle_stable/dd_never_catastrophic；未调查性修改既有策略、用户工作或基线。完整 stdout+stderr、退出码和时间在 `.superpowers/sdd/2026-10-09-options-kernel-p1/task-8-full-gates.log`。全分支独立初审已发现 I1/I2/M1，汇总修复已完成；Task8 Step6仍未勾选，等待一次限定范围复审，集成仍 BLOCKED。

最终CLI source SHA-256 为 `a317ff4d541e220a467e9c76cee5dc3f422ae70ade5173f1a83e5b77bc9398ac`；两个r2的 source data/fixture/policy保持对应身份，code hash更新如下：

| 最终产物目录 | report.json bytes | report.json SHA-256 | identity.json SHA-256 |
| --- | ---: | --- | --- |
| `results/options_platform/p1_1000_r2` | 8933612 | `36abb6c2b466479241bba924d5e24186e6154de082f5f08b69c5895d708be1b3` | `68badade9cfd49e1d6544f3620c48bded680e4b4c99f5d24d13b27f469a75964` |
| `results/options_platform/p1_3000_r2` | 16588 | `caa3a090fae0f2cd4b5b81c62b7b7ee3bfa56266f1c5869de2c699d8a0bd9c05` | `4ae50aeb74f5d86afa26a7927ceb553a0590e07e95d0cd40f54a69d213755cd3` |


## 最终审查修复与可移植验收边界

I1：完整成交释放 active reservation 后，证明归属的后到开仓费先扣实际现金、累计原币费用 basis，再从成功应用的首次 native 回执重建原始预算与累计花费。超原预算冻结 `RESERVATION_OVERRUN`，下一 intent 与 inline 费用同样拒绝。原生回执重传不重复记账；已付现金不重新预留；未解决回执不重试；不同费用钱包不暗中汇率转换；复用订单号造成预算归属不唯一时保留费用并冻结。

I2：`tests/options/test_panel.py` 中的确定性原始 fixture 代码已跟踪，临时 raw/seal 字节独立构造，不依赖工作站资料；这部分为必跑的 portable 覆盖。唯一真实封存集成 `test_sealed_input_integration` 使用两个固定本地根目录（相对于本仓库），不支持自动下载、搬移、复制或重新封存：

| 固定根目录 | seal.json SHA-256 |
| --- | --- |
| `results/crypto_opportunity/20261009_options_liquidity_panel_v1` | `b61d04c898cd53545e2bc95ffdede6da6861b18d602d49cb83d57eb52816423b` |
| `results/crypto_opportunity/20261009_options_venue_scout_v1` | `c35edb8ad23c8d958fe8f2383786f963160842c55ea8c3489e36efb96d25b983` |

普通 `uv run pytest tests/options/ -q` 仅允许对整个根目录不存在的情况显式 `LOCAL_OPTIONS_PANEL_ABSENT` skip，skip 明确表示未完成 source verification。每个存在的根目录先执行 pin、manifest 全部原始 bytes/SHA 校验；缺 seal、缺文件、损坏字节、断链等仍失败，即使另一个根目录完全不存在也不降为 skip。以下 pytest-only 严格命令要求两个根目录均存在并校验通过，任何缺失或损坏均失败；未添加产品 CLI 参数：

```sh
uv run pytest tests/options/test_panel.py::test_sealed_input_integration --require-local-options-panel -q
```

M1：本计划仅保留与 canonical handoff 一致的 1–22 项裁决，各只编号一次。早期 Task 8 报告的“all15”不再表示最终裁决数。额外 argv/发布说明不占裁决编号。最终 reviewer 的 14 项 declined-to-judge 已按裁决 22 逐项接受批准 P1 的边界，明细随 `final-scope-adjudications.md` 归档；I1 预算一致性始终属于 P1，未借 full order lifecycle 范围外将其豁免。

交付证据归档位置为 `results/options_platform/p1_delivery_evidence_20261009/`（包括 final-review、final-fix-report、canonical rulings、scope adjudications 和局部验证日志）；源 panel/scout corpus 保留原处且不修改。当前仅汇总修复完成、等待一次 scoped 复审；全仓集成仍 BLOCKED，旧全仓记录仍为失败且非冻结的历史诊断，不得解释为当前 HEAD 验收。未再次运行全 harness、重写 golden、merge、push、部署、下单或晋级。


本次修复代码与测试提交后，仅运行一次覆盖新 P1 的 `uv run pytest tests/options/ -q`：**428 passed in 2.49s**；局部范围覆盖 ledger/risk/replay 的传播影响。真实源严格命令另为 **1 passed in 1.69s**；scoped `ruff check` 通过、`ruff format --check` 显示 **23 files already formatted**。I1 的修复前 focused RED 为 7 failed / 154 passed，修复后同命令为 161 passed；I2 portable 原始 fixture/可用性回归为 45 passed / 1 deselected（仅未在该命令重复真实源集成）。这些是当前修复的实际运行数，不覆盖旧 full harness 失败。

当前身份产物（原 r2 全部三文件保持逐字节不变）：

```sh
uv run python scripts/analyze_options_portfolio.py --panel-root results/crypto_opportunity/20261009_options_liquidity_panel_v1 --output-dir results/options_platform/p1_1000_r3 --capital 1000 --scenario long-call
uv run python scripts/analyze_options_portfolio.py --output-dir results/options_platform/p1_3000_r3 --capital 3000 --scenario straddle
```

两条命令均退出 0。本轮逐文件核验 `report.json / REPORT.md` bytes/SHA 及 identity 中 9 个代码 hash；相对于 r2，仅 ledger 源身份更新，fixture、policy、panel data/source references 不变。当前 ledger SHA-256 为 `0213aaab6afd44b72a744217560758f453f8e5b32076144cb5de7f3ee6f82493`。panel/scout 的原 510+31 条 bytes/SHA 及固定 pins 一致，105 个受保护既有文件哈希全部一致。

| 当前产物目录 | report.json bytes | report.json SHA-256 | identity.json SHA-256 |
| --- | ---: | --- | --- |
| `results/options_platform/p1_1000_r3` | 8933612 | `48f7b485596da954d53c43a05757d5c3229d8a7538a01bae6c42887e97f9843a` | `c8ffcf6b19507cd651f5353001ff201d0a4313117a6954ccc6602a135242bfa3` |
| `results/options_platform/p1_3000_r3` | 16588 | `536d98e8f5b62bccae30e807daef0267091cab1a3bf99d2e58d7c469f7de36f3` | `0dd127a42e4be8e5d1b1a63524f6eafa5febd4d363908dec04c87d10d8969cee` |
