#!/bin/bash
# 启动纸面实盘服务 (冻结版本)

set -e

echo "=========================================="
echo "ZenQuant 纸面实盘启动脚本"
echo "=========================================="
echo ""

# 1. 检查当前分支
CURRENT_BRANCH=$(git branch --show-current)
CURRENT_COMMIT=$(git rev-parse --short HEAD)

echo "当前分支: $CURRENT_BRANCH"
echo "当前提交: $CURRENT_COMMIT"
echo ""

# 2. 警告：不要在开发分支运行实盘
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "⚠️  警告: 当前在开发分支 ($CURRENT_BRANCH)"
    echo ""
    echo "推荐做法:"
    echo "1. 创建独立实盘目录:"
    echo "   cd ~/workspace/code"
    echo "   git clone zenQuant zenQuant-live"
    echo "   cd zenQuant-live"
    echo "   git checkout main"
    echo ""
    echo "2. 或使用git worktree:"
    echo "   git worktree add ../zenQuant-live main"
    echo ""
    read -p "是否继续在当前分支运行? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 3. 检查数据库
echo "检查数据库连接..."
if [ -z "$ZENQUANT_PG_DSN" ]; then
    echo "❌ 错误: ZENQUANT_PG_DSN 未设置"
    echo ""
    echo "请设置环境变量:"
    echo "  export ZENQUANT_PG_DSN=\"postgresql://localhost/zenquant_live\""
    exit 1
fi

if ! psql "$ZENQUANT_PG_DSN" -c "SELECT 1" > /dev/null 2>&1; then
    echo "❌ 错误: 无法连接数据库"
    echo ""
    echo "请创建数据库:"
    echo "  createdb zenquant_live"
    exit 1
fi

echo "✅ 数据库连接正常"
echo ""

# 4. 检查端口
if lsof -i :8765 > /dev/null 2>&1; then
    echo "⚠️  警告: 端口 8765 已被占用"
    lsof -i :8765
    echo ""
    read -p "是否停止现有服务? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        pkill -f "zenquant.app.server" || true
        sleep 2
    else
        exit 1
    fi
fi

# 5. 创建日志目录
mkdir -p logs/decisions/$(date +%Y-%m)
mkdir -p logs/metrics

# 6. 记录部署信息
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
LOG_FILE="logs/live_$(date +%Y%m%d_%H%M%S).log"
DEPLOYMENT_LOG="logs/deployment.log"

echo "$TIMESTAMP | Branch: $CURRENT_BRANCH | Commit: $CURRENT_COMMIT | Starting paper-live" >> "$DEPLOYMENT_LOG"

# 7. 启动服务
echo "启动纸面实盘服务..."
echo "日志文件: $LOG_FILE"
echo ""

nohup uv run python -m zenquant.app.server --mode live \
    > "$LOG_FILE" 2>&1 &

SERVER_PID=$!
echo "服务PID: $SERVER_PID"
echo "$TIMESTAMP | PID: $SERVER_PID | Started" >> "$DEPLOYMENT_LOG"

# 8. 等待启动
echo ""
echo "等待服务启动..."
sleep 5

# 9. 健康检查
if ! ps -p $SERVER_PID > /dev/null; then
    echo "❌ 错误: 服务启动失败"
    echo ""
    echo "查看日志:"
    echo "  tail -50 $LOG_FILE"
    exit 1
fi

if ! lsof -i :8765 > /dev/null 2>&1; then
    echo "⚠️  警告: 端口 8765 未监听 (可能仍在启动中)"
else
    echo "✅ 端口 8765 已监听"
fi

# 10. 显示监控命令
echo ""
echo "=========================================="
echo "✅ 纸面实盘服务已启动"
echo "=========================================="
echo ""
echo "监控命令:"
echo "  # 查看实时日志"
echo "  tail -f $LOG_FILE"
echo ""
echo "  # 查看进程状态"
echo "  ps aux | grep zenquant"
echo ""
echo "  # 打开仪表板"
echo "  open http://localhost:8765"
echo ""
echo "  # 运行验证脚本"
echo "  uv run zenquant-live-validate --symbol BTCUSDT_PERP --watch 300"
echo ""
echo "  # 停止服务"
echo "  pkill -f 'zenquant.app.server'"
echo ""
echo "  # 每日报告"
echo "  bash scripts/daily_report.sh"
echo ""
echo "部署记录: $DEPLOYMENT_LOG"
echo "=========================================="
