# 仪表板查看指南

## 🌐 访问仪表板

**URL**: http://localhost:8765

浏览器应该已经自动打开。如果没有，手动访问上述URL。

---

## 📊 仪表板功能

### 1. 主页面 (Dashboard)

显示实时交易状态：

- **K线图**: 4h时间框架的价格走势
- **当前价格**: 实时BTC价格 (当前: $74,062)
- **目标仓位**: 策略建议的仓位 (当前: 3.95 BTC)
- **市场状态**:
  - Regime (趋势/震荡/熊市)
  - Confidence (置信度)
  - Transition Risk (转换风险)
- **Playbook**: 当前使用的策略手册
- **Exit Signals**: 退出信号状态

### 2. 指标页面 (Indicators)

显示技术指标：

- **ATR** (波动率)
- **EMA** (趋势)
- **RSI** (超买超卖)
- **Bollinger Bands** (波动区间)
- **Volume** (成交量)

### 3. WebSocket连接

仪表板通过WebSocket实时更新：

- 每4小时bar close自动更新
- 无需刷新页面
- 实时显示最新决策

---

## 🔍 当前状态检查

### 命令行查看

```bash
# 1. 查看当前状态
curl -s "http://localhost:8765/api/state?symbol=BTCUSDT_PERP&tf=4h" | python3 -m json.tool

# 2. 查看指标历史
curl -s "http://localhost:8765/api/indicators/history?symbol=BTCUSDT_PERP&tf=4h" | python3 -m json.tool

# 3. 查看服务器日志
tail -f logs/live_20260317_125709.log

# 4. 查看数据库记录
export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"
psql $ZENQUANT_PG_DSN -c "SELECT COUNT(*) FROM market_states;"
```

### 当前系统状态

✅ **服务运行正常**
- 端口: 8765
- PID: 38648
- WebSocket: 已连接
- 数据源: Binance实时数据

✅ **数据获取正常**
- 最新价格: $74,062
- 目标仓位: 3.95 BTC (做多)
- K线数据: 实时更新

---

## 📈 关键指标说明

### Target Position (目标仓位)
- **正值**: 做多 (Long)
- **负值**: 做空 (Short)
- **0**: 空仓 (Flat)
- **范围**: -3.5 到 +3.5 (杠杆倍数)

### Regime (市场状态)
- **BULL**: 牛市趋势
- **BEAR**: 熊市趋势
- **RANGE**: 震荡市
- **PRESERVE_CAPITAL**: 保守模式

### Playbook (策略手册)
- **BULL_CONTINUATION**: 牛市延续
- **BEAR_DEFENSE**: 熊市防御
- **RANGE_LIGHT**: 震荡轻仓
- **CAPITAL_PRESERVATION**: 资本保护

### Exit Type (退出类型)
- **NONE**: 无退出信号
- **INVALIDATION**: 信号失效
- **TRAILING_STOP**: 追踪止损
- **DECAY**: 信号衰减
- **REGIME_SHIFT**: 市场状态转换

---

## 🔔 监控要点

### 每4小时检查 (Bar Close时)

1. **仓位变化**: 目标仓位是否合理
2. **市场状态**: Regime是否正确识别
3. **退出信号**: 是否有异常退出
4. **质量标志**: Quality State是否为OK

### 每日检查

```bash
# 生成每日报告
bash scripts/daily_report.sh

# 查看报告
cat logs/metrics/daily_summary_$(date +%Y-%m-%d).txt
```

### 异常情况

⚠️ **需要关注的情况**:
- Quality State = UNSAFE (数据质量问题)
- 频繁的Regime切换 (可能过度交易)
- 极端仓位 (接近±3.5)
- WebSocket断连 (需要重启)

---

## 🛠️ 常用操作

### 查看实时日志
```bash
tail -f logs/live_20260317_125709.log
```

### 停止服务
```bash
pkill -f 'zenquant.app'
```

### 重启服务
```bash
pkill -f 'zenquant.app'
sleep 2
export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"
bash scripts/start_live.sh
```

### 查看数据库
```bash
export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"

# 最近10条决策
psql $ZENQUANT_PG_DSN -c "
SELECT
    TO_TIMESTAMP(cutoff_time_ns / 1e9) as time,
    regime,
    playbook_id,
    final_leverage,
    exit_type
FROM market_states m
JOIN playbook_selections p ON m.cutoff_time_ns = p.cutoff_time_ns
JOIN sizing_decisions s ON m.cutoff_time_ns = s.cutoff_time_ns
JOIN exit_decisions e ON m.cutoff_time_ns = e.cutoff_time_ns
ORDER BY m.cutoff_time_ns DESC
LIMIT 10;
"
```

---

## 📱 移动端访问

如果需要在手机上查看：

1. 确保手机和电脑在同一网络
2. 找到电脑IP地址: `ifconfig | grep "inet "`
3. 在手机浏览器访问: `http://[电脑IP]:8765`

---

## 🎯 下一步

1. **观察24小时**: 确保系统稳定运行
2. **检查决策质量**: 仓位变化是否合理
3. **运行验证脚本**:
   ```bash
   export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"
   uv run zenquant-live-validate --symbol BTCUSDT_PERP --watch 300
   ```
4. **每日报告**: 明天运行 `bash scripts/daily_report.sh`

---

**最后更新**: 2026-03-17 13:00
**服务状态**: ✅ 运行中
**数据收集**: ✅ 进行中
