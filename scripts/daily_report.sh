#!/bin/bash
# 生成每日实盘报告

set -e

DATE=$(date +%Y-%m-%d)
REPORT_FILE="logs/metrics/daily_summary_${DATE}.txt"

echo "生成每日报告: $DATE"
echo ""

if [ -z "$ZENQUANT_PG_DSN" ]; then
    echo "错误: ZENQUANT_PG_DSN 未设置"
    exit 1
fi

psql "$ZENQUANT_PG_DSN" > "$REPORT_FILE" << 'EOF'
\echo '=========================================='
\echo 'ZenQuant 每日实盘报告'
\echo '=========================================='
\echo ''

\echo '1. 决策统计 (最近24小时)'
\echo '------------------------------------------'
SELECT
    COUNT(*) as total_decisions,
    ROUND(AVG(final_leverage)::numeric, 2) as avg_leverage,
    ROUND(MIN(final_leverage)::numeric, 2) as min_leverage,
    ROUND(MAX(final_leverage)::numeric, 2) as max_leverage,
    COUNT(CASE WHEN final_leverage > 0 THEN 1 END) as long_count,
    COUNT(CASE WHEN final_leverage < 0 THEN 1 END) as short_count,
    COUNT(CASE WHEN final_leverage = 0 THEN 1 END) as flat_count
FROM sizing_decisions
WHERE cutoff_time_ns > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1e9;

\echo ''
\echo '2. 退出决策统计'
\echo '------------------------------------------'
SELECT
    exit_type,
    COUNT(*) as count,
    ROUND(AVG(target_reduction_pct)::numeric, 2) as avg_reduction_pct
FROM exit_decisions
WHERE cutoff_time_ns > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1e9
    AND exit_type != 'NONE'
GROUP BY exit_type
ORDER BY count DESC;

\echo ''
\echo '3. 市场状态分布'
\echo '------------------------------------------'
SELECT
    regime,
    COUNT(*) as count,
    ROUND(AVG(regime_confidence)::numeric, 2) as avg_confidence,
    ROUND(AVG(transition_risk)::numeric, 2) as avg_transition_risk
FROM market_states
WHERE cutoff_time_ns > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1e9
GROUP BY regime
ORDER BY count DESC;

\echo ''
\echo '4. Playbook选择分布'
\echo '------------------------------------------'
SELECT
    playbook_id,
    COUNT(*) as count,
    ROUND(AVG(playbook_confidence)::numeric, 2) as avg_confidence,
    ROUND(AVG(max_leverage)::numeric, 2) as avg_max_leverage
FROM playbook_selections
WHERE cutoff_time_ns > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1e9
GROUP BY playbook_id
ORDER BY count DESC;

\echo ''
\echo '5. 质量标志统计'
\echo '------------------------------------------'
SELECT
    quality_state,
    COUNT(*) as count
FROM market_states
WHERE cutoff_time_ns > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1e9
GROUP BY quality_state
ORDER BY count DESC;

\echo ''
\echo '6. 最近10条决策'
\echo '------------------------------------------'
SELECT
    TO_TIMESTAMP(cutoff_time_ns / 1e9) as time,
    ROUND(final_leverage::numeric, 2) as leverage,
    exit_type,
    quality_state
FROM sizing_decisions s
JOIN exit_decisions e ON s.cutoff_time_ns = e.cutoff_time_ns
JOIN market_states m ON s.cutoff_time_ns = m.cutoff_time_ns
WHERE s.cutoff_time_ns > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1e9
ORDER BY s.cutoff_time_ns DESC
LIMIT 10;

\echo ''
\echo '=========================================='
\echo '报告生成时间:'
SELECT NOW();
\echo '=========================================='
EOF

echo "✅ 报告已生成: $REPORT_FILE"
echo ""
echo "查看报告:"
echo "  cat $REPORT_FILE"
