# VIX 只读风险观察验收（2026-10-17）

基线：`5fca5d69222567dd0108deb3bbd54177d61de1a7`。功能代码提交：`cdd01e4fb31c67aa676d77688c9f4c77649a1608`，包含 `a80f30f` core/夹具/测试、`c0d6fe4` HTTP/身份/测试、`cdd01e4` Svelte/浏览器。后续提交只加入文档、CI 归档规则与本验收包；最终 PR head 和对应 CI 在交付时另行核实，避免自引用提交 hash。

实现保持一个纯计算模块、一个薄输入适配器、一个页面。严格独立契约保留原 v1，月度 VX 两合约与 VIX9D/VIX 各自可用或失败；两类输入不依赖账户、指标策略、意图或资金预留。没有通用指标框架、新 crate、Python/Go 后端、并发或缓存。

## 可复验结果

| 命令（仓库根目录） | 结果 | 原始记录 |
|---|---|---|
| `cargo test --manifest-path rust/Cargo.toml --workspace --all-features --locked`（基线） | 182 passed, 0 failed | baseline-test.txt |
| `cargo test --manifest-path rust/Cargo.toml -p options-core --test vix_observation --test architecture --locked`（开发检查） | 新增 14 项核心测试与 3 项架构检查通过，最终已包含在全量测试 | final-test.txt |
| `cargo fmt --manifest-path rust/Cargo.toml --all -- --check` | exit 0 | fmt.txt |
| `cargo clippy --manifest-path rust/Cargo.toml --workspace --all-targets --all-features --locked -- -D warnings` | exit 0 | clippy.txt |
| `cargo test --manifest-path rust/Cargo.toml --workspace --all-features --locked`（最终代码） | 199 passed, 0 failed；新增 14 core + 3 HTTP | final-test.txt |
| `cargo build --manifest-path rust/Cargo.toml --release --locked -p options-workbench --bin options-ui --example paper_browser_fixture` | exit 0 | release.txt |
| `npm run check --prefix options-ui` | 0 errors / 0 warnings | ui-check.txt |
| `npm test --prefix options-ui` | 2 passed | ui-unit.txt |
| `npm run build --prefix options-ui` | exit 0 | ui-build.txt |
| `npm run test:options` | 6 passed：既有期权 3 + 新 VIX 3；脚本亦包含 JS unit 2 | browser-options.txt |
| `npm run test:paper` | 1 passed：既有人工流程，隔离工程测试状态 | browser-paper.txt |
| `git diff --check` | exit 0 | 交付前执行 |
| `sha256sum --check SHA256SUMS`（本目录） | 所有证据一致 | CI 亦执行 |

云工具链：Rust 1.90.0、Node v24.19.0；Chromium 版本见 summary.json。环境设置：

```sh
export CARGO_HOME=/workspace/.zenquant-cargo
export RUSTUP_HOME=/workspace/.zenquant-rustup
export PATH=/workspace/.zenquant-cargo/bin:$PATH
export ZQ_BROWSER=/usr/bin/chromium
export XDG_CACHE_HOME=/workspace/.zenquant-browser-cache
export XDG_CONFIG_HOME=/workspace/.zenquant-browser-config
```

`cargo fmt --manifest-path rust/Cargo.toml --all` 用于格式化。最后一次复验在收紧具体合约符号格式之后完成；Svelte 未再变化。既有 View 必填、未知字段、原子发布/故障恢复和封存检查均在全量 Rust 测试内。未添加依赖 Python 的 VIX 测试。旧 Python Harness 由对应 PR CI 验证，不以它替代原生验证。

## 演示验收

- `01-desktop.png`：月度 `VX/V6`/`VX/X6`，虚构 24/20 → 精确价差 4、比率差 0.2；独立指数 27/24 → VIX9D/VIX 1.125。
- `02-mobile.png`：390×844 视口，无横向溢出、无 pageerror。
- `03-asynchronous.png`：报价不同步，INVALID 且没有数值；另测缺失、过期，无补零。
- `04-roll.png`：显式到期边界切换至 `VX/X6`/`VX/Z6`，展示换月警告，不解释为恐慌改善。
- `observation.json`：从真实浏览器导出的 Rust 报告及输入、来源、时区、合约日历和构建身份；全部为固定演示、UNVERIFIED。精确循环比率 1/21、输入独立性与严格错误恢复也经过浏览器测试。
- `state-before.json` / `state-after.json`：原 `.simulated-reservations/snapshot.json` SHA-256 和 mtime 一致；`.manual-paper/snapshot.json` 仍不存在。测试没有创建用户预算、意图或账户记录。既有 stash 未修改。

## 限制与后续

没有授权实时数据、真实行情响应留存、账户或订单接入、历史回测、自动阈值、风险预测或交易权限。不宣称达到未测性能目标。本轮无外部数据阻塞，采用显式演示夹具完成可验收计算和显示；真实数据接入仍需确认授权、日历完整性及时间/延迟语义。环境断开提示后于 15:30:38 UTC 成功执行并核实文件仍在，随后保存本地提交，未重复实现。

下一最小步骤：对单一授权来源做只读快照适配与证据留存，复用现有异步、缺失、过期和换月测试。模块契约、Cboe 一手引用及可选能力保留标准见 [运行指南](../../VIX_OBSERVATION_RUST_GUIDE.md)。完整改动路径见交付 PR 的 Files changed；本轮不合并、部署或交易。
