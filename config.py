# ===================== config.py =====================
from __future__ import annotations
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"

class ExchangeName(str, Enum):
    OKX = "okx"
    BINANCE = "binance"

class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"

@dataclass(frozen=True)
class ExchangeConfig:
    name: ExchangeName = field(default_factory=lambda: ExchangeName(os.getenv("EXCHANGE_NAME", "okx").lower()))
    api_key: str = field(default_factory=lambda: os.getenv("EXCHANGE_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("EXCHANGE_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("EXCHANGE_API_PASSPHRASE", ""))
    use_testnet: bool = field(default_factory=lambda: os.getenv("EXCHANGE_TESTNET", "false").lower() == "true")

    def is_credentials_ready(self) -> bool:
        if self.name == ExchangeName.OKX:
            return bool(self.api_key and self.api_secret and self.api_passphrase)
        return bool(self.api_key and self.api_secret)

@dataclass(frozen=True)
class IndicatorConfig:
    ma_short_period: int = 5
    ma_mid_period: int = 20
    ma_long_period: int = 60
    timeframes: tuple[str, ...] = ("15m", "1h", "4h")
    kdj_n: int = 9
    kdj_k_smooth: int = 3
    kdj_d_smooth: int = 3
    kdj_overbought: float = 80.0
    kdj_oversold: float = 20.0
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    volume_change_window: int = 20

@dataclass(frozen=True)
class RiskConfig:
    leverage: int = 1
    # 风控升级：动态风险比例 (总分>=70得1.2%，55-69得0.8%)
    max_risk_per_trade_pct: float = 0.012
    min_risk_per_trade_pct: float = 0.008
    # 止损：基于4H结构失效点或 ATR×1.5
    atr_period: int = 14
    atr_multiplier: float = 1.5
    stop_loss_pct: float = 0.02 # 兜底百分比止损
    # 止盈：分批，1:1.5 平40-50%，剩余移动止损
    take_profit_ratio: float = 1.5
    partial_tp_ratio: float = 0.5 # 平仓50%
    # 熔断：单日2.5%，连亏3笔休息4小时
    max_daily_loss_pct: float = 0.025
    max_consecutive_losses: int = 3
    cooldown_hours: int = 4
    # 初始资金
    initial_paper_balance: float = 300.0
    equity_cap: float = 300.0
    order_retry_times: int = 3
    order_retry_backoff_sec: float = 1.5

@dataclass(frozen=True)
class TradingConfig:
    symbol: str = "STX/USDT:USDT"
    btc_symbol: str = "BTC/USDT:USDT"
    poll_interval_sec: int = 60
    ohlcv_limit: int = 200

@dataclass(frozen=True)
class AppConfig:
    mode: TradingMode = field(default_factory=lambda: TradingMode(os.getenv("TRADING_MODE", "paper").lower()))
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    indicator: IndicatorConfig = field(default_factory=IndicatorConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    def validate(self) -> None:
        if self.mode == TradingMode.LIVE and not self.exchange.is_credentials_ready():
            raise ValueError(f"Live 模式下 API 密钥不完整。")
        if self.risk.leverage != 1:
            raise ValueError("本系统强制要求 1 倍杠杆。")

LOG_FORMAT: Final[str] = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

def setup_logging(level: str = "INFO") -> logging.Logger:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level, format=LOG_FORMAT)
    logger = logging.getLogger("stx_quant")
    logger.setLevel(numeric_level)
    return logger

CONFIG: Final[AppConfig] = AppConfig()
