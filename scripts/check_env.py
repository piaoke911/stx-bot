# ===================== scripts/check_env.py =====================
"""
scripts/check_env.py
====================
上线前环境自检脚本。

用法：
    python scripts/check_env.py

检查项：
1. Python 版本
2. 依赖包是否齐全
3. .env 是否存在
4. Live 模式密钥是否完整
5. 交易所连通性与市场元数据加载
6. 账户杠杆 / 持仓一致性检查（Live 模式）
7. 磁盘与日志目录权限
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# 让脚本能 import 项目根目录模块
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    print("[WARN] 未安装 python-dotenv，跳过 .env 加载")


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}[PASS]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"{RED}[FAIL]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{RESET} {msg}")


def check_python() -> bool:
    v = sys.version_info
    if v.major == 3 and v.minor >= 10:
        ok(f"Python 版本 {v.major}.{v.minor}.{v.micro}")
        return True
    fail(f"Python 版本过低: {v.major}.{v.minor}，需要 >= 3.10")
    return False


def check_deps() -> bool:
    missing = []
    for pkg in ("ccxt", "pandas", "numpy"):
        try:
            __import__(pkg)
            ok(f"依赖 {pkg} 已安装")
        except ImportError:
            fail(f"依赖 {pkg} 未安装")
            missing.append(pkg)
    return not missing


def check_env_file() -> bool:
    env_path = ROOT / ".env"
    if env_path.exists():
        ok(f".env 存在: {env_path}")
        return True
    warn(f".env 不存在: {env_path}（可复制 .env.example）")
    return False


def check_live_credentials() -> bool:
    mode = os.getenv("TRADING_MODE", "paper").lower()
    if mode != "live":
        ok("当前为 Paper 模式，无需密钥")
        return True

    exch = os.getenv("EXCHANGE_NAME", "okx").lower()
    key = os.getenv("EXCHANGE_API_KEY", "")
    secret = os.getenv("EXCHANGE_API_SECRET", "")
    passphrase = os.getenv("EXCHANGE_API_PASSPHRASE", "")

    ok_flag = True
    if not key:
        fail("EXCHANGE_API_KEY 未设置"); ok_flag = False
    if not secret:
        fail("EXCHANGE_API_SECRET 未设置"); ok_flag = False
    if exch == "okx" and not passphrase:
        fail("OKX 需要 EXCHANGE_API_PASSPHRASE"); ok_flag = False
    if ok_flag:
        ok(f"Live 模式密钥完整 (交易所={exch})")
    return ok_flag


def check_exchange_connectivity() -> bool:
    try:
        from config import CONFIG
        from strategy import create_exchange
        ex = create_exchange(CONFIG, need_auth=False)
        ex.load_markets()
        market = ex.market(CONFIG.trading.symbol)
        ok(f"交易所 {CONFIG.exchange.name.value} 连通，市场 {CONFIG.trading.symbol} 存在")
        limits = market.get("limits", {})
        print(f"       最小数量 = {limits.get('amount', {}).get('min')}")
        print(f"       最小名义 = {limits.get('cost', {}).get('min')}")
        # 拉一次 ticker 验证
        t = ex.fetch_ticker(CONFIG.trading.symbol)
        print(f"       最新价   = {t.get('last')}")
        return True
    except Exception as exc:
        fail(f"交易所连通性检查失败: {exc}")
        traceback.print_exc()
        return False


def check_live_account() -> bool:
    mode = os.getenv("TRADING_MODE", "paper").lower()
    if mode != "live":
        warn("非 Live 模式，跳过账户检查")
        return True
    try:
        from config import CONFIG
        from strategy import create_exchange
        ex = create_exchange(CONFIG, need_auth=True)
        bal = ex.fetch_balance()
        usdt = bal.get("USDT", {}).get("total", 0.0)
        print(f"       USDT 总权益: {usdt}")
        positions = ex.fetch_positions([CONFIG.trading.symbol])
        active = [p for p in positions if float(p.get("contracts") or 0) != 0]
        if active:
            warn(f"⚠️ 检测到已有持仓 {len(active)} 个，请确认后启动：{active}")
        else:
            ok("当前无持仓")
        return True
    except Exception as exc:
        fail(f"Live 账户检查失败: {exc}")
        return False


def check_dirs() -> bool:
    try:
        (ROOT / "data").mkdir(exist_ok=True)
        (ROOT / "logs").mkdir(exist_ok=True)
        ok("data/ 与 logs/ 目录就绪")
        return True
    except Exception as exc:
        fail(f"目录创建失败: {exc}")
        return False


def main() -> int:
    print("=" * 60)
    print(" STX Bot 上线前自检")
    print("=" * 60)

    results = [
        ("Python 版本", check_python()),
        ("依赖包", check_deps()),
        (".env 文件", check_env_file()),
        ("Live 密钥", check_live_credentials()),
        ("目录权限", check_dirs()),
        ("交易所连通性", check_exchange_connectivity()),
        ("Live 账户", check_live_account()),
    ]

    print("=" * 60)
    failed = [name for name, passed in results if not passed]
    if failed:
        print(f"{RED}自检未通过：{', '.join(failed)}{RESET}")
        return 1
    print(f"{GREEN}全部检查通过，可以启动。{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
