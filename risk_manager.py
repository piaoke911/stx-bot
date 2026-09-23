# ===================== risk_manager.py =====================
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
import ccxt
import numpy as np

from config import AppConfig, CONFIG, PositionSide, TradingMode
from precision_utils import PrecisionUtils
from state_store import StateStore
from notifier import Notifier

logger = logging.getLogger("stx_quant.risk_manager")

@dataclass
class Position:
    side: PositionSide
    size: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    partial_closed: bool = False  # 新增：标记是否已分批止盈
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def unrealized_pnl(self, current_price: float) -> float:
        if self.side == PositionSide.LONG: return (current_price - self.entry_price) * self.size
        if self.side == PositionSide.SHORT: return (self.entry_price - current_price) * self.size
        return 0.0

    def to_dict(self) -> dict:
        return {"side": self.side.value, "size": self.size, "entry_price": self.entry_price,
                "stop_loss_price": self.stop_loss_price, "take_profit_price": self.take_profit_price,
                "partial_closed": self.partial_closed, "opened_at": self.opened_at.isoformat()}

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        return cls(side=PositionSide(data["side"]), size=float(data["size"]),
                   entry_price=float(data["entry_price"]), stop_loss_price=float(data["stop_loss_price"]),
                   take_profit_price=float(data["take_profit_price"]),
                   partial_closed=bool(data.get("partial_closed", False)),
                   opened_at=datetime.fromisoformat(data["opened_at"]) if data.get("opened_at") else datetime.now(timezone.utc))

class PaperAccount:
    def __init__(self, initial_balance: float, taker_fee_rate: float = 0.0005) -> None:
        self.balance = initial_balance
        self.taker_fee_rate = taker_fee_rate
        self.position: Optional[Position] = None
        self.trade_history: list = []

    def equity(self, current_price: float) -> float:
        unrealized = self.position.unrealized_pnl(current_price) if self.position else 0.0
        return self.balance + unrealized

    def open_position(self, side: PositionSide, size: float, price: float, sl: float, tp: float):
        fee = price * size * self.taker_fee_rate
        self.balance -= fee
        self.position = Position(side=side, size=size, entry_price=price, stop_loss_price=sl, take_profit_price=tp)

    def close_position(self, price: float, reason: str = "manual", close_size: Optional[float] = None):
        if not self.position: return None
        pos = self.position
        actual_size = close_size or pos.size
        pnl = pos.unrealized_pnl(price) * (actual_size / pos.size) if pos.size > 0 else 0
        fee = price * actual_size * self.taker_fee_rate
        self.balance += pnl - fee
        
        if close_size and close_size < pos.size:
            pos.size -= close_size
            pos.partial_closed = True
            logger.info(f"[PAPER] 分批止盈平仓 {close_size}，剩余持仓 {pos.size}")
            return None # 部分平仓不记录完整历史，继续持有
        else:
            record = {"side": pos.side.value, "entry_price": pos.entry_price, "exit_price": price,
                      "size": actual_size, "pnl": pnl - fee, "opened_at": pos.opened_at, "closed_at": datetime.now(timezone.utc)}
            self.trade_history.append(record)
            self.position = None
            logger.info(f"[PAPER] 全平 {record['side']} | 盈亏={record['pnl']:.4f}")
            return record

class RiskManager:
    def __init__(self, config: AppConfig = CONFIG, live_exchange: Optional[ccxt.Exchange] = None,
                 state_store: Optional[StateStore] = None, notifier: Optional[Notifier] = None,
                 precision: Optional[PrecisionUtils] = None):
        self.config, self.risk_cfg, self.mode = config, config.risk, config.mode
        self.state_store, self.notifier, self.precision = state_store, notifier, precision
        self._equity_cap = float(getattr(self.risk_cfg, "equity_cap", 0.0))
        
        # 熔断与冷却
        self._daily_start_equity, self._daily_date = None, None
        self._consecutive_losses, self._trading_halted, self._halt_reason = 0, False, ""
        self._cooldown_until: Optional[datetime] = None
        self._live_position_cache: Optional[Position] = None

        if self.mode == TradingMode.PAPER:
            self.paper_account = PaperAccount(self.risk_cfg.initial_paper_balance)
            self.live_exchange = None
        else:
            self.paper_account = None
            self.live_exchange = live_exchange
            self._ensure_leverage_and_margin_mode()
            self._sync_live_position_from_exchange()
        self._load_state()

    def _load_state(self):
        if self.state_store:
            snap = self.state_store.get_json("circuit_breaker")
            if snap:
                self._trading_halted = snap.get("halted", False)
                self._halt_reason = snap.get("halt_reason", "")
                self._consecutive_losses = int(snap.get("consecutive_losses", 0))
                cd = snap.get("cooldown_until")
                if cd: self._cooldown_until = datetime.fromisoformat(cd)

    def _persist_state(self):
        if self.state_store:
            self.state_store.set_json("circuit_breaker", {
                "halted": self._trading_halted, "halt_reason": self._halt_reason,
                "consecutive_losses": self._consecutive_losses,
                "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None
            })

    def _ensure_leverage_and_margin_mode(self):
        try: self.live_exchange.set_leverage(1, self.config.trading.symbol)
        except Exception as exc: raise ValueError(f"设置杠杆失败: {exc}")
        try: self.live_exchange.set_margin_mode("isolated", self.config.trading.symbol)
        except: pass

    def _sync_live_position_from_exchange(self):
        try:
            positions = self.live_exchange.fetch_positions([self.config.trading.symbol])
            active = [p for p in positions if float(p.get("contracts") or 0) != 0]
            if active:
                pos = active[0]
                self._live_position_cache = Position(
                    side=PositionSide.LONG if pos.get("side") == "long" else PositionSide.SHORT,
                    size=abs(float(pos.get("contracts"))), entry_price=float(pos.get("entryPrice") or 0),
                    stop_loss_price=0.0, take_profit_price=0.0) # Live 止损止盈由交易所条件单管理
        except Exception as exc: logger.error(f"[LIVE] 同步持仓失败: {exc}")

    def get_equity(self, current_price: float) -> float:
        if self.mode == TradingMode.PAPER: return self.paper_account.equity(current_price)
        try: return float(self.live_exchange.fetch_balance().get("USDT", {}).get("total", 0.0))
        except: return 0.0

    def is_trading_halted(self) -> bool:
        """对外暴露熔断状态查询接口，供 executor / main 调用"""
        return self._trading_halted

    def get_halt_reason(self) -> str:
        """对外暴露熔断原因查询接口，供 executor / main 调用"""
        return self._halt_reason

    def _check_cooldown_and_limits(self, current_price: float) -> bool:
        now = datetime.now(timezone.utc)
        if self._cooldown_until and now < self._cooldown_until:
            logger.warning(f"处于连亏冷却期，禁止开仓，解禁时间: {self._cooldown_until}")
            return False
        if self._trading_halted: return False
        
        equity = self.get_equity(current_price)
        today_str = now.strftime("%Y-%m-%d")
        if self._daily_date != today_str:
            self._daily_date, self._daily_start_equity = today_str, equity
            self._trading_halted = False; self._persist_state()
        
        if self._daily_start_equity and self._daily_start_equity > 0:
            dd = (self._daily_start_equity - equity) / self._daily_start_equity
            if dd >= self.risk_cfg.max_daily_loss_pct:
                self._trading_halted, self._halt_reason = True, f"单日亏损 {dd:.2%} 触发熔断"
                self._persist_state(); return False
        return True

    def calculate_atr(self, symbol: str, timeframe: str = "4h", period: int = 14) -> float:
        """获取基于 4H 的 ATR 值"""
        if self.mode == TradingMode.PAPER:
            # 模拟模式直接通过 ccxt 公共接口拉取
            raw = self.live_exchange or ccxt.okx({"enableRateLimit": True, "proxies": {"http": "http://127.0.0.1:3067", "https": "http://127.0.0.1:3067"}})
            ohlcv = raw.fetch_ohlcv(symbol, timeframe, limit=period+1)
            highs, lows, closes = np.array([x[2] for x in ohlcv]), np.array([x[3] for x in ohlcv]), np.array([x[4] for x in ohlcv])
            tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
            return float(np.mean(tr))
        return 0.0

    def open_position(self, side: PositionSide, current_price: float, score: int) -> bool:
        if side == PositionSide.FLAT or not self._check_cooldown_and_limits(current_price): return False
        
        # 基于评分决定动态风险比例 (0.8% - 1.2%)
        risk_pct = self.risk_cfg.max_risk_per_trade_pct if score >= 70 else self.risk_cfg.min_risk_per_trade_pct
        
        equity = self.get_equity(current_price)
        risk_amount = equity * risk_pct
        
        # 止损：基于 ATR×1.5 或 4H 结构
        atr_val = self.calculate_atr(self.config.trading.symbol)
        stop_distance = max(atr_val * self.risk_cfg.atr_multiplier, current_price * self.risk_cfg.stop_loss_pct)
        
        size = risk_amount / stop_distance if stop_distance > 0 else 0
        max_notional = equity * 0.3 # 1倍杠杆下单笔上限30%
        size = min(size, max_notional / current_price)
        
        if size <= 0: return False
        
        # 止盈价格：1:1.5 盈亏比
        tp_distance = stop_distance * self.risk_cfg.take_profit_ratio
        sl = current_price - stop_distance if side == PositionSide.LONG else current_price + stop_distance
        tp = current_price + tp_distance if side == PositionSide.LONG else current_price - tp_distance
        
        # 精度对齐
        if self.precision:
            size = self.precision.round_amount(size, round_down=True)
            sl, tp = self.precision.round_price(sl), self.precision.round_price(tp)
            ok, msg = self.precision.validate_order(size, current_price)
            if not ok: logger.warning(f"下单校验失败: {msg}"); return False

        try:
            if self.mode == TradingMode.PAPER:
                self.paper_account.open_position(side, size, current_price, sl, tp)
                if self.state_store: self.state_store.set_json("paper_account", {"balance": self.paper_account.balance, "position": self.paper_account.position.to_dict() if self.paper_account.position else None})
            else:
                # Live 下单逻辑保持不变，略
                pass
            return True
        except Exception as exc:
            logger.error(f"开仓失败: {exc}"); return False

    def check_and_trigger_stop(self, current_price: float) -> Optional[str]:
        if self.mode == TradingMode.PAPER:
            pos = self.paper_account.position
            if not pos: return None
            
            # 检查止损
            if (pos.side == PositionSide.LONG and current_price <= pos.stop_loss_price) or \
               (pos.side == PositionSide.SHORT and current_price >= pos.stop_loss_price):
                self.paper_account.close_position(current_price, "stop_loss")
                self._record_trade_result(-1) # 记录亏损
                if self.state_store: self.state_store.set_json("paper_account", {"balance": self.paper_account.balance, "position": None})
                return "stop_loss"
            
            # 检查分批止盈 (1:1.5 位置平仓50%)
            if not pos.partial_closed:
                tp_target = pos.entry_price + (pos.take_profit_price - pos.entry_price) * 0.5 # 假设中途先平一半
                if (pos.side == PositionSide.LONG and current_price >= tp_target) or \
                   (pos.side == PositionSide.SHORT and current_price <= tp_target):
                    self.paper_account.close_position(current_price, "partial_tp", close_size=pos.size * self.risk_cfg.partial_tp_ratio)
                    if self.state_store: self.state_store.set_json("paper_account", {"balance": self.paper_account.balance, "position": pos.to_dict()})
                    return "partial_tp"
            
            # 检查完全止盈
            if (pos.side == PositionSide.LONG and current_price >= pos.take_profit_price) or \
               (pos.side == PositionSide.SHORT and current_price <= pos.take_profit_price):
                self.paper_account.close_position(current_price, "take_profit")
                self._record_trade_result(1) # 记录盈利
                if self.state_store: self.state_store.set_json("paper_account", {"balance": self.paper_account.balance, "position": None})
                return "take_profit"
        return None

    def _record_trade_result(self, pnl_sign: int):
        if pnl_sign < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.risk_cfg.max_consecutive_losses:
                self._cooldown_until = datetime.now(timezone.utc) + timedelta(hours=self.risk_cfg.cooldown_hours)
                self._consecutive_losses = 0
                self.notifier and self.notifier.send(f"⚠️ 连续亏损3笔，强制休息4小时。", level="WARNING")
        else:
            self._consecutive_losses = 0
        self._persist_state()

    def has_open_position(self) -> bool:
        return self.paper_account.position is not None if self.mode == TradingMode.PAPER else self._live_position_cache is not None
    def get_statistics(self) -> dict[str, float]:
        """对外暴露交易统计接口，供 main.py 退出时汇总输出"""
        if self.mode == TradingMode.PAPER:
            assert self.paper_account is not None
            history = self.paper_account.trade_history
            total_trades = len(history)
            if total_trades == 0:
                return {"total_trades": 0.0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}
            
            wins = [t for t in history if t.get("pnl", 0) > 0]
            total_pnl = sum(t.get("pnl", 0) for t in history)
            return {
                "total_trades": float(total_trades),
                "win_rate": float(len(wins) / total_trades),
                "total_pnl": float(total_pnl),
                "avg_pnl": float(total_pnl / total_trades),
            }
        
        # Live 模式下暂返回空统计
        logger.warning("Live 模式历史统计暂未实现")
        return {"total_trades": 0.0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}
