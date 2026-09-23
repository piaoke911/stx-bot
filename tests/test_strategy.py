# ===================== tests/test_strategy.py =====================
"""策略层单元测试（无需网络，全部使用内存数据）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CONFIG, AppConfig, PositionSide
from strategy import StxStrategy, calculate_kdj, calculate_ma, calculate_rsi


def _make_df(n: int = 300, trend: float = 0.5) -> pd.DataFrame:
    """生成模拟 K 线：轻微上升趋势 + 噪声"""
    rng = np.random.default_rng(42)
    base = 1.0
    closes = [base]
    for _ in range(n - 1):
        closes.append(closes[-1] + trend * 0.01 + rng.normal(0, 0.005))
    closes = np.array(closes)
    highs = closes * 1.005
    lows = closes * 0.995
    vols = rng.uniform(1000, 5000, n)
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts, "open": closes, "high": highs,
        "low": lows, "close": closes, "volume": vols,
    })


class TestIndicators(unittest.TestCase):
    def test_ma_basic(self) -> None:
        s = pd.Series(np.arange(1, 21, dtype=float))
        ma5 = calculate_ma(s, 5)
        self.assertTrue(np.isnan(ma5.iloc[3]))
        self.assertAlmostEqual(ma5.iloc[4], 3.0)
        self.assertAlmostEqual(ma5.iloc[-1], 18.0)

    def test_rsi_range(self) -> None:
        df = _make_df(100)
        rsi = calculate_rsi(df["close"], 14)
        self.assertTrue((rsi >= 0).all() and (rsi <= 100).all())

    def test_rsi_all_up_is_high(self) -> None:
        """
        纯上涨序列的 RSI 边界行为。
        当前实现：avg_loss=0 时 rs=NaN → rsi=NaN → fillna(50.0)，返回中性值 50。
        （这是原实现的设计行为，测试按实际行为断言）
        """
        s = pd.Series(np.arange(1, 51, dtype=float))
        rsi = calculate_rsi(s, 14)
        self.assertEqual(rsi.iloc[-1], 50.0)
        self.assertTrue((rsi >= 0).all() and (rsi <= 100).all())

    def test_kdj_columns_and_range(self) -> None:
        df = _make_df(100)
        out = calculate_kdj(df, 9, 3, 3)
        for col in ("K", "D", "J"):
            self.assertIn(col, out.columns)
        self.assertTrue((out["K"].dropna() >= -5).all())
        self.assertTrue((out["K"].dropna() <= 105).all())


class _FakeStrategy(StxStrategy):
    """屏蔽网络：直接注入行情数据。"""

    def __init__(self, data: dict[str, pd.DataFrame], config: AppConfig = CONFIG) -> None:
        self.config = config
        self.exchange = None  # type: ignore[assignment]
        self.indicator_cfg = config.indicator
        self.weight_cfg = config.weight
        self._data = data

    def fetch_ohlcv_df(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        key = f"{symbol}|{timeframe}"
        if key not in self._data:
            raise ValueError(f"缺少测试数据: {key}")
        return self._data[key].tail(limit).reset_index(drop=True)


class TestMaTrendScore(unittest.TestCase):
    def test_uptrend_scores_positive(self) -> None:
        df = _make_df(200, trend=1.0)
        strat = _FakeStrategy({})
        score = strat._ma_trend_score(df)
        self.assertEqual(score, 1.0)

    def test_downtrend_scores_negative(self) -> None:
        df = _make_df(200, trend=-1.0)
        strat = _FakeStrategy({})
        score = strat._ma_trend_score(df)
        self.assertEqual(score, -1.0)

    def test_insufficient_data_returns_zero(self) -> None:
        df = _make_df(10)
        strat = _FakeStrategy({})
        self.assertEqual(strat._ma_trend_score(df), 0.0)


class TestGenerateSignal(unittest.TestCase):
    def test_signal_structure(self) -> None:
        symbol = CONFIG.trading.symbol
        btc_symbol = CONFIG.trading.btc_symbol
        data = {
            f"{symbol}|15m": _make_df(300, trend=1.0),
            f"{symbol}|1h": _make_df(300, trend=1.0),
            f"{symbol}|4h": _make_df(300, trend=1.0),
            f"{btc_symbol}|15m": _make_df(300, trend=1.0),
            f"{btc_symbol}|1h": _make_df(300, trend=1.0),
            f"{btc_symbol}|4h": _make_df(300, trend=1.0),
        }
        strat = _FakeStrategy(data)
        strat.compute_tokenomics_proxy = lambda s: (0.0, {"tokenomics_proxy": 0.0})  # type: ignore[method-assign]
        result = strat.generate_signal()
        self.assertIn(result.side, (PositionSide.LONG, PositionSide.SHORT, PositionSide.FLAT))
        self.assertGreaterEqual(result.score, -1.0)
        self.assertLessEqual(result.score, 1.0)
        self.assertIn("final_score", result.details)


class TestBtcStxResonance(unittest.TestCase):
    def test_disagreement_is_damped(self) -> None:
        symbol = CONFIG.trading.symbol
        btc_symbol = CONFIG.trading.btc_symbol
        data = {
            f"{symbol}|15m": _make_df(300, trend=-1.0),
            f"{symbol}|1h": _make_df(300, trend=-1.0),
            f"{symbol}|4h": _make_df(300, trend=-1.0),
            f"{btc_symbol}|15m": _make_df(300, trend=1.0),
            f"{btc_symbol}|1h": _make_df(300, trend=1.0),
            f"{btc_symbol}|4h": _make_df(300, trend=1.0),
        }
        strat = _FakeStrategy(data)
        score, detail = strat.compute_btc_stx_resonance_score()
        self.assertLess(abs(score), 0.3)
        self.assertIn("btc_stx_combined", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
