# ===================== tests/test_risk_manager.py =====================
"""风控层单元测试（Paper 模式，无需网络）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CONFIG, PositionSide, TradingMode
from risk_manager import PaperAccount, Position, RiskManager


class TestPaperAccount(unittest.TestCase):
    def test_initial_balance(self) -> None:
        acc = PaperAccount(initial_balance=10_000.0)
        self.assertEqual(acc.balance, 10_000.0)
        self.assertIsNone(acc.position)

    def test_open_close_long_profit(self) -> None:
        acc = PaperAccount(initial_balance=10_000.0, taker_fee_rate=0.0)
        acc.open_position(PositionSide.LONG, 100.0, 1.0, 0.98, 1.04)
        self.assertIsNotNone(acc.position)
        rec = acc.close_position(1.04, reason="test")
        self.assertIsNotNone(rec)
        self.assertAlmostEqual(rec.pnl, 4.0, places=6)
        self.assertAlmostEqual(acc.balance, 10_004.0, places=6)

    def test_open_close_short_profit(self) -> None:
        acc = PaperAccount(initial_balance=10_000.0, taker_fee_rate=0.0)
        acc.open_position(PositionSide.SHORT, 100.0, 1.0, 1.02, 0.96)
        rec = acc.close_position(0.96, reason="test")
        self.assertAlmostEqual(rec.pnl, 4.0, places=6)

    def test_double_open_raises(self) -> None:
        acc = PaperAccount(initial_balance=10_000.0)
        acc.open_position(PositionSide.LONG, 1.0, 1.0, 0.98, 1.04)
        with self.assertRaises(RuntimeError):
            acc.open_position(PositionSide.LONG, 1.0, 1.0, 0.98, 1.04)


class TestPositionSerialization(unittest.TestCase):
    def test_round_trip(self) -> None:
        p = Position(PositionSide.LONG, 12.5, 1.23, 1.20, 1.28)
        data = p.to_dict()
        p2 = Position.from_dict(data)
        self.assertEqual(p2.side, p.side)
        self.assertAlmostEqual(p2.size, p.size)
        self.assertAlmostEqual(p2.entry_price, p.entry_price)


class TestRiskManagerPaper(unittest.TestCase):
    def _rm(self) -> RiskManager:
        return RiskManager(CONFIG)

    def test_initial_state(self) -> None:
        rm = self._rm()
        self.assertFalse(rm.is_trading_halted())
        self.assertFalse(rm.has_open_position())

    def test_position_size_respects_max_position_pct(self) -> None:
        rm = self._rm()
        price = 1.0
        size, sl, tp = rm.calculate_position_size(price, PositionSide.LONG)
        self.assertGreater(size, 0.0)
        self.assertLessEqual(size * price, 10_000.0 * rm.risk_cfg.max_position_pct + 1e-6)

    def test_stop_loss_and_take_profit_prices(self) -> None:
        rm = self._rm()
        price = 2.0
        _, sl, tp = rm.calculate_position_size(price, PositionSide.LONG)
        self.assertAlmostEqual(sl, price * (1 - rm.risk_cfg.stop_loss_pct), places=6)
        self.assertAlmostEqual(tp, price * (1 + rm.risk_cfg.take_profit_pct), places=6)

        _, sl_s, tp_s = rm.calculate_position_size(price, PositionSide.SHORT)
        self.assertAlmostEqual(sl_s, price * (1 + rm.risk_cfg.stop_loss_pct), places=6)
        self.assertAlmostEqual(tp_s, price * (1 - rm.risk_cfg.take_profit_pct), places=6)

    def test_open_and_stop_trigger(self) -> None:
        rm = self._rm()
        price = 1.0
        self.assertTrue(rm.open_position(PositionSide.LONG, price))
        self.assertTrue(rm.has_open_position())

        stop_trigger_price = price * (1 - rm.risk_cfg.stop_loss_pct) - 0.01
        reason = rm.check_and_trigger_stop(stop_trigger_price)
        self.assertEqual(reason, "stop_loss")
        self.assertFalse(rm.has_open_position())

    def test_take_profit_trigger(self) -> None:
        rm = self._rm()
        price = 1.0
        self.assertTrue(rm.open_position(PositionSide.LONG, price))
        tp_price = price * (1 + rm.risk_cfg.take_profit_pct) + 0.01
        reason = rm.check_and_trigger_stop(tp_price)
        self.assertEqual(reason, "take_profit")
        self.assertFalse(rm.has_open_position())

    def test_circuit_breaker_on_consecutive_losses(self) -> None:
        rm = self._rm()
        # 先建立当日基线（首次调用会初始化 _daily_start_equity）
        rm.check_risk_limits(current_price=1.0)
        # 注入连续亏损（_record_trade_result 只更新计数器，不触发熔断）
        for _ in range(rm.risk_cfg.max_consecutive_losses):
            rm._record_trade_result(-10.0)
        # 再次调用 check_risk_limits 才会真正触发熔断
        rm.check_risk_limits(current_price=1.0)
        self.assertTrue(rm.is_trading_halted())

    def test_circuit_breaker_reset(self) -> None:
        rm = self._rm()
        rm.check_risk_limits(current_price=1.0)
        for _ in range(rm.risk_cfg.max_consecutive_losses):
            rm._record_trade_result(-10.0)
        rm.check_risk_limits(current_price=1.0)
        rm.reset_circuit_breaker()
        self.assertFalse(rm.is_trading_halted())


if __name__ == "__main__":
    unittest.main(verbosity=2)
