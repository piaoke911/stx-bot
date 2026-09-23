# ===================== main.py（集成 P0/P1 版本） =====================
"""
main.py
========
STX/USDT 永续合约量化交易系统主入口（集成 P0/P1 增强）。
"""

from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import logging
import os
import signal
import sys
import threading
from typing import Optional

try:
    import ccxt  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise ImportError("请先安装 ccxt: pip install ccxt") from exc

from config import CONFIG, TradingMode
from logging_setup import setup_logging
from data_fetcher import MarketDataFetcher
from executor import TradingExecutor
from risk_manager import RiskManager
from strategy import StxStrategy, create_exchange

# [NEW] 可选增强组件
try:
    from precision_utils import PrecisionUtils
except ImportError:
    PrecisionUtils = None  # type: ignore[assignment]

try:
    from state_store import StateStore
except ImportError:
    StateStore = None  # type: ignore[assignment]

try:
    from notifier import Notifier
except ImportError:
    Notifier = None  # type: ignore[assignment]

logger = logging.getLogger("stx_quant.main")


def _print_banner(logger: logging.Logger) -> None:
    logger.info("=" * 72)
    logger.info("STX/USDT 永续合约交易机器人")
    logger.info("  运行模式  : %s", CONFIG.mode.value.upper())
    logger.info("  交易所    : %s", CONFIG.exchange.name.value)
    logger.info("  测试网    : %s", "YES" if CONFIG.exchange.use_testnet else "NO")
    logger.info("  交易标的  : %s", CONFIG.trading.symbol)
    logger.info("  BTC 参照  : %s", CONFIG.trading.btc_symbol)
    logger.info("  杠杆      : %dx (系统强制)", CONFIG.risk.leverage)
    logger.info("  风险比例  : %.2f%% - %.2f%% / 笔", CONFIG.risk.min_risk_per_trade_pct * 100, CONFIG.risk.max_risk_per_trade_pct * 100)
    logger.info("  止损/止盈 : ATR x %.1f / 盈亏比 1:%.1f", CONFIG.risk.atr_multiplier, CONFIG.risk.take_profit_ratio)
    logger.info("  轮询间隔  : %d 秒", CONFIG.trading.poll_interval_sec)
    logger.info("=" * 72)


def _build_components() -> tuple[
    Optional[StxStrategy], Optional[RiskManager],
    Optional[MarketDataFetcher], Optional[TradingExecutor],
    Optional[StateStore], Optional[Notifier],
]:
    live_exchange: Optional[ccxt.Exchange] = None

    # [NEW] 状态持久化
    state_store: Optional[StateStore] = None
    if StateStore is not None:
        try:
            db_path = os.getenv("STATE_DB_PATH", "data/stx_bot.db")
            state_store = StateStore(db_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("初始化 StateStore 失败（继续运行，无持久化）: %s", exc)

    # [NEW] 告警
    notifier: Optional[Notifier] = None
    if Notifier is not None:
        try:
            enabled = os.getenv("NOTIFY_ENABLED", "true").lower() != "false"
            notifier = Notifier(enabled=enabled)
        except Exception as exc:  # noqa: BLE001
            logger.error("初始化 Notifier 失败（继续运行，无告警）: %s", exc)

    # Live 模式：创建鉴权交易所
    if CONFIG.mode == TradingMode.LIVE:
        logger.warning("⚠️  Live 模式已启用，将使用真实资金进行交易！")
        if CONFIG.exchange.use_testnet:
            logger.warning("⚠️  测试网模式已开启（EXCHANGE_TESTNET=true），不下真实订单")
        try:
            live_exchange = create_exchange(CONFIG, need_auth=True)
            logger.info("Live 交易所实例创建成功")
        except Exception as exc:  # noqa: BLE001
            logger.exception("创建 Live 交易所实例失败: %s", exc)
            return None, None, None, None, state_store, notifier

    # 策略
    try:
        strategy = StxStrategy(CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.exception("策略初始化失败: %s", exc)
        return None, None, None, None, state_store, notifier

    # [NEW] 精度工具：复用策略层已创建的无鉴权交易所实例加载市场元数据
    precision = None
    if PrecisionUtils is not None:
        try:
            # 优先使用策略层的公开交易所实例（一定有网络能力）
            precision = PrecisionUtils(strategy.exchange, CONFIG.trading.symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("初始化 PrecisionUtils 失败: %s", exc)

    # 风控（注入新组件）
    try:
        risk_manager = RiskManager(
            CONFIG,
            live_exchange=live_exchange,
            state_store=state_store,
            notifier=notifier,
            precision=precision,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("风险管理器初始化失败: %s", exc)
        return None, None, None, None, state_store, notifier

    # 数据
    try:
        data_fetcher = MarketDataFetcher(CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.exception("数据拉取器初始化失败: %s", exc)
        return None, None, None, None, state_store, notifier

    executor = TradingExecutor(strategy, risk_manager, data_fetcher, CONFIG)
    return strategy, risk_manager, data_fetcher, executor, state_store, notifier


def main() -> int:
    # 使用新的日志配置（文件 + 控制台）
    setup_logging(CONFIG.log_level)

    try:
        CONFIG.validate()
    except ValueError as exc:
        logger.error("配置校验失败: %s", exc)
        return 1

    _print_banner(logger)

    strategy, risk_manager, data_fetcher, executor, state_store, notifier = _build_components()
    if executor is None or risk_manager is None or data_fetcher is None:
        logger.error("组件初始化失败，程序退出")
        if notifier:
            notifier.send("启动失败：组件初始化异常，请检查日志。", level="ERROR")
        return 1

    # 启动通知
    if notifier and os.getenv("NOTIFY_ON_STARTUP", "true").lower() != "false":
        notifier.send(
            f"STX Bot 已启动 | 模式={CONFIG.mode.value.upper()} | "
            f"交易所={CONFIG.exchange.name.value} | 标的={CONFIG.trading.symbol}",
            level="INFO",
        )

    # 优雅退出
    stop_event = threading.Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("收到系统信号 %s，准备优雅退出", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    iteration = 0
    logger.info("主循环启动，按 Ctrl+C 可优雅退出")
    while not stop_event.is_set():
        iteration += 1
        try:
            summary = executor.run_once()
            logger.info(
                "[#%d] 价格=%.4f | 动作=%-16s | 方向=%-5s | 得分=%+.3f | 权益=%.2f | %s",
                iteration, summary.price, summary.action, summary.side,
                summary.score, summary.equity, summary.detail,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[#%d] 迭代执行异常（继续运行）: %s", iteration, exc)
            if notifier and iteration % 10 == 0:
                # 避免告警风暴：每 10 轮异常才发一次
                notifier.send(f"主循环异常（第 {iteration} 轮）：{exc}", level="ERROR")

        stop_event.wait(CONFIG.trading.poll_interval_sec)

    # 退出统计
    logger.info("主循环已退出，正在生成最终统计...")
    try:
        stats = risk_manager.get_statistics()
        logger.info(
            "统计 | 总交易=%d | 胜率=%.2f%% | 总盈亏=%.2f USDT | 平均每笔=%.4f USDT",
            int(stats.get("total_trades", 0)),
            stats.get("win_rate", 0.0) * 100,
            stats.get("total_pnl", 0.0),
            stats.get("avg_pnl", 0.0),
        )
        if state_store is not None:
            agg = state_store.aggregate_stats()
            logger.info(
                "持久化累计统计 | 总交易=%d | 胜率=%.2f%% | 总盈亏=%.2f",
                agg["total_trades"], agg["win_rate"] * 100, agg["total_pnl"],
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("获取统计数据失败: %s", exc)

    if notifier:
        try:
            notifier.send("STX Bot 已优雅退出。", level="INFO")
        except Exception:
            pass

    try:
        data_fetcher.close()
    except Exception:
        pass

    logger.info("STX 交易机器人已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
