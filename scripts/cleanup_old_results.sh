#!/bin/bash
# ZenQuant Results 清理脚本
# 清理旧版本的临时研究数据，保留最新版本和关键目录
#
# 执行前请确认：
# 1. 当前 framework_optimization 已完成
# 2. 已备份关键数据
# 3. 运行模式：DRY_RUN=1 (预览) 或 DRY_RUN=0 (执行)

set -euo pipefail

RESULTS_DIR="/Users/disaster/workspace/code/zenQuant/results"
ARCHIVE_DIR="${RESULTS_DIR}/archive"
DRY_RUN="${DRY_RUN:-1}"  # 默认干运行模式

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查必要的保护目录是否存在
check_protected_dirs() {
    local protected_dirs=(
        "framework_optimization"
        "data_readiness"
        "research_swarm"
        "paulwei_research"
    )

    for dir in "${protected_dirs[@]}"; do
        if [ ! -d "${RESULTS_DIR}/${dir}" ]; then
            log_warn "保护目录不存在: ${dir}"
        fi
    done
}

# 清理函数：保留最新N个，删除其余
cleanup_pattern() {
    local pattern=$1
    local keep_count=$2
    local description=$3

    log_info "处理: ${description} (pattern: ${pattern}, 保留: ${keep_count})"

    # 查找所有匹配的目录，按版本号排序
    local all_dirs=($(find "${RESULTS_DIR}" -maxdepth 1 -type d -name "${pattern}" | sort -V))
    local total_count=${#all_dirs[@]}

    if [ $total_count -eq 0 ]; then
        log_info "  未找到匹配的目录"
        return
    fi

    log_info "  找到 ${total_count} 个目录"

    # 计算要删除的数量
    local delete_count=$((total_count - keep_count))

    if [ $delete_count -le 0 ]; then
        log_info "  无需清理（总数 <= 保留数）"
        return
    fi

    log_info "  将删除 ${delete_count} 个旧版本"

    # 创建归档目录
    if [ $DRY_RUN -eq 0 ]; then
        mkdir -p "${ARCHIVE_DIR}"
    fi

    # 删除旧版本（保留最新的）
    local deleted=0
    for ((i=0; i<delete_count; i++)); do
        local dir="${all_dirs[$i]}"
        local dir_name=$(basename "$dir")
        local dir_size=$(du -sh "$dir" 2>/dev/null | cut -f1)

        if [ $DRY_RUN -eq 1 ]; then
            echo "  [DRY-RUN] 将删除: ${dir_name} (${dir_size})"
        else
            log_info "  删除: ${dir_name} (${dir_size})"
            rm -rf "$dir"
            deleted=$((deleted + 1))
        fi
    done

    if [ $DRY_RUN -eq 0 ]; then
        log_info "  ✓ 已删除 ${deleted} 个目录"
    fi
}

# 主清理流程
main() {
    log_info "=== ZenQuant Results 清理开始 ==="

    if [ $DRY_RUN -eq 1 ]; then
        log_warn "*** 干运行模式 (DRY_RUN=1) - 仅预览，不实际删除 ***"
        log_warn "*** 执行清理请运行: DRY_RUN=0 $0 ***"
    else
        log_warn "*** 执行模式 (DRY_RUN=0) - 将实际删除文件 ***"
        echo -n "确认继续？(输入 yes): "
        read -r confirmation
        if [ "$confirmation" != "yes" ]; then
            log_error "已取消"
            exit 1
        fi
    fi

    echo ""

    # 检查保护目录
    check_protected_dirs
    echo ""

    # 1. 清理 intent_churn_attribution（最占空间）
    cleanup_pattern "intent_churn_attribution_v*" 10 "intent_churn_attribution 版本"
    echo ""

    # 2. 清理 regime_objective_candidate_search (train_only)
    cleanup_pattern "regime_objective_candidate_search_v*_*train_only" 15 "regime_objective_candidate_search (train_only)"
    echo ""

    # 3. 清理 temporal_robustness (smoke)
    cleanup_pattern "temporal_robustness_v*_smoke" 10 "temporal_robustness (smoke)"
    echo ""

    # 4. 清理 framework_optimization_loop
    cleanup_pattern "framework_optimization_loop_v*" 3 "framework_optimization_loop"
    echo ""

    # 5. 清理其他 train_only 但非上述类别的
    cleanup_pattern "regime_objective_candidate_search_v[0-9]*" 10 "regime_objective_candidate_search (其他)"
    echo ""

    log_info "=== 清理完成 ==="

    if [ $DRY_RUN -eq 1 ]; then
        log_warn "这是预览模式，未实际删除任何文件"
        log_warn "执行清理请运行: DRY_RUN=0 $0"
    else
        log_info "清理后的磁盘使用："
        du -sh "${RESULTS_DIR}"
    fi
}

# 执行
main
