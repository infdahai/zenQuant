# Rust 期权工作台 D2 验证记录 — 2026-10-10

实现分支 `codex/options-workbench-rust`；功能代码 `bc73f82`；基线 `99d3292`。D2 的离线诊断实现和独立复核通过，**全仓集成门禁仍未通过**。工作区保留在 `.worktrees/options-workbench-rust`；未合并、推送、部署、接账户或下单。

[使用指南与架构图](../../OPTIONS_WORKBENCH_RUST_GUIDE.md) 包含实际 CLI、schema、模块扩展及算术边界。`options-core` 是纯 Rust 计算/准入库，`options-workbench` 是原生离线 CLI；Python 仅提供可选封存数据导出和有限域对照。

## 已验收

- Cash、Call、Put、Straddle、Strangle 的到期情景比较；同场所 Vertical 的全价格域数学证书。手续费、深度、最小数量、币种与预算证据分开。
- 现金零腿基线经实际 CLI 验证。公开分析、证书与报告通过借用型 `ValidatedAnalysis` 强制准入；未绑定、未来才可见、空情景和历史不一致输入不产生有效资金预算或 net outcomes。报告一次校验后复用。
- Root 最终独立验证：Rust **70** 通过，fmt/Clippy 通过；Python options/import/parity **459 通过、1 跳过**。跳过为旧 P1 封存根不在隔离工作区，真实新面板另外通过原生验证。
- 历史面板 **480 报价、48 规格、543 来源文件**；新旧导出字节相同，同代码下三次原生报告字节相同。原始文件校验与导出字段语义认证分开，投影始终 `EXPORTED_PROJECTION_UNATTESTED`。
- 23 个旧 P1 文件、主工作区 110 个保护文件 SHA 未变；两组旧 CLI 的 6 个产物与基线逐字节相同。

## 集成阻碍

全 `zenquant-check` 仅执行一次：0/B/C/D 通过；A 为三个旧文件格式；E 为旧 panel conftest 在默认全量发现中没有注册选项；F 为隔离工作区缺 BTC 历史 parquet。排除新测试模块、仅执行该旧测试仍重现 E。没有修旧路径、复制共享数据或更新 golden。

## 证据与判断

[归档 manifest](archive-manifest.json)、[验收状态](acceptance.json)、[完整门禁日志](task5/zenquant-check.log)、[独立最终验证](final_verification/status.json)、[最终复审](final-re-review.md)、[执行判断](rulings.md) 均在本目录。完整原生 JSON/Markdown、导出和重放输入保留在 `results/options_platform/rust_d2_execution_20261010/`；原始两个封存根仍在主工作区。上述目录内文件对应 hashes，不需要聊天历史恢复。

所有实际/纸面 PnL、Sharpe、容量和生命周期套利收益均未知；静态数学正下界不证明交易可完成。v1 只覆盖同币种、欧式线性到期模型，`linear_via_future` 只是条件现金等价。未覆盖到期前卖出、动态对冲、实际保证金/融资/交割；Linux 原生运行未验收。Mac 封存语料依赖的导出安全测试在其他机器会跳过，作为后续 portability 工作保留。

Rust 使用新 schema 和 `exact_decimal.rust.v1`；有限域对照不承诺 Python 50 位舍入或哈希的全面等价。不终止/越界计算显式拒绝；未给予任何下单权限。

归档 Rust 文本日志仅去除了末尾额外空行；原始验证日志保留在 results 目录，内容与退出状态不变。归档文件自身由 archive-manifest 单独绑定。

[主目录保护核对](preservation.json) 记录原 HEAD/分支、110 文件直接 SHA 与脏路径集合。旧 status 文本 checksum 未记录命令/编码，不用于完整状态字节一致的声明。
