# ===================== executor.py =====================
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from config import AppConfig, CONFIG, PositionSide
from data_fetcher import MarketDataFetcher
from risk_manager import RiskManager
from strategy import SignalResult, StxStrategy

logger = logging.getLogger("stx_quant.executor")

@dataclass
class IterationSummary:
    timestamp: datetime
    price: float
    action: str
    side: str
    score: int
    detail: str
    equity: float

class TradingExecutor:
    def __init__(self, strategy: StxStrategy, risk_manager: RiskManager, data_fetcher: MarketDataFetcher, config: AppConfig = CONFIG):
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.data_fetcher = data_fetcher
        self.config = config

    def run_once(self) -> IterationSummary:
        now = datetime.now(timezone.utc)
        symbol = self.config.trading.symbol
        try:
            current_price = self.data_fetcher.fetch_last_price(symbol)
        except Exception as exc:
            return IterationSummary(now, 0.0, "error", "flat", 0, f"价格获取失败: {exc}", 0.0)

        if self.risk_manager.is_trading_halted():
            return IterationSummary(now, current_price, "halted", "flat", 0, self.risk_manager.get_halt_reason(), self.risk_manager.get_equity(current_price))

        triggered = self.risk_manager.check_and_trigger_stop(current_price)
        if triggered:
            return IterationSummary(now, current_price, f"exit_{triggered}", "flat", 0, f"触发 {triggered}，已平仓", self.risk_manager.get_equity(current_price))

        try:
            signal = self.strategy.generate_signal()
        except Exception as exc:
            return IterationSummary(now, current_price, "signal_error", "flat", 0, f"信号异常: {exc}", self.risk_manager.get_equity(current_price))

        if self.risk_manager.has_open_position():
            return IterationSummary(now, current_price, "hold", signal.side.value, signal.score, "已持仓，继续监控止损止盈", self.risk_manager.get_equity(current_price))

        if signal.side == PositionSide.FLAT:
            return IterationSummary(now, current_price, "wait", "flat", signal.score, signal.reasons[0] if signal.reasons else "观望", self.risk_manager.get_equity(current_price))

        opened = self.risk_manager.open_position(signal.side, current_price, signal.score)
        action = "opened" if opened else "open_failed"
        return IterationSummary(now, current_price, action, signal.side.value, signal.score, signal.reasons[0] if signal.reasons else "", self.risk_manager.get_equity(current_price))
