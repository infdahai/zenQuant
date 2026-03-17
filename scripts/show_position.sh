#!/bin/bash
# 快速查看当前仓位和杠杆信息

echo "=========================================="
echo "当前仓位和杠杆信息"
echo "=========================================="
echo ""

python3 << 'EOF'
import json, urllib.request
url = "http://localhost:8765/api/state?symbol=BTCUSDT_PERP&tf=4h"
with urllib.request.urlopen(url) as response:
    data = json.loads(response.read())

pos = data.get('position_state', {})
sizing = data.get('latest_sizing', {})
price = data.get('last_price', 0)
market = data.get('market_state', {})
playbook = data.get('active_playbook', {})

print('📊 市场状态')
print(f'  当前价格: ${price:,.2f}')
print(f'  市场状态: {market.get("regime", "N/A")}')
print(f'  置信度: {market.get("regime_confidence", 0):.2%}')
print(f'  转换风险: {market.get("transition_risk", 0):.2%}')
print()

print('📈 策略信息')
print(f'  Playbook: {playbook.get("playbook_id", "N/A")}')
print(f'  最大杠杆: {playbook.get("max_leverage", 0):.2f}x')
print()

print('💰 仓位信息')
if pos.get('current_qty', 0) > 0:
    direction = "🟢 做多" if pos.get('direction', 0) > 0 else "🔴 做空"
    print(f'  方向: {direction}')
    print(f'  持仓: {pos.get("current_qty", 0):.4f} BTC')
    print(f'  入场价: ${pos.get("entry_price", 0):,.2f}')
    print(f'  当前价: ${price:,.2f}')

    # 计算盈亏
    if pos.get('entry_price', 0) > 0:
        pnl_pct = (price / pos['entry_price'] - 1) * 100 * pos.get('direction', 1)
        pnl_symbol = "📈" if pnl_pct > 0 else "📉"
        print(f'  未实现盈亏: {pnl_symbol} {pnl_pct:+.2f}%')

    print(f'  持仓时间: {pos.get("bars_held", 0)} bars (4h)')
    print(f'  追踪止损: ${pos.get("trailing_stop", 0):,.2f}')

    # 计算名义价值
    notional = pos['current_qty'] * price
    print(f'  名义价值: ${notional:,.2f}')
else:
    print('  状态: 空仓')
print()

print('⚡ 杠杆信息')
print(f'  最终杠杆: {sizing.get("final_leverage", 0):.2f}x')
print(f'  基础规模: {sizing.get("base_size", 0):.2f}')
print(f'  置信度乘数: {sizing.get("confidence_multiplier", 0):.2%}')
print(f'  压力折扣: {sizing.get("stress_discount", 0):.2%}')
print(f'  执行折扣: {sizing.get("execution_haircut", 0):.2%}')
print()

# 假设本金计算实际杠杆
if pos.get('current_qty', 0) > 0 and price > 0:
    notional = pos['current_qty'] * price
    for capital in [100000, 50000, 10000]:
        actual_lev = notional / capital
        print(f'  实际杠杆 (本金${capital:,}): {actual_lev:.2f}x')

print()
print("==========================================")
print("查看仪表板: http://localhost:8765")
print("==========================================")
EOF
