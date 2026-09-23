#!/usr/bin/env bash
# ============================================================
#  STX 交易机器人 - Linux/macOS 启动脚本
#  用法：
#     ./run.sh            # 按 .env 中的 TRADING_MODE 启动
#     ./run.sh paper      # 强制 Paper 模式
#     ./run.sh live       # 强制 Live 模式（含二次确认）
#     ./run.sh backtest   # 运行回测
#     ./run.sh test       # 运行单元测试
# ============================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

MODE="${1:-auto}"

# ---- 检查 Python ----
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] 未检测到 python3，请先安装 Python 3.10+"
    exit 1
fi

# ---- 虚拟环境 ----
if [ ! -d "venv" ]; then
    echo "[INFO] 首次运行，创建虚拟环境..."
    python3 -m venv venv
    # shellcheck disable=SC1091
    source venv/bin/activate
    python -m pip install --upgrade pip
    pip install -r requirements.txt
else
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

# ---- 分支执行 ----
case "$MODE" in
    paper)
        export TRADING_MODE=paper
        echo "[INFO] 启动 Paper 模式"
        python main.py
        ;;
    live)
        echo "============================================================"
        echo " ⚠️  即将启动 LIVE 模式，将使用真实资金交易！"
        echo " 请确认已阅读 README.md 的《上线前检查清单》"
        echo "============================================================"
        read -r -p "确认启动？请输入 YES: " CONFIRM
        if [ "$CONFIRM" != "YES" ]; then
            echo "[INFO] 已取消。"
            exit 0
        fi
        export TRADING_MODE=live
        python main.py
        ;;
    backtest)
        python backtester.py
        ;;
    test)
        python -m unittest discover -s tests -v
        ;;
    auto)
        echo "[INFO] 按 .env 中的 TRADING_MODE 启动"
        python main.py
        ;;
    *)
        echo "[ERROR] 未知参数: $MODE"
        echo "用法: $0 [paper|live|backtest|test|auto]"
        exit 1
        ;;
esac
