# D2 通用只读期权工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在单 Mac 上交付两个只读入口：冻结观点后的长期权表达比较，以及同所 vertical 现金流约束诊断；保留旧 P1 接口和结果，给以后数据源与模型留明确扩展点。

**Architecture:** 新合同包装现有 P1，纯计算模块不读文件或账户。一个具体封存数据适配器向内输出 typed input，独立 CLI 输出新 schema 的决策单。D2 没有真实预留、订单或 paper 成交。

**Tech Stack:** Python >=3.11、stdlib frozen dataclasses/Decimal/JSON/hashlib、现有 P1；沿用 uv/pytest/ruff，不增加依赖。

**Spec:** [已批准的工作台规格](../specs/2026-10-09-options-trading-workbench-design.md)，实施范围为 D2；[P2 数据规格](../specs/2026-10-09-options-data-p2-design.md)是未来输入子系统，不在本包实现。

起草：2026-10-09；审查交付：2026-10-10。规划基线：`87b7bc184894dc6919d0f3b97856a2bb3919ec7f`。状态：完整计划待审阅，尚未实施。用户已选择 subagent 执行方式；此轮使用 subagents 拆解合同、扩展与验收，不提前写产品代码。

## Global Constraints

- 产品支持多资产、多场所期权；BTC 是第一机制验证对象，ETH 是冻结条件后的复制对象。
- 当前生产仍为 `4h`；方向意图遵守显式 confirmed-bar policy。本包观点记录和模型草案不生成可执行方向意图。
- 核心无网络、数据库、隐含墙钟或非固定随机数；现金与数量使用 Decimal。
- 所有 D2 结果 `diagnostic_only=true / rankable=false / live_eligible=false / execution_authority=NONE`。
- `research` 与 `decision_support` 无真实预留和订单权限；D2 不提供切换 paper/live 的参数。
- 复用原 P1 `contracts/costs/payoffs/ledger/risk`，不修改其 dataclasses、枚举、canonical/fingerprint、exports、旧 CLI 或报告模块。
- `tests/options/test_boundaries.py` 的机械边界不放宽；旧 `zenquant.app` 不导入新核心，CLI 放 `scripts/`。
- 不覆盖旧规格、封存输入或报告；无概率不出 EV，缺证不写零，保护效用不称盈利。
- 同一比较只有一份总资本；不同表达是互斥参考账户，模型资金不等于真实余额。
- 既有 A/C/E 阻断保留；不得为本包重生成 golden、修旧策略或声称合并/实盘通过。

以下是本包新增的工程限额，需进入新输入 identity：单 request 文件最大 1 MiB；最多 128 条观点版本、64 个候选、64 个情景、512 个 quote。超限在输出创建前拒绝。它们限制本地只读任务规模，不是经济阈值。

## Review Focus

1. 输入混入未来 schema、重复 JSON key、float/NaN 或权限字段：明确拒绝，不产生部分报告，不回显不可信值。
2. 观点修订、metadata、费用或报价晚于 cutoff：旧版本仍可重建；晚到内容不能回写历史。
3. 两个 venue 使用同 instrument ID，或某轮有冲突 book：复合身份隔离，不串绑费用/结算，不静默选赢家。
4. 数量、费用、FX、到期费用或深度缺证：保留原生金额和原因，未知合计/净收益为 null，不能通过预算。
5. 模型 VALID、预算拒绝、未支持模板或场景期限不符：保存候选及 NO_TRADE 原因，账户/执行仍未验证，actual PnL/Sharpe 保持 null。

---

## 1. 本包边界与扩展方式

### 1.1 可交付能力

- 严格读取人工观点/修订、终值情景与候选，保留原 bytes/hash 和来源。人工提供的时间是未经外部认证的声明，不冒充前瞻登记证明。
- 四种已支持 long 模板和同币种 cash 的到期模型；显示入场成本、合法量、模型预算、终值假设结果及条件 EV。
- 同所、同到期、同 fixing 的 call/put vertical 分段 payoff 证书；含卖腿仍只作数学诊断。
- 封存旧 panel 的报价成本诊断。旧 panel 的 `exercise_style=UNKNOWN`、结算/账户缺证和 proxy 全保留；因此它不产生真实终值模型或账户通过结论。
- 新 JSON/Markdown 决策单与按知识截止筛选的观点时间线；CLI 成功也包括有理由的 NO_TRADE/NOT_EVALUABLE。

### 1.2 三个窄扩展点

| 扩展点 | D2 实现 | 以后怎样增加 |
| --- | --- | --- |
| 输入 | 具体 `SealedPanelAdapter`；测试/演示的 typed fixture | P2 corpus 增加独立 adapter，保持 QuoteInput 语义和来源，不让核心识别供应商文件 |
| 表达 | `terminal_paid_long.v1` 与 `cash_no_interest.v1` | 新模板/模型版本显式登记，独立实现及验收；不动态 import 用户给的 Python 路径 |
| 诊断 | `linear_vertical_payoff.v1` | box/parity/跨所/统计模型各自新证明域与协议；不复用 vertical 标签偷扩能力 |

资产来自 InstrumentSpec，数量来自 step/min/multiplier，不硬编码 BTC 或 0.01。模板名允许描述未来结构，但没有匹配模型时返回 UNSUPPORTED，保留原候选，不降级成 long call。

旧 P1 永远由旧入口解释。D2 采用显式 schema allowlist，未知版本/字段拒绝；未来 v2 要有新 codec 和版本化迁移投影，保留旧原字节与旧 reader。读懂旧记录不等于补出不存在的历史规则、账户或成交证据。

### 1.3 不属于本包

D1 网络采集、IV/时间路径估值、提前退出定价、概率校准、真实账户/费用 tier、OMS、原子资金预留、paper/live、卖腿/合约核算、native combo 与跨所执行均另包。D2 保存失败原因和版本接口，不创建空执行服务或未来 adapter。

## 2. 文件与任务依赖

| 拟新增文件 | 职责 | 任务 |
| --- | --- | --- |
| `zenquant/options/workbench_contracts.py` | 不可变 request、观点、情景、复合身份与结果 | 1 |
| `zenquant/options/expressions.py` | 精确支持检查、成本、long/cash 终值模型与条件 EV | 3 |
| `zenquant/options/constraints.py` | 同所 vertical 全域数学证明与条件成本诊断 | 4 |
| `zenquant/research/options_platform/workbench_inputs.py` | 严格新 schema、观点版本筛选、具体 panel adapter | 2 |
| `zenquant/research/options_platform/workbench_reporting.py` | 决策单、时间线、新 schema 与身份 | 5 |
| `scripts/analyze_options_workbench.py` | 独立 CLI、路径隔离与原子 no-replace 发布 | 6 |
| `tests/options/workbench_factories.py` | 固定、可手算 fixture；只在 tests 使用 | 1 |
| `tests/options/test_workbench_{contracts,inputs,expressions,constraints,reporting,cli}.py` | 各任务行为验收 | 1–6 |
| `docs/OPTIONS_WORKBENCH_GUIDE.md` | 新入口使用、修改点和结果边界 | 6 |

不新增 registry 框架；D2 的精确模板表与解析函数放 expressions。`1 → 2/3/4 → 5 → 6`；任务 2/3/4 在合同冻结后可并行，6 在整合后执行。每个任务使用新实现 agent 与审查 agent，不允许同时编辑同一文件。

## 3. 公共合同冻结

下列都是拟新增类型，不修改 P1。所有 dataclasses 为 frozen/slots/kw_only，所有容器为 tuple；金额拒绝 float、bool 和非有限值，时间只收 strict int。

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from zenquant.options.contracts import Book, CombinationIntent, Observation, StatusResult, WalletKey
from zenquant.options.costs import CostEvidence, FeePolicy, TimingPolicy
from zenquant.options.ledger import WalletBalance
from zenquant.options.risk import RiskApproval, RiskPolicy

@dataclass(frozen=True, slots=True, kw_only=True)
class RecordMeta:
    object_id: str
    revision: int
    parent_hash: str | None
    schema_version: str
    method_version: str
    received_ns: int
    available_ns: int
    cutoff_ns: int
    source_ref: str
    source_sha256: str | None
    provenance: Literal["fixture", "manual_unattested", "sealed_proxy", "derived"]

@dataclass(frozen=True, slots=True, kw_only=True)
class View:
    meta: RecordMeta
    underlying: str
    mechanism: str
    direction: Literal["bullish", "bearish", "neutral", "volatility", "unspecified"]
    horizon_ns: int
    report_currency: str
    initial_capital: Decimal
    max_loss_budget: Decimal
    competing_explanations: tuple[str, ...]
    invalidation: tuple[str, ...]
    no_trade_conditions: tuple[str, ...]
    clock_policy: str

@dataclass(frozen=True, slots=True, kw_only=True)
class ScenarioPoint:
    at_ns: int
    delivery_by_spec_hash: tuple[tuple[str, Decimal], ...]
    iv_shift: Decimal | None
    liquidity_haircut: Decimal | None

@dataclass(frozen=True, slots=True, kw_only=True)
class Scenario:
    scenario_id: str
    points: tuple[ScenarioPoint, ...]
    probability: Decimal | None

@dataclass(frozen=True, slots=True, kw_only=True)
class ScenarioSet:
    meta: RecordMeta
    view_hash: str
    purpose: Literal["forecast", "stress"]
    probability_source: Literal["subjective", "risk_neutral", "empirical"] | None
    scenarios: tuple[Scenario, ...]

@dataclass(frozen=True, slots=True, kw_only=True)
class InstrumentDescriptor:
    venue: str
    product: str
    instrument_id: str
    underlying: str
    rule_version: str
    spec_schema: str
    spec_hash: str

@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateLeg:
    instrument: InstrumentDescriptor
    side: Literal["BUY", "SELL"]
    quantity_native: Decimal

@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateRequest:
    request_id: str
    template_id: str
    template_version: str
    legs: tuple[CandidateLeg, ...]
    exit_policy: str
    terminal_fee_native: Decimal | None
    sleeve: Literal["directional", "volatility", "tail"]

@dataclass(frozen=True, slots=True, kw_only=True)
class QuoteInput:
    quote_id: str
    book: Book
    index_in_quote: Observation | None
    index_currency: str | None
    quote_to_report: Observation | None
    fx_pair: tuple[str, str] | None
    fee_policy: FeePolicy | None
    fee_known_ns: int | None
    metadata_known_ns: int | None
    timing: TimingPolicy
    source_identities: tuple[tuple[str, str], ...]
    assumptions: tuple[str, ...]

@dataclass(frozen=True, slots=True, kw_only=True)
class AnalysisInput:
    schema_version: str
    adapter_version: str
    cutoff_ns: int
    view: View
    view_history: tuple[View, ...]
    scenarios: ScenarioSet
    requests: tuple[CandidateRequest, ...]
    quotes: tuple[QuoteInput, ...]
    hypothetical_balances: tuple[WalletBalance, ...]
    risk_policy: RiskPolicy
    source_identities: tuple[tuple[str, str], ...]
    gaps: tuple[StatusResult, ...]

@dataclass(frozen=True, slots=True, kw_only=True)
class SupportColumns:
    input_validity: StatusResult
    model_support: StatusResult
    modeled_feasibility: StatusResult
    account_eligibility: StatusResult
    execution_evidence: StatusResult

@dataclass(frozen=True, slots=True, kw_only=True)
class ScenarioOutcome:
    scenario_id: str
    currency: str
    gross_payoff: Decimal | None
    conditional_model_pnl: Decimal | None
    reason: str

@dataclass(frozen=True, slots=True, kw_only=True)
class ExpressionAnalysis:
    request_id: str
    request_hash: str
    input_hash: str
    model_version: str
    support: SupportColumns
    p1_intent: CombinationIntent | None
    costs: tuple[CostEvidence, ...]
    model_funding: RiskApproval | None
    outcomes: tuple[ScenarioOutcome, ...]
    conditional_ev: Decimal | None
    break_even_levels: tuple[Decimal, ...] | None
    conditional_model_loss_bound: Decimal | None
    reasons: tuple[str, ...]

@dataclass(frozen=True, slots=True, kw_only=True)
class ConstraintCertificate:
    request_id: str
    input_hash: str
    method_version: str
    status: StatusResult
    spec_hashes: tuple[str, ...]
    currency: str | None
    knots: tuple[Decimal, ...]
    values_at_knots: tuple[Decimal, ...]
    segment_slopes: tuple[Decimal, ...]
    gross_payoff_lower: Decimal | None
    gross_payoff_upper: Decimal | None
    opening_net_spend: Decimal | None
    conditional_model_lower: Decimal | None
    conditional_model_upper: Decimal | None
    reasons: tuple[str, ...]
```

语义检查必须实现，不能认为类型注解就是验证：

- meta revision 从 1 开始，v1 parent=null，其余 parent 必须匹配历史上一版本；`received ≤ available ≤ cutoff`；horizon > 观点 cutoff。source SHA 若存在必须是 64 位小写 hex，sealed 来源必填。View/ScenarioSet 的 schema 分别为 `options-workbench.view.v1` / `options-workbench.scenarios.v1`，不是任意字符串。
- 观点金额有限且非负，max_loss_budget ≤ initial_capital；不提供实盘预算默认值。`clock_policy` 保存研究声明，D2 不据此授权方向开仓。
- Scenario ID、quote ID、request ID 唯一；prices 的 spec hash 不重复，价格 ≥0。points 时间严格递增且不早于 cutoff；概率全有或全无，范围 [0,1]，有则精确合计 1；stress 禁止权重。
- descriptor 的全部字段与实际 spec/fingerprint 精确绑定；`spec_schema=p1.instrument.v1` 是新 envelope 对 P1 表示的标签，不写回旧 InstrumentSpec。
- 同 spec 多条历史 quote 不丢失；按固定 cutoff 选最后可用版本，同可用时间而内容冲突时 INVALID，不选更便宜者。记录被排除的 late/conflict 原因。
- AnalysisInput 与 ScenarioSet 的 view_hash 一致；引用不匹配 INVALID。完整 typed view_history 由 loader 保留，selected view 必须等于该历史在 cutoff 下选出的对象，不由报告再解析原 JSON。分析 cutoff 独立于观点登记 cutoff：计算不得使用 available/meta cutoff 晚于该分析时点的观点/情景，不能改原观点来适配新 cutoff；晚到 history 只进入 excluded 时间线。horizon 已过时保留候选并给 EXPIRED_VIEW。手工 JSON 不允许填写 support、intent、approval、权限或已实现收益。
- index_currency 必须为 book 的 quote_currency；FX pair 必须为 `(quote_currency, report_currency)`，缺失币种证明即缺证，不能只检查数值。已有 Observation/P1 对象不加字段，新单位信息仅在 envelope。

## 4. 最小函数合同

| 所属模块 | 拟新增签名 |
| --- | --- |
| workbench_inputs | `decode_request(data: bytes) -> dict[str, object]`：严格 schema 后的传输对象，后续立即转 typed 合同 |
| workbench_inputs | `select_view(views: tuple[View, ...], *, view_id: str, cutoff_ns: int) -> View` |
| workbench_inputs | `quotes_from_panel(panel: PanelInput, *, cutoff_ns: int) -> tuple[QuoteInput, ...]` |
| workbench_inputs | `load_analysis(data: bytes, *, panel: PanelInput | None, fixture: AnalysisInput | None) -> AnalysisInput` |
| expressions | `analyze_expression(*, input: AnalysisInput, request: CandidateRequest) -> ExpressionAnalysis` |
| expressions | `compare_expressions(*, input: AnalysisInput) -> tuple[ExpressionAnalysis, ...]` |
| constraints | `certify_vertical(*, input: AnalysisInput, request: CandidateRequest) -> ConstraintCertificate` |
| constraints | `diagnose_verticals(*, input: AnalysisInput) -> tuple[ConstraintCertificate, ...]` |
| workbench_reporting | `build_report(*, input: AnalysisInput, expressions: tuple[ExpressionAnalysis, ...], constraints: tuple[ConstraintCertificate, ...]) -> dict[str, object]` |
| workbench_reporting | `render_markdown(report: dict[str, object]) -> str` |
| scripts CLI | `main(argv: list[str] | None = None) -> int`；`_publish(source: Path, destination: Path) -> None` |

`SealedPanelAdapter.to_quotes(*, cutoff_ns: int) -> tuple[QuoteInput, ...]` 包装已验证 PanelInput，只有这一种具体文件 adapter。函数 load_analysis 的 fixture 参数仅用于测试/显式演示；CLI 不接受 Python import 路径或 SDK 对象。

所有 hash 调用现有 `fingerprint/canonical_payload`，新 envelope 有独立 schema/version；hash 绑定原 source、adapter、规则、模型、policy 和 cutoff，不改原 digest。source bytes SHA 与新 canonical hash 分列。

## 5. 固定 fixture 与 oracle

Task 1 新增 `workbench_case(*, template: str="long_call", underlying: str="ETH", budget: str="10", weighted: bool=False) -> AnalysisInput`、`vertical_case(*, option_type: str="C", mismatched_fixing: bool=False) -> AnalysisInput`。辅助 `workbench_views() -> tuple[View, ...]` 返回 v1/v2 可见性反例。fixture 原始构造用现有 `linear_spec/book_for/observation/wallet`，不能调用待测表达函数产生 expected。

- 单币 USDC；K=100，expiry=1000，分析/观点 cutoff=10；source/received/available=10，metadata/fee known=0；index_currency=USDC、fx_pair=(USDC,USDC)。规则为 fixture 的 linear_cash/european，固定窗口，不声称现实 ETH 价格。
- min/step=.01、q=.01、multiplier=1；call ask=2、bid=1.9，put ask=1.5、bid=1.4，显示 depth=1；FeePolicy rate=.0003、cap=.125、fee_currency=USDC、provenance=fixture，index=100、同币 FX=1。
- 单 call 入场 .0203；S=90/100/110 时 gross=0/0/.1、conditional model PnL=-.0203/-.0203/.0797；break-even=102.03，loss bound=.0203。terminal fee 显式 fixture 0；删除该假设时净 PnL/EV/loss bound 缺证，不补零。
- straddle 总支出 .0356；S=90/100/110 的 payoff=.1/0/.1、PnL=.0644/-.0356/.0644；break-even=96.44/103.56。两腿必须共同预算，不各拿 10。
- subjective 权重 .25/.5/.25 时 call 条件 EV=.0047，straddle=.0144；无概率/risk-neutral/stress 时 EV=null。它们是 fixture 条件模型值，不是策略收益率。
- 预算 .0203 时单 call 模型通过，.020299 拒绝；min .01，.009/.015 拒绝。q=.1、asks=(2,.04),(3,.06) 时 premium=.26、fee=.003、total=.263；深度只有 .09 时拒绝补量。
- 模型资金 1,000 USDC 分配到唯一 fixture 钱包，RiskPolicy 明示 per-intent=10、active+pending=50、rolling-tail=20、window=30d；3,000 情景是独立模型账户，限额按同实验 profile 放大。不得把此 fixture 改成用户实盘默认。
- 单 put K100 入场 .0153，S=90/100/110 的 conditional PnL=.0847/-.0153/-.0153，break-even=98.47。strangle fixture 使用 put K90/call K110、相同 base 量及上述 ask/fee，总支出 .0356；S=80/100/120 的 PnL=.0644/-.0356/.0644，break-even=86.44/113.56。cash 在所有情景为明确模型 0。按同币种 q 和总 paid cost 检查到期根，负价格域的根不展示。
- workbench_views 的 v1/v2 使用同 object_id：v1 received=available=cutoff=100、revision1/parent=null；v2 received=available=cutoff=120、revision2/parent=fingerprint(v1)，均 horizon=1000、provenance=manual_unattested，其余字段与 §5 观点一致。这只验证知识截止，不称外部认证登记。
- vertical fixture：long K100 call / short K110 call，q=.01；gross 边界 [0,.1]，knots=(0,100,110)，values=(0,0,.1)，segment slopes=(0,.01,0)。call ask=4、high-call bid=5，各费用 .0003，opening net spend=-.0094；terminal fee fixture0 时 conditional model bound=[.0094,.1094]，仍 SHORT_EXECUTION_UNSUPPORTED。完整生命周期边际在报告中始终 null。

假设资金/成本都标 fixture/manual，不能生成真实 settlement/fill/账户事件。所有数值在测试里直接写 Decimal 常量。

## Task 1: 不可变合同与固定 fixture

**Files:** Create `workbench_contracts.py`、`tests/options/workbench_factories.py`、`test_workbench_contracts.py`。不改 P1 文件。

**Interfaces:** 产出 §3 全部类型和 §5 三个 factory；消费 P1 值类型、现有 tests factory。

- [ ] **Step 1: 写行为反例与手算输入。** 至少包括下面测试，并实现 factory 的明确 fixture 数据；测试先引用尚不存在的新合同：

```python
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import pytest
from tests.options.workbench_factories import workbench_case

def test_view_is_immutable_and_budget_is_not_float():
    case = workbench_case()
    with pytest.raises(FrozenInstanceError):
        case.view.max_loss_budget = Decimal("11")
    with pytest.raises(TypeError):
        replace(case.view, max_loss_budget=0.1)

def test_references_and_future_meta_are_not_silently_repaired():
    case = workbench_case()
    with pytest.raises(ValueError):
        replace(case.view.meta, available_ns=11)
    with pytest.raises(ValueError):
        replace(case, scenarios=replace(case.scenarios, view_hash="0" * 64))
```

- [ ] **Step 2: 验证红灯。** `uv run python -m pytest tests/options/test_workbench_contracts.py -q`；确认缺失新模块/行为而失败，不把偶发环境错误当红灯。
- [ ] **Step 3: 实现 §3 合同的完整验证。** 拒绝可变容器、重复身份、错误概率、bool 时间、future meta 和不匹配引用；unsupported template 字符串可描述但不授权。不增加可变缓存或隐含时间。
- [ ] **Step 4: 验证绿灯。** 同命令；增加 revision parent、stress 有概率、负/非有限值及金额零值与缺失值区分。切换 Decimal context 后 fingerprint 相同。
- [ ] **Step 5: 审查并单用途提交。** 实现和测试尽量分为 `feat(options): add immutable workbench contracts`、`test(options): pin workbench input contracts`；每次 staged paths 只包含本任务文件。

## Task 2: 严格输入、历史观点与封存 panel adapter

**Files:** Create `workbench_inputs.py`、`test_workbench_inputs.py`。消费 Task 1 合同和现有 `load_panel/input_integrity`；不修改 panel/reporting。

**Interfaces:** 实现 §4 的 decode_request/select_view/quotes_from_panel/load_analysis 和 SealedPanelAdapter；传输层字典不得进入纯计算。

- [ ] **Step 1: 写严格解析及可见性反例。** schema 仅 `options-workbench-request.v1`；固定字段为 schema、adapter_version、view_id、cutoff_ns、views、scenario_set、requests、hypothetical_balances、risk_policy、source_identities、fixture_name。quote 来源只允许外部已验证 panel 或显式 fixture，不能夹带账户权限。scenario_set 须匹配选中观点且在 cutoff 前可用，缺失时拒绝，不借用后来版本。

```python
import pytest
from zenquant.options.contracts import fingerprint
from zenquant.research.options_platform.workbench_inputs import decode_request, select_view
from tests.options.workbench_factories import workbench_views

@pytest.mark.parametrize("raw", [
    b'{"schema":"options-workbench-request.v99"}',
    b'{"schema":"x","schema":"y"}',
    b'{"initial_capital":NaN}',
])
def test_invalid_json_schema_is_rejected(raw):
    with pytest.raises(ValueError):
        decode_request(raw)

def test_late_revision_does_not_replace_earlier_view():
    v1, v2 = workbench_views()
    old_hash = fingerprint(v1)
    assert select_view((v1, v2), view_id=v1.meta.object_id, cutoff_ns=100) == v1
    assert select_view((v1, v2), view_id=v1.meta.object_id, cutoff_ns=120) == v2
    assert fingerprint(v1) == old_hash
```

- [ ] **Step 2: 运行红灯。** `uv run python -m pytest tests/options/test_workbench_inputs.py -q`。
- [ ] **Step 3: 实现 parser、完整版本链和 adapter。** json 使用 object_pairs_hook 拒绝重复字段、parse_float/parse_constant 拒绝 float/非有限常量；不认识的字段或模式明确拒绝。检查每条 revision,parent、源哈希及时间；selected 只来自 available≤cutoff 的最高合法 revision，晚到项保留为排除原因。
- [ ] **Step 4: adapter 逐 record 映射。** quote_id 绑定 round/cell/family/request hash；所有记录和原时点保留，由核心按分析 cutoff 决定可见性，不只保留最便宜/最后一轮。metadata_known_ns 保存旧 loader 的知识界并注明不是生效日期。费用只来自冻结 protocol 的 taker_fee_rate_proxy/premium_fee_caps，provenance=proxy；费用 known time 没有证明时为 null，不能将其当 cutoff 前 observed 费率。index_currency/FX pair 必须有明确单位依据，BTC/BTC 与同币 FX 只可明示派生 identity；不得拿其他 record 的 index 或 USD/USDC parity 补缺。load_analysis 在 source_identities 记录真实 request bytes SHA，不回写已有 View 元数据而破坏 parent hash。
- [ ] **Step 5: 覆盖 integrity 与未来输入绿灯。** 原 input_integrity 不 `PINNED_BYTES_VERIFIED` 时拒绝；旧原字节/assumptions/gaps 保存。load_analysis 保留全部 typed views 到 AnalysisInput.view_history；CLI 将这个同一 input 交给 build_report，增加从 loader 到报告的 v1/v2 可见/排除时间线集成测试。晚到 metadata/fee/index/FX 必须显式缺证，不把 old −1s proxy 改名同步行情。重复同 ID 异 venue 分开，同身份同时间冲突拒绝。增加 file-size/count 边界及 manual_unattested 时间声明测试。
- [ ] **Step 6: 提交。** `feat(research): adapt sealed inputs for options workbench` 和独立 input tests；只能更改本任务新增文件。

## Task 3: 四 long/cash 表达比较与条件 EV

**Files:** Create `expressions.py`、`test_workbench_expressions.py`。消费 Task 1；无需等待文件 adapter。

**Interfaces:** analyze_expression/compare_expressions 返回 ExpressionAnalysis；精确模板表为 `long_call/long_put/long_straddle/long_strangle` version=`v1`、model=`terminal_paid_long.v1`；cash=`cash_no_interest.v1`。

- [ ] **Step 1: 写已知答案与预算拒绝。**

```python
from dataclasses import replace
from decimal import Decimal as D
from zenquant.options.expressions import compare_expressions
from tests.options.workbench_factories import workbench_case

def test_long_call_oracle_and_subjective_ev():
    case = workbench_case(weighted=True)
    row = compare_expressions(input=case)[0]
    assert tuple(x.conditional_model_pnl for x in row.outcomes) == (D("-.0203"), D("-.0203"), D(".0797"))
    assert row.conditional_ev == D(".0047")
    assert row.break_even_levels == (D("102.03"),)
    assert row.support.account_eligibility.status == "NOT_EVALUABLE"
    assert row.model_funding.execution_authority == "NONE"

def test_missing_terminal_fee_is_not_zero():
    case = workbench_case()
    request = replace(case.requests[0], terminal_fee_native=None)
    row = compare_expressions(input=replace(case, requests=(request,)))[0]
    assert row.outcomes[2].gross_payoff == D(".1")
    assert all(x.conditional_model_pnl is None for x in row.outcomes)
    assert row.conditional_ev is row.conditional_model_loss_bound is None

def test_budget_refusal_is_preserved():
    case = workbench_case(budget=".020299")
    row = compare_expressions(input=case)[0]
    assert row.support.modeled_feasibility.status == "REJECTED"
    assert row.request_id == case.requests[0].request_id
```

- [ ] **Step 2: 红灯。** `uv run python -m pytest tests/options/test_workbench_expressions.py -q`。
- [ ] **Step 3: 精确解析候选。** descriptor 匹配 venue/product/id/underlying/rule/schema/hash；查该 spec 在 cutoff 最后可用 quote。未来/过期/冲突产生各自原因。纯未知模板、SELL、inverse 或跨所组合保持原请求但 p1_intent=null；不调用 long 风险路径。
- [ ] **Step 4: 计算成本及模型资金。** 调现有 `validate_quantity/quote_cost`，逐腿绑定成本的 instrument hash、数量、币种和分析 cutoff。合法 long 构造仅模型用途 P1 intent，intent cutoff 为分析 cutoff、valid_until 为到期；不形成已确认方向开仓权限。effective RiskPolicy 的 per-intent=min(原 policy,view budget)，新 hash 入 intent。hypothetical_balances 单币合计必须等于唯一 initial_capital，缺钱包为缺证。initial_state 只登记本候选无歧义 specs，不能将跨 venue 同 native ID 的全集放进 P1。`approve_intent` 只生成模型所需资金，绝不 apply reservation/fill。
- [ ] **Step 5: 计算严格终值。** 四模板检查相同 venue/underlying/expiry/fixing/window/币种、equal base qty；straddle 同 strike，strangle put strike<call strike。同一 fixing 域的全部腿在每个情景必须有且只有同一个 Decimal 终值，完整覆盖候选 spec hashes；缺项 NOT_EVALUABLE，给 call110/put90 这类同域不一致价格返回 INVALID/SCENARIO_FIXING_CONFLICT，全部净值/EV为空。P1 expiry_payoff 不校验这个一致性，由本层先验。通过后才将 spec_hash 映射到该无歧义组合 native ID，调用 P1 `expiry_payoff`；不改 native ID。horizon/point 时间必须等于到期，恰好单点，iv_shift/liquidity_haircut 必须为空。否则净值 NOT_EVALUABLE，并保留可独立计算的成本。
- [ ] **Step 6: 费用和概率。** terminal_fee_native 是明确的 fixture/manual 条件假设，只支持与结算/report 同币种。净值=payoff−完整入场支出−该费用；费用未知净值 null。P1 model_funding 只检查其原始入场口径，wrapper 还需检查入场支出+terminal reserve 同时不超过 effective_policy.per_intent_report、effective_policy.active_and_pending_report、对应模型钱包现金；tail sleeve 另不超过 effective_policy.rolling_tail_paid_report。该 reference account 没有旧库存，不能把现金与风险帽当一回事。超限 modeled_feasibility=REJECTED，费用未知 modeled_feasibility=NOT_EVALUABLE，不能拿内层 VALID 覆盖。添加 budget=.0203、terminal fee=.001 的拒绝测试，及 opening P1 通过但增加terminal reserve超 global/tail帽的独立反例。无缺证且 forecast/subjective 权重完整时，固定 Context(prec=50,ROUND_HALF_EVEN) 求和；无概率、stress、risk-neutral/empirical 的 EV 为 null 并带原因。cash 明确零息、不交易模型零值，actual PnL 仍 null。
- [ ] **Step 7: 扩展绿灯与提交。** 同名 instrument 跨所不串用；contracts multiplier=10 时 `.01 base`不得误当 `.01 contracts`；非法量、深度、缺 fee/FX、早于到期、未知模板都保存结果。用固定 oracle 检查 put/straddle/strangle/cash 与不同 Decimal context。提交独立表达模块与测试，不改 P1。

## Task 4: 同所 vertical 全域证书

**Files:** Create `constraints.py`、`test_workbench_constraints.py`。消费 Task 1，调用 P1 intrinsic_value/quote_cost；不依赖 Task 3 内部私有函数。

**Interfaces:** certify_vertical/diagnose_verticals；只处理请求 template=`call_vertical/put_vertical` version=`v1`，method=`linear_vertical_payoff.v1`。扫描固定请求集合，不扩大 quote universe。

- [ ] **Step 1: 写节点/尾部 oracle 和 fixing 反例。** 使用 Task1 已冻结的 vertical_case，按 §5 原值；Task3/4 并行时均不编辑共享 factory。

```python
from decimal import Decimal as D
from zenquant.options.constraints import diagnose_verticals
from tests.options.workbench_factories import vertical_case

def test_vertical_certificate_covers_knots_and_right_tail():
    c = diagnose_verticals(input=vertical_case())[0]
    assert c.knots == (D("0"), D("100"), D("110"))
    assert c.values_at_knots == (D("0"), D("0"), D(".1"))
    assert c.segment_slopes == (D("0"), D(".01"), D("0"))
    assert (c.gross_payoff_lower, c.gross_payoff_upper) == (D("0"), D(".1"))
    assert c.opening_net_spend == D("-.0094")
    assert c.conditional_model_lower == D(".0094")
    assert "SHORT_EXECUTION_UNSUPPORTED" in c.reasons

def test_different_fixing_has_no_certificate():
    c = diagnose_verticals(input=vertical_case(mismatched_fixing=True))[0]
    assert c.status.status == "NOT_EVALUABLE"
    assert c.gross_payoff_lower is c.gross_payoff_upper is None
```

- [ ] **Step 2: 红灯。** `uv run python -m pytest tests/options/test_workbench_constraints.py -q`。
- [ ] **Step 3: 实现证明域与数量校验。** 两 leg 同 venue、underlying、linear/european、expiry、fixing/window、settlement rule 与全部相关币种，规则有效且非 UNKNOWN。call 买低 strike/卖高 strike；put 买高 strike/卖低 strike；base 数量相等且合法。缺腿、unknown index、不同域/版本、inverse 返回原因，不借邻近 strike 或 perp 补腿。
- [ ] **Step 4: 节点与斜率证明。** 节点为 0 与所有 strike，区间按该方向 signed intrinsic 的解析斜率；核验最右斜率为0，再取节点 min/max。put fixture 的节点值应为(.1,.1,0)，slopes=(0,-.01,0)；它不能套 call 方向。有限采样网格不是证书。
- [ ] **Step 5: 条件成本。** opening_net_spend=BUY total−SELL net proceeds；输入不足时保持数学边界，cost fields null。terminal fee 明确时 conditional bounds=gross bounds−opening_net_spend−terminal fee；未知则 null。完整 lifecycle edge 永不由这个证书产生，不生成 short CombinationIntent/approve/fill；report 必须保留融资/账户/执行缺证。
- [ ] **Step 6: 绿灯/提交。** 检查同名异 venue、metadata sentinel、负/非法比例、付费净边界、未知交割费用和规则有效域。提交 `feat(options): diagnose linear vertical cashflow bounds` 及独立 tests。

## Task 5: 两入口决策单与观点时间线

**Files:** Create `workbench_reporting.py`、`test_workbench_reporting.py`。消费 1–4；不改旧 `reporting.py`，它的全文件 code hash 属于旧结果身份。

**Interfaces:** build_report/render_markdown；新 schema=`options-workbench-report.v1`。JSON 保留全部请求、能力五列、成本来源、outcomes、constraints、visible/excluded views 与 gaps。

- [ ] **Step 1: 写权限与缺证反例。**

```python
from zenquant.options.expressions import compare_expressions
from zenquant.options.constraints import diagnose_verticals
from zenquant.research.options_platform.workbench_reporting import build_report, render_markdown
from tests.options.workbench_factories import workbench_case

def test_math_report_has_no_execution_or_actual_returns():
    case = workbench_case()
    report = build_report(input=case, expressions=compare_expressions(input=case),
                          constraints=diagnose_verticals(input=case))
    assert report["schema"] == "options-workbench-report.v1"
    assert report["execution_authority"] == "NONE"
    assert report["rankable"] is report["live_eligible"] is False
    assert all(report[k] is None for k in ("actual_pnl", "paper_pnl", "sharpe", "capacity"))
    assert "模型" in render_markdown(report)
    assert "未验证" in render_markdown(report)
```

- [ ] **Step 2: 红灯。** `uv run python -m pytest tests/options/test_workbench_reporting.py -q`。
- [ ] **Step 3: 输出固定结构。** 权限由代码常量设定，不拷贝输入字段。三种 edge 分列；此包 displayed/model 值有明确来源时才出现，`full_lifecycle_cashflow_bound=null`。经济/运营证据缺失不从 input VALID 推导账户通过。NO_TRADE 包含 budget/quantity/unsupported/late 原因。
- [ ] **Step 4: 可复现时间线。** 源观点全保留，visible 与 late 不覆盖原对象；记录选择引用的 view/scenario/candidate hashes。持有/退出判断在 D2 只是观点，不能写关闭库存/已实现收益。记录登记覆盖和 manual_unattested 的选择偏差限制。
- [ ] **Step 5: 手算、字节与渲染绿灯。** 同输入输出同 canonical bytes/hash；零 cash 模型与未知期权收益分别测试；改 cutoff、规则、模型或概率改变新 hash。Markdown 用局部纯字符串函数转义 `|`、换行、反引号和 HTML 特殊字符，不引入当前 boundary 禁止的 html 包；用户原话只作文本，不成为路径/代码/HTML。
- [ ] **Step 6: 提交。** 新 schema/report 与独立 tests 单用途提交；旧 CLI 的 key、字段类型及文件源码保持原样。

## Task 6: 独立 CLI、兼容保全和交接

**Files:** Create `scripts/analyze_options_workbench.py`、`test_workbench_cli.py`、`docs/OPTIONS_WORKBENCH_GUIDE.md`；Modify 本包新增的 `tests/options/workbench_factories.py`，补 cli_request_bytes。不修改 pyproject entrypoints、旧 CLI、legacy app 或旧配置。

**Interfaces:** `--request-file PATH`、`--output-dir PATH` 必需；`--panel-root PATH` 可选，与 request 中显式 fixture_name 二选一。无请求默认交易，也无 --live/--account/--exchange/--signal。

- [ ] **Step 1: 写禁止环境/网络与路径反例。** 新 CLI 按 tests/options/test_cli.py 的 importlib 方式加载，但 module name 为 `options_workbench_cli`。`cli_request_bytes(*, fixture_name: str="terminal-eth-call") -> bytes` 由本任务补到 test fixture，仅返回严格新请求 schema。

```python
def test_cli_is_offline_and_does_not_inherit_execution(tmp_path, monkeypatch):
    import os, socket, urllib.request
    from tests.options.workbench_factories import cli_request_bytes
    def forbidden(*args, **kwargs):
        raise AssertionError("network or account environment access")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    request = tmp_path / "request.json"
    request.write_bytes(cli_request_bytes())
    out = tmp_path / "out"
    assert cli.main(["--request-file", str(request), "--output-dir", str(out)]) == 0
    assert cli.main(["--request-file", str(request), "--output-dir", str(out), "--live"]) == 2
```

- [ ] **Step 2: 红灯。** `uv run python -m pytest tests/options/test_workbench_cli.py -q`。
- [ ] **Step 3: 完整 CLI 流程。** 路径检查→读取最多1MiB+1byte并拒绝超限→strict schema/limits→load sealed input 或显式 fixture→纯比较与证书→新报告。不能只 stat 后无界 read。输入错误 exit2，内部失败 exit1，包含拒绝的完整诊断 exit0；stderr 只输出固定错误类别，不泄露原 argv/原值。
- [ ] **Step 4: 无覆盖发布。** 输出不得与 request、panel、任一 pinned source/旧结果相等、祖先、后代或 symlink 重叠。existing output 拒绝；新临时 sibling 用 xb/fsync 写 report.json、REPORT.md、identity.json，沿用已验证的 macOS renamex_np/Linux renameat2 no-replace 算法在新 CLI 自包含实现，禁止 import/改旧 CLI。竞态失败不覆盖新出现的目录；保留可识别 incomplete identity。
- [ ] **Step 5: 明确演示输入。** 新 CLI 可内置独立 fixture builder，数值按 §5，不 import tests 或旧 scripts；fixture_name allowlist=`terminal-eth-call/terminal-eth-straddle/terminal-vertical`。它只构造合同时刻固定的数学输入，无 fill、报价抓取或现行平台规则声明。请求不提供时不能自动选 fixture。
- [ ] **Step 6: 固定旧接口基线。** 实施前在隔离结果目录用旧 CLI 运行 `long-call/1000` 和 `straddle/3000`，保留三 artifacts 的 bytes/SHA。实施后同命令新目录逐字节比较；旧 report/Markdown/identity 均须相同。不得重封期望或将新字段加入旧 schema。
- [ ] **Step 7: 使用文档与最终验收。** GUIDE 说明新 CLI、请求字段、三 fixture、能力五列、null/zero、扩展位置与禁止权限；提供 JSON 请求示例及命令，不许写“已盈利”或可下单榜。完成以下命令并保留 exit/数量/失败证据：

```bash
uv run python -m pytest tests/options -q
uv run python -m pytest tests/options/test_boundaries.py tests/test_import_boundaries.py tests/test_parity.py -q
uv run ruff check zenquant/options/workbench_contracts.py zenquant/options/expressions.py zenquant/options/constraints.py zenquant/research/options_platform/workbench_inputs.py zenquant/research/options_platform/workbench_reporting.py scripts/analyze_options_workbench.py tests/options/workbench_factories.py tests/options/test_workbench_contracts.py tests/options/test_workbench_inputs.py tests/options/test_workbench_expressions.py tests/options/test_workbench_constraints.py tests/options/test_workbench_reporting.py tests/options/test_workbench_cli.py
uv run ruff format --check zenquant/options/workbench_contracts.py zenquant/options/expressions.py zenquant/options/constraints.py zenquant/research/options_platform/workbench_inputs.py zenquant/research/options_platform/workbench_reporting.py scripts/analyze_options_workbench.py tests/options/workbench_factories.py tests/options/test_workbench_contracts.py tests/options/test_workbench_inputs.py tests/options/test_workbench_expressions.py tests/options/test_workbench_constraints.py tests/options/test_workbench_reporting.py tests/options/test_workbench_cli.py
uv run zenquant-check
```

- [ ] **Step 8: 单用途提交与交接。** CLI、测试和使用文档分别 scope-pure；最终 fresh reviewer 检查全部 D2 diff 和原 P1 字节基线。记录 D2 targeted 与全仓 gate 分列；旧 A/C/E 若仍失败就保持 BLOCKED，不合并、不更新 golden、不部署。

## 6. 执行、覆盖与裁决

| 工作台规格范围 | 本计划落实 | 明确后续 |
| --- | --- | --- |
| 用户任务、两入口、第一性原理 | 3–6：固定候选与完整决策单 | 自动选市场/表达不属于 D2 |
| 可扩展核心和能力分列 | 1、3、5：additive envelope、精确类型/版本、五列结果 | 账户能力与真实授权 D3以后 |
| 主观修订/概率/黑天鹅区分 | 1–3、5：原观点保留、条件终值而非策略盈利 | IV路径、全日历尾部盈利/保护实验另协议 |
| 套利主张 | 4、5：同域全状态数学证明、完整边际缺证 | box/parity/跨所执行与融资另包 |
| PIT、hash、确定性、兼容 | 1–2、5–6：来源分列、严格 cutoff、no-replace、旧字节 | D1 continuous journal 新 adapter |
| 资金/风险/执行生命周期 | 3：互斥假设资金和 P1 model_funding | 原子预留、UNKNOWN订单、重启及实盘在 D3–D5验收 |
| 消融与收益裁决 | 5：记录可用于后续比较的版本与失败 | 本包不跑 alpha 搜索或填 Sharpe |

执行前使用独立 worktree，保留当前 dirty 工作；只带本计划所需已提交基线，不复制无关未提交文件。受保护 input/spec/report 的 SHA 在开工和交接时核对。

任务1冻结后，任务2/3/4可用最多三个并行实现 agent；只读 reviewer 可顺序复核。接口变更回到主协调者统一修订计划，不由某个 agent 私下改同名类型。任务5/6串行整合，最后全包审查。

用户已选 subagent-driven 方法。本计划审阅通过后才进入实施；技术方案没有给账户、订单、采样或资金分配授权。交付应报告：变更路径、targeted测试、旧接口字节兼容、全仓 gate、未完成模型/账户能力。利润、容量和实盘接受状态仍分别需要后续证据。
