# ===================== tests/test_precision_utils.py =====================
"""精度工具单元测试（无交易所实例时验证降级路径）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from precision_utils import PrecisionUtils


class TestPrecisionFallback(unittest.TestCase):
    """exchange=None → 使用降级精度参数。"""

    def setUp(self) -> None:
        self.p = PrecisionUtils(
            exchange=None,
            symbol="STX/USDT:USDT",
            fallback_amount_precision=2,
            fallback_price_precision=4,
            fallback_min_amount=0.1,
            fallback_min_cost=5.0,
        )

    def test_round_amount_floor(self) -> None:
        self.assertAlmostEqual(self.p.round_amount(12.345, round_down=True), 12.34)
        self.assertAlmostEqual(self.p.round_amount(0.0), 0.0)

    def test_round_price(self) -> None:
        self.assertAlmostEqual(self.p.round_price(2.123456), 2.1235)

    def test_validate_min_amount(self) -> None:
        ok, msg = self.p.validate_order(0.05, 10.0)
        self.assertFalse(ok)
        self.assertIn("最小下单量", msg)

    def test_validate_min_cost(self) -> None:
        ok, msg = self.p.validate_order(0.1, 10.0)  # notional = 1.0 < 5.0
        self.assertFalse(ok)
        self.assertIn("最小名义", msg)

    def test_validate_ok(self) -> None:
        ok, msg = self.p.validate_order(1.0, 10.0)
        self.assertTrue(ok, msg)

    def test_validate_negative_amount(self) -> None:
        ok, msg = self.p.validate_order(-1.0, 10.0)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
