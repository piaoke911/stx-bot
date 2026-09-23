# ===================== backtester.py =====================
"""
backtester.py
==============
基于历史 K 线的简化回测器。

运行方式：
    python backtester.py

设计说明（务必阅读"回测局限性"）：
------------------------------------------------------------------
本回测器使用与实盘【完全相同的策略代码路径】：
    _BacktestStrategy 继承自 StxStrategy，仅重写 fetch_ohlcv_df（从内存切片而非网络拉取），
    以保证"回测信号"与"实盘信号"在代码层面一致。

因此，回测结果是策略逻辑在当前数据源上的诚实反映，
但**仍然与真实成交存在系统性偏差**，详见 BacktestResult.limitations。
------------------------------------------------------------------
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from config import CONFIG, AppConfig, PositionSide, setup_logging
from data_fetcher import MarketDataFetcher
from risk_manager import PaperAccount, TradeRecord
from strategy import StxStrategy

logger = logging.getLogger("stx_quant.backtester")


# ========================= 回测专用桩件 =========================

class _NullExchange:
    """
    回测专用空交易所桩件：
    回测中无法获得历史资金费率，统一返回 0（中性），避免污染 tokenomics 因子。
    """

    def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
        return {"fundingRate": 0.0}


class _BacktestStrategy(StxStrategy):
    """
    回测策略：
    继承实盘策略的全部信号计算逻辑，仅替换数据来源为内存中预加载的 DataFrame。
    时间窗口按"当前回测时间戳"切片，避免使用未来数据。
    """

    def __init__(self, config: AppConfig, data_store: dict[tuple[str, str], pd.DataFrame]) -> None:
        # 刻意不调用 super().__init__()：回测不创建真实网络连接
        self.config = config
        self.exchange = _NullExchange()          # type: ignore[assignment]
        self.indicator_cfg = config.indicator
        self.weight_cfg = config.weight
        self._data_store = data_store
        self._current_ts: Optional[pd.Timestamp] = None

    def set_current_time(self, ts: pd.Timestamp) -> None:
        """由回测主循环注入当前 bar 时间戳"""
        self._current_ts = ts

    def fetch_ohlcv_df(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """从内存切片，返回当前时间戳之前的 K 线（严格防未来数据泄露）"""
        key = (symbol, timeframe)
        if key not in self._data_store:
            raise ValueError(f"回测数据缺失: {key}")
        df = self._data_store[key]
        if self._current_ts is None:
            raise RuntimeError("回测策略未设置当前时间戳，禁止在未初始化时调用")
        sliced = df[df["timestamp"] <= self._current_ts].tail(limit)
        if len(sliced) < 2:
            raise ValueError(f"{key} 在 {self._current_ts} 之前有效数据不足")
        return sliced.reset_index(drop=True)


# ========================= 回测结果 =========================

@dataclass
class BacktestResult:
    """回测结果汇总"""
    initial_balance: float = 0.0
    final_balance: float = 0.0
    return_pct: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


# ========================= 回测器主体 =========================

class Backtester:
    """
    简化回测器：
    - 使用 1h 作为基准 bar，多周期信号通过时间戳切片对齐（不使用未来数据）。
    - 固定滑点（默认 5bp），不模拟深度与部分成交。
    - 未模拟资金费率支付、保证金变动、强平机制。
    """

    def __init__(
        self,
        config: AppConfig = CONFIG,
        fetcher: Optional[MarketDataFetcher] = None,
        warmup_bars: int = 200,
        slippage_pct: float = 0.0005,
        base_timeframe: str = "1h",
    ) -> None:
        self.config = config
        self.fetcher = fetcher or MarketDataFetcher(config)
        self.warmup_bars = warmup_bars
        self.slippage_pct = slippage_pct
        self.base_timeframe = base_timeframe

    # ---------------------- 对外入口 ----------------------

    def run(
        self,
        symbol: Optional[str] = None,
        data_limit: int = 1000,
    ) -> BacktestResult:
        """
        执行回测：
        - symbol: 回测标的，默认使用 config.trading.symbol
        - data_limit: 每个周期拉取的历史 K 线根数
        """
        symbol = symbol or self.config.trading.symbol
        btc_symbol = self.config.trading.btc_symbol

        logger.info("=" * 68)
        logger.info("回测开始 | 标的=%s | 基准周期=%s | 数据量=%d", symbol, self.base_timeframe, data_limit)
        logger.info("=" * 68)

        # 1. 预加载数据
        data_store: dict[tuple[str, str], pd.DataFrame] = {}
        for sym in (symbol, btc_symbol):
            for tf in self.config.indicator.timeframes:
                try:
                    df = self.fetcher.fetch_ohlcv(sym, tf, limit=data_limit)
                    data_store[(sym, tf)] = df
                    logger.info("已加载 %-24s %-4s 共 %d 根", sym, tf, len(df))
                except Exception as exc:  # noqa: BLE001
                    logger.error("加载 %s %s 失败: %s", sym, tf, exc)

        base_key = (symbol, self.base_timeframe)
        if base_key not in data_store:
            raise RuntimeError(f"缺少基准周期数据: {base_key}")
        base_df = data_store[base_key]
        if len(base_df) <= self.warmup_bars + 10:
            raise RuntimeError(
                f"基准数据不足（{len(base_df)} 根），至少需要 {self.warmup_bars + 10} 根"
            )

        # 2. 初始化策略与模拟账户
        strategy = _BacktestStrategy(self.config, data_store)
        account = PaperAccount(initial_balance=self.config.risk.initial_paper_balance)

        equity_curve: list[tuple[pd.Timestamp, float]] = []
        peak_equity: float = account.balance
        max_dd: float = 0.0

        # 3. 主循环
        for i in range(self.warmup_bars, len(base_df)):
            bar = base_df.iloc[i]
            ts = bar["timestamp"]
            price = float(bar["close"])
            strategy.set_current_time(ts)

            # 3.1 止损/止盈检查
            if account.position is not None:
                pos = account.position
                exit_reason: Optional[str] = None
                if pos.side == PositionSide.LONG:
                    if price <= pos.stop_loss_price:
                        exit_reason = "stop_loss"
                    elif price >= pos.take_profit_price:
                        exit_reason = "take_profit"
                elif pos.side == PositionSide.SHORT:
                    if price >= pos.stop_loss_price:
                        exit_reason = "stop_loss"
                    elif price <= pos.take_profit_price:
                        exit_reason = "take_profit"

                if exit_reason is not None:
                    # 平仓滑点：多头以略低价卖出，空头以略高价买回
                    exit_price = (
                        price * (1 - self.slippage_pct)
                        if pos.side == PositionSide.LONG
                        else price * (1 + self.slippage_pct)
                    )
                    account.close_position(exit_price, reason=exit_reason)

            # 3.2 无持仓 → 生成信号 → 尝试开仓
            if account.position is None:
                try:
                    signal = strategy.generate_signal()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("回测信号计算异常（跳过该 bar）: %s", exc)
                    signal = None

                if signal is not None and signal.side != PositionSide.FLAT:
                    size, sl, tp = self._calc_size(account, price, signal.side)
                    if size > 0:
                        # 开仓滑点：多头以略高价买入，空头以略低价卖出
                        entry_price = (
                            price * (1 + self.slippage_pct)
                            if signal.side == PositionSide.LONG
                            else price * (1 - self.slippage_pct)
                        )
                        # 用实际成交价重算止损止盈
                        if signal.side == PositionSide.LONG:
                            sl = entry_price * (1 - self.config.risk.stop_loss_pct)
                            tp = entry_price * (1 + self.config.risk.take_profit_pct)
                        else:
                            sl = entry_price * (1 + self.config.risk.stop_loss_pct)
                            tp = entry_price * (1 - self.config.risk.take_profit_pct)
                        try:
                            account.open_position(signal.side, size, entry_price, sl, tp)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("回测开仓失败: %s", exc)

            # 3.3 记录权益
            equity = account.equity(price)
            equity_curve.append((ts, equity))
            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        # 4. 回测结束强制平仓（使用最后一根 bar 收盘价）
        if account.position is not None:
            last_price = float(base_df.iloc[-1]["close"])
            pos = account.position
            exit_price = (
                last_price * (1 - self.slippage_pct)
                if pos.side == PositionSide.LONG
                else last_price * (1 + self.slippage_pct)
            )
            account.close_position(exit_price, reason="backtest_end")

        # 5. 汇总结果
        stats = account.get_statistics()
        initial = self.config.risk.initial_paper_balance
        final = account.balance
        result = BacktestResult(
            initial_balance=initial,
            final_balance=final,
            return_pct=(final - initial) / initial if initial > 0 else 0.0,
            total_trades=int(stats["total_trades"]),
            win_rate=stats["win_rate"],
            total_pnl=stats["total_pnl"],
            avg_pnl=stats["avg_pnl"],
            max_drawdown_pct=max_dd,
            trades=account.trade_history,
            equity_curve=equity_curve,
            limitations=self._limitations(),
        )

        self._print_report(result)
        return result

    # ---------------------- 内部工具 ----------------------

    def _calc_size(
        self, account: PaperAccount, price: float, side: PositionSide,
    ) -> tuple[float, float, float]:
        """
        仓位计算，与 RiskManager.calculate_position_size 保持同样的公式：
        风险金额 / 止损距离，并与 max_position_pct 约束取较小值。
        （此处不直接复用 RiskManager 是为了避免引入 Live 交易所依赖）
        """
        equity = account.balance
        if equity <= 0 or price <= 0:
            return 0.0, 0.0, 0.0

        stop_distance = price * self.config.risk.stop_loss_pct
        risk_amount = equity * self.config.risk.risk_per_trade_pct
        size_by_risk = risk_amount / stop_distance if stop_distance > 0 else 0.0
        size_by_cap = (equity * self.config.risk.max_position_pct) / price
        size = max(min(size_by_risk, size_by_cap), 0.0)

        if side == PositionSide.LONG:
            sl = price * (1 - self.config.risk.stop_loss_pct)
            tp = price * (1 + self.config.risk.take_profit_pct)
        elif side == PositionSide.SHORT:
            sl = price * (1 + self.config.risk.stop_loss_pct)
            tp = price * (1 - self.config.risk.take_profit_pct)
        else:
            return 0.0, 0.0, 0.0
        return size, sl, tp

    @staticmethod
    def _limitations() -> list[str]:
        """
        诚实标注本回测器的所有已知局限。
        这些限制会导致回测结果【系统性偏乐观或偏悲观】，使用者不应将回测收益
        等同于实盘收益，更不能据此直接切换到 Live 模式。
        """
        return [
            "【成交价近似】按 1h 收盘价 + 固定滑点成交，未模拟盘口深度、部分成交或极端行情下的流动性枯竭。",
            "【资金费率缺失】回测未扣除永续合约资金费率，长期持仓的实际盈亏会明显偏离结果。",
            "【盘中极值忽略】止损/止盈仅按 bar 收盘价判定，无法模拟 bar 内瞬间插针触发止损后立刻回撤的情形（会低估被止损概率）。",
            "【周期对齐近似】多周期（15m/1h/4h）信号按时间戳切片对齐，用 1h 作为基准 bar 会忽略 15m 级别的噪声波动。",
            "【链上/事件缺失】因子4/6/8（协议升级、宏观、竞争）当前权重预留为 0，未纳入任何事件驱动逻辑。",
            "【保证金模型简化】Paper 账户不模拟保证金占用、维持保证金、强平价格与自动减仓（ADL）。",
            "【数据量有限】默认仅拉取约 1000 根 K 线，无法覆盖完整牛熊周期，存在过拟合于近期行情的风险。",
            "【无网络/故障模拟】未模拟交易所 API 限流、网络中断、下单失败等实盘常见异常场景。",
            "【非未来收益预测】回测结果基于历史数据，不构成对未来收益的任何承诺或暗示。",
        ]

    @staticmethod
    def _print_report(result: BacktestResult) -> None:
        logger.info("=" * 68)
        logger.info("回测结果")
        logger.info("=" * 68)
        logger.info("初始资金:      %.2f USDT", result.initial_balance)
        logger.info("最终资金:      %.2f USDT", result.final_balance)
        logger.info("总收益率:      %.2f%%", result.return_pct * 100)
        logger.info("总交易次数:    %d", result.total_trades)
        logger.info("胜率:          %.2f%%", result.win_rate * 100)
        logger.info("总盈亏:        %.2f USDT", result.total_pnl)
        logger.info("平均每笔盈亏:  %.4f USDT", result.avg_pnl)
        logger.info("最大回撤:      %.2f%%", result.max_drawdown_pct * 100)
        logger.info("-" * 68)
        logger.info("【回测局限性说明 — 强烈建议完整阅读】")
        for i, lim in enumerate(result.limitations, 1):
            logger.info("  %d. %s", i, lim)
        logger.info("=" * 68)


# ========================= 独立运行入口 =========================

if __name__ == "__main__":
    setup_logging(CONFIG.log_level)
    try:
        backtester = Backtester()
        backtester.run(data_limit=1000)
    except KeyboardInterrupt:
        logger.warning("回测被用户中断")
    except Exception as exc:  # noqa: BLE001
        logger.exception("回测执行失败: %s", exc)
