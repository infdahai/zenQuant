# 实盘部署方案

## 问题
1. 实盘需要冻结版本
2. 开发在optimization分支持续进行
3. 需要隔离实盘环境和开发环境

## 解决方案

### 方案A: 独立实盘目录 (推荐)

```bash
# 1. 创建独立实盘目录
cd ~/workspace/code
git clone zenQuant zenQuant-live
cd zenQuant-live

# 2. 切换到稳定版本
git checkout main  # 或特定tag

# 3. 创建独立虚拟环境
uv venv --python 3.12
source .venv/bin/activate
uv sync

# 4. 配置实盘环境
export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"
export ZENQUANT_LIVE_DIR="$HOME/workspace/code/zenQuant-live"

# 5. 启动实盘服务
nohup uv run python -m zenquant.app.server --mode live \
    > logs/live_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 6. 记录版本信息
echo "$(date): Started live trading on commit $(git rev-parse HEAD)" >> logs/deployment.log
```

**优点**:
- 完全隔离开发和实盘
- 可以随时回滚
- 日志清晰分离

### 方案B: Git Worktree (轻量级)

```bash
# 1. 创建worktree (共享.git目录)
cd ~/workspace/code/zenQuant
git worktree add ../zenQuant-live main

# 2. 在worktree中启动实盘
cd ../zenQuant-live
uv sync
export ZENQUANT_PG_DSN="postgresql://localhost/zenquant_live"
nohup uv run python -m zenquant.app.server --mode live \
    > logs/live_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```

**优点**:
- 节省磁盘空间
- 共享.git历史
- 易于管理

### 方案C: Docker容器 (生产级)

```dockerfile
# Dockerfile.live
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install uv && uv sync
CMD ["uv", "run", "python", "-m", "zenquant.app.server", "--mode", "live"]
```

```bash
# 构建镜像
docker build -f Dockerfile.live -t zenquant-live:v1.0 .

# 运行容器
docker run -d \
    --name zenquant-live \
    -e ZENQUANT_PG_DSN="postgresql://host.docker.internal/zenquant_live" \
    -p 8765:8765 \
    -v $(pwd)/logs:/app/logs \
    zenquant-live:v1.0
```

**优点**:
- 完全隔离
- 可重现部署
- 易于回滚

## 推荐方案

**当前阶段**: 方案A (独立目录)
- 简单直接
- 完全隔离
- 适合纸面实盘验证

**生产阶段**: 方案C (Docker)
- 可重现
- 易于管理
- 适合真实资金

## 日志收集策略

### 日志目录结构
```
zenQuant-live/
├── logs/
│   ├── deployment.log          # 部署记录
│   ├── live_20260317_143000.log  # 服务日志
│   ├── decisions/              # 决策日志
│   │   └── 2026-03/
│   │       ├── market_states.jsonl
│   │       ├── playbook_selections.jsonl
│   │       ├── sizing_decisions.jsonl
│   │       └── exit_decisions.jsonl
│   └── metrics/                # 性能指标
│       └── daily_summary.csv
```

### 日志轮转配置
```python
# zenquant/app/logging_config.py
import logging
from logging.handlers import RotatingFileHandler

def setup_live_logging():
    handler = RotatingFileHandler(
        'logs/live.log',
        maxBytes=100*1024*1024,  # 100MB
        backupCount=10
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logging.getLogger().addHandler(handler)
```

## 监控脚本

### 健康检查
```bash
#!/bin/bash
# scripts/check_live_health.sh

# 检查进程
if ! pgrep -f "zenquant.app.server" > /dev/null; then
    echo "ERROR: Live server not running"
    exit 1
fi

# 检查端口
if ! lsof -i :8765 > /dev/null 2>&1; then
    echo "ERROR: Port 8765 not listening"
    exit 1
fi

# 检查数据库连接
if ! psql $ZENQUANT_PG_DSN -c "SELECT 1" > /dev/null 2>&1; then
    echo "ERROR: Database connection failed"
    exit 1
fi

# 检查最近决策时间
last_decision=$(psql $ZENQUANT_PG_DSN -t -c \
    "SELECT MAX(cutoff_time_ns) FROM market_states")

if [ -z "$last_decision" ]; then
    echo "WARNING: No decisions in database"
fi

echo "OK: Live server healthy"
```

### 每日报告
```bash
#!/bin/bash
# scripts/daily_report.sh

DATE=$(date +%Y-%m-%d)
psql $ZENQUANT_PG_DSN << EOF > logs/metrics/daily_summary_${DATE}.txt
-- 每日决策统计
SELECT
    COUNT(*) as total_decisions,
    AVG(final_leverage) as avg_leverage,
    COUNT(CASE WHEN exit_type != 'NONE' THEN 1 END) as exit_count
FROM sizing_decisions s
JOIN exit_decisions e ON s.cutoff_time_ns = e.cutoff_time_ns
WHERE s.cutoff_time_ns > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1e9;
EOF
```

## 版本标记

### 创建实盘版本tag
```bash
# 在optimization分支完成后
git tag -a v1.0-live -m "Live trading version 1.0
- Forward validation Sharpe: 6.05
- Parameters: 0.65, 0.85, 3.5
- Data: 539 days Binance
- Tested: 2026-03-17"

git push origin v1.0-live
```

### 实盘部署记录
```bash
# logs/deployment.log
2026-03-17 14:30:00 | v1.0-live | commit 5d4634e | Started paper-live
2026-03-17 14:30:05 | Health check: OK
2026-03-17 14:30:10 | First bar-close event received
```
