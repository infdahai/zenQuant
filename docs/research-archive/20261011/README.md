# 遗留研究归档与 Rust paper 验收约束

核验日期：2026-10-11。评估基线为 main `b32e0b8e448cd6ce4ae6efb0649f02a041c16f7f`。
本记录保留旧研究的负结果、来源与可恢复历史，不迁移 Python 实现，不批准策略、训练或 paper-live。
以下统计仅转述固定提交中的文档；本轮没有重跑历史实验、读取封存数据或验证被忽略的结果文件。

## 归档身份与取舍

两个 annotated tag 已推送并读回核对对象及目标提交。完整 SHA、标签对象与五个旧分支映射见
[archive-refs.json](archive-refs.json)。短 SHA 仅用于本表显示。

| 原远端分支 | 精确提交前缀 | 归档 tag |
| --- | --- | --- |
| `feature/trend-registration-time-space-execution` | `7d93cc1e53c8` | `archive/2026-10-11/trend-registration-execution` |
| `feature/trend-registration-trend-participation-execution` | `7d93cc1e53c8` | 同上 |
| `feature/trend-registration-time-space-hypothesis` | `e7d0f3d8afae` | 同上；已验证为 execution 祖先 |
| `feature/trend-registration-trend-participation-hypothesis` | `e7d0f3d8afae` | 同上；已验证为 execution 祖先 |
| `optimization/full-cycle-training` | `08fbfd413108` | `archive/2026-10-11/full-cycle-training` |

trend 分支保存了两层假说、输入冻结和注册设施；计划中的 `time_space_shadow.py`、
`trend_participation.py` 及对应策略 runner 没有实现，Git 中没有相应 paper 结果。
注册成功不能解释为策略通过。保留其审计思路和会计测试语义，不整分支合并。

training 分支主要有用工作已通过 PR [#10](https://github.com/infdahai/zenQuant/pull/10)、
[#11](https://github.com/infdahai/zenQuant/pull/11) 进入现有历史。剩余 103 行
`scripts/secret_benchmark.py` 读取当时最新数据，缺少完整冻结身份和一次性盲测约束；
弱日期检查也不足以替代现有数据切分纪律，因此不迁移。

## 保留的负结果及证据等级

权威来源是固定 execution 提交的
[QUANT_V2_ACCEPTANCE_HANDOFF.md](https://github.com/infdahai/zenQuant/blob/7d93cc1e53c8908ef26c753e83935877f5861853/docs/QUANT_V2_ACCEPTANCE_HANDOFF.md)，
该文件 SHA-256 已记录在 `archive-refs.json`。同历史中的旧 Expert Review、retirement 文档
若与其冲突，以这份修复后交接为该 donor 的记录口径；不以较早巨额利润做选择依据。

文档记载：旧费用处理把负现金截为零，抹去杠杆成交后的融资负债，旧利润和受影响绩效指标失效。
修复后 identity-complete 30-run 文档记录的 late-dev A5 平均净损益为：

| 成本条件 | 文档记录均值 | 正收益种子 |
| --- | ---: | ---: |
| 观察到的基准成本 | `+379.21` | `4/5` |
| 额外 `5bp` | `-353.44` | `1/5` |
| 额外 `15bp` | `-1818.72` | `0/5` |

记录的额外成本空间约 `2.59bp`，robustness `0/5`，结论为
`BLOCKED_PAUSE_PROMOTION_AND_TRAINING`。五个扰动种子不是五份独立市场样本。
这些数字是历史文档陈述，不是本轮复算、盈利背书或策略准入。
交接还明确 funding/financing 未完整纳入旧 equity；不能据此声称全成本账本一致。

210/210 新旧阶段对照仍为 `UNVERIFIED_LEGACY`；没有可重放的旧 fill/mark/fee 账本，
且两侧阶段 provenance 不完整。不能反推首笔受影响费用、首个分歧或精确因果收益增量。
交接记载的修复报告 SHA-256 为
`3259f41a54a4ca688fd17cc59ad5c784564c49032f1c2a34721ea0ad237916d9`，
acceptance summary 为 `27b344630b9737446897cc20a195c44b01584fadc25f9a739057208a0a97c723`；
对应生成文件本轮没有获取，hash 只保留原文身份声明。

评估基线的旧 Python `zenquant/risk/guard.py` 仍含 `max(0.0, self._cash - fee)`。
本次不修复或重新启用该运行路径，也不将它作为 Rust paper 的资金权威。
Rust 现有全额现金预留要求 available 非负，是准入预算不变量，不是抹去已发生负债；
不能把这两种语义混为同一缺陷。

## 注册收据保存边界

[github-registration-metadata.json](github-registration-metadata.json) 保存四个 run 的原始
artifact 元数据、GitHub digest、分支／提交、时间、状态及永久 API/运行链接。
四次运行均是 manifest 注册，不是两层策略执行。

| Run | Artifact ID | GitHub 报告 ZIP 字节 | 到期时间 UTC |
| --- | ---: | ---: | --- |
| [32686267806](https://github.com/infdahai/zenQuant/actions/runs/32686267806) | 9505750638 | 356 | 2026-11-22 03:23:18 |
| [32686273339](https://github.com/infdahai/zenQuant/actions/runs/32686273339) | 9505753052 | 354 | 2026-11-22 03:23:24 |
| [32697642994](https://github.com/infdahai/zenQuant/actions/runs/32697642994) | 9509378232 | 352 | 2026-11-22 06:30:49 |
| [32697643022](https://github.com/infdahai/zenQuant/actions/runs/32697643022) | 9509377347 | 354 | 2026-11-22 06:30:49 |

授权 GitHub 工具返回了 ZIP 文件引用，但向云工作区下载四次均 HTTP 403。
因此没有保存 ZIP/收据内容、没有检查 ZIP 内敏感内容、没有独立校验其 digest；
只提交小型元数据，不保留临时签名下载 URL。以后若取得 ZIP，必须先核验原 digest、
条目路径、体积与内容，再保存小型注册收据，不能混入凭据或生成数据大包。

tag 只保留 Git 已跟踪历史，不备份忽略的 `results/`、`data/`、冻结 corpus 或交易状态。
这些标签与元数据不构成原实验可重现的证明。

## 下一轮 Rust paper 必须验证的不变量

以下是未来实现的验收清单，不表示目前 Rust 已有 OMS、成交账本或完整恢复。
旧测试仅提供行为参考：固定提交的
[risk guard](https://github.com/infdahai/zenQuant/blob/7d93cc1e53c8908ef26c753e83935877f5861853/tests/test_risk_guard.py#L163)、
[execution router](https://github.com/infdahai/zenQuant/blob/7d93cc1e53c8908ef26c753e83935877f5861853/tests/test_execution_router.py#L22)、
[fixed-event parity](https://github.com/infdahai/zenQuant/blob/7d93cc1e53c8908ef26c753e83935877f5861853/tests/test_parity.py#L109)。

- 两条 paper 线各有 run_id、策略版本／参数、显式模拟初资、订单／成交与账本归属。人工或算法来源与产品类型独立；共享数据、精确计算和执行机制，不共享隐含余额。
- 先定义产品现金／负债语义。现货全额支付应在准入时拒绝超预算；若以后支持融资负债，已发生负债必须完整记录，不能截零。所有金额和数量精确，估值近似另行标明。
- 每笔 fill 与 fee 只入账一次；同身份同载荷重试幂等，不同载荷原子拒绝。无效金额、身份、时点或超量成交不能部分改账。
- 部分成交只更新实际量；反向开仓须先完成原仓关闭，不能用目标仓位替代成交事实。通知订阅者失败不能拆开资金、费用和持仓提交。
- 固定事件重放及重启恢复必须得到相同现金、负债、持仓、费用和最终 mark 估值，并验证守恒、原子发布及故障重试。
- 区分 event、received、available、decision cutoff、execution 时点；已知证据不得晚于决策，执行时再次检查新鲜度和有效期。缺失值不补零，回填不冒充 forward，封存数据限制不变。
- 冻结代码、数据、规格、费用、信号与成交假设身份。hash 标识字节，不认证市场真伪；保持 UNKNOWN/UNVERIFIED，旧 v1 View 必填和严格未知字段拒绝不变。
- 模拟成交须有可观察 bid/ask、深度、规格、费用和显式滑点假设；不拿 mark 当可成交价格，不重复扣已包含于成交价的滑点。缺证据拒绝或不可评估。
- 期权先明确入场、退出和到期生命周期。现有终值数学不是账户／成交引擎；`LinearViaFuture` 不得改标为 `LinearCash`。CEX 现货和永续分别定义，不能静默省略永续 funding、保证金或清算负债。
- 先用单进程、少量模块和有限次显式输入证明最小闭环；不先加缓存、并发撮合、DSL、多交易所或自动优化。必要正确性靠不变量与故障测试保留，可选机制仅在实测解决问题时增加。

## 恢复与检查

本证据 PR 通过适用 CI 并合并后，删除前须再次精确核对五个远端 SHA；任何变化都重新评估。
删除活跃分支不删除标签、Git 对象、本地数据或 stash。需要时可恢复本地原分支：

```sh
git fetch origin tag archive/2026-10-11/trend-registration-execution
git fetch origin tag archive/2026-10-11/full-cycle-training
git branch feature/trend-registration-time-space-execution archive/2026-10-11/trend-registration-execution^{commit}
git branch feature/trend-registration-trend-participation-execution archive/2026-10-11/trend-registration-execution^{commit}
git branch feature/trend-registration-time-space-hypothesis e7d0f3d8afaea0f457b47e75c2328acc9695a7b0
git branch feature/trend-registration-trend-participation-hypothesis e7d0f3d8afaea0f457b47e75c2328acc9695a7b0
git branch optimization/full-cycle-training archive/2026-10-11/full-cycle-training^{commit}
cd docs/research-archive/20261011
sha256sum --check SHA256SUMS
```

本轮文档验证包括 JSON 解码、分支／标签／祖先／固定来源 hash 比对、归档目录哈希和
`git diff --check`。无需为了文档新增镜像实现的测试；源码验收仍由适用的现有 PR CI 执行。
