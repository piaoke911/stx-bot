# ===================== strategy.py =====================
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd
import ccxt

from config import AppConfig, CONFIG, ExchangeName, PositionSide
from external_data_fetcher import ExternalDataFetcher

logger = logging.getLogger("stx_quant.strategy")

@dataclass
class SignalResult:
    side: PositionSide
    score: int # 0-100分
    details: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

def create_exchange(config: AppConfig, need_auth: bool) -> ccxt.Exchange:
    exchange_cls_map = {ExchangeName.OKX: ccxt.okx, ExchangeName.BINANCE: ccxt.binanceusdm}
    params: dict[str, object] = {"enableRateLimit": True, "options": {"defaultType": "swap"}, "proxies": {"http": "http://127.0.0.1:3067", "https": "http://127.0.0.1:3067"}}
    if need_auth:
        params["apiKey"] = config.exchange.api_key
        params["secret"] = config.exchange.api_secret
        if config.exchange.name == ExchangeName.OKX:
            params["password"] = config.exchange.api_passphrase
    exchange = exchange_cls_map.get(config.exchange.name)(params)
    return exchange

def calculate_ma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()

def calculate_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50.0)

def calculate_kdj(df: pd.DataFrame, n: int, k_smooth: int, d_smooth: int) -> pd.DataFrame:
    low_n = df["low"].rolling(window=n, min_periods=n).min()
    high_n = df["high"].rolling(window=n, min_periods=n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(alpha=1/k_smooth, adjust=False).mean()
    d = k.ewm(alpha=1/d_smooth, adjust=False).mean()
    df["K"], df["D"], df["J"] = k, d, 3*k - 2*d
    return df

class StxStrategy:
    def __init__(self, config: AppConfig = CONFIG, exchange: Optional[ccxt.Exchange] = None) -> None:
        self.config = config
        self.exchange = exchange or create_exchange(config, need_auth=False)
        self.indicator_cfg = config.indicator
        self.ext_fetcher = ExternalDataFetcher()
        self._ma_cache = {}

    def fetch_ohlcv_df(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def _check_direction_priority(self) -> tuple[bool, PositionSide, str]:
        """
        一、方向判断（最高优先级）
        必须 BTC 与 STX 15m/1H/4H 均线排列完全一致，否则强制空仓！
        """
        stx_scores, btc_scores = [], []
        for tf in self.indicator_cfg.timeframes:
            # 获取 STX 趋势
            df_stx = self.fetch_ohlcv_df(self.config.trading.symbol, tf, self.config.trading.ohlcv_limit)
            ma_s = calculate_ma(df_stx['close'], self.indicator_cfg.ma_short_period).iloc[-1]
            ma_m = calculate_ma(df_stx['close'], self.indicator_cfg.ma_mid_period).iloc[-1]
            ma_l = calculate_ma(df_stx['close'], self.indicator_cfg.ma_long_period).iloc[-1]
            stx_scores.append(1 if ma_s > ma_m > ma_l else (-1 if ma_s < ma_m < ma_l else 0))
            
            # 获取 BTC 趋势
            df_btc = self.fetch_ohlcv_df(self.config.trading.btc_symbol, tf, self.config.trading.ohlcv_limit)
            bma_s = calculate_ma(df_btc['close'], self.indicator_cfg.ma_short_period).iloc[-1]
            bma_m = calculate_ma(df_btc['close'], self.indicator_cfg.ma_mid_period).iloc[-1]
            bma_l = calculate_ma(df_btc['close'], self.indicator_cfg.ma_long_period).iloc[-1]
            btc_scores.append(1 if bma_s > bma_m > bma_l else (-1 if bma_s < bma_m < bma_l else 0))

        # BTC 4H 必须同向，且所有周期不能有冲突
        btc_4h = btc_scores[2]
        if btc_4h == 0:
            return False, PositionSide.FLAT, "BTC 4H 趋势不明确"
        if len(set(stx_scores + [btc_4h])) != 1:
            return False, PositionSide.FLAT, "多周期均线排列出现冲突，强制空仓"
        
        side = PositionSide.LONG if stx_scores[0] == 1 else PositionSide.SHORT
        return True, side, "多周期均线共振通过"

    def generate_signal(self) -> SignalResult:
        # 1. 方向判断（最高优先级）
        direction_ok, side, reason = self._check_direction_priority()
        if not direction_ok:
            return SignalResult(side=PositionSide.FLAT, score=0, reasons=[reason])

        symbol = self.config.trading.symbol
        tf = "1h"
        df = self.fetch_ohlcv_df(symbol, tf, self.config.trading.ohlcv_limit)
        
        # 2. 入场过滤条件
        kdj_df = calculate_kdj(df.copy(), self.indicator_cfg.kdj_n, self.indicator_cfg.kdj_k_smooth, self.indicator_cfg.kdj_d_smooth)
        k_prev, d_prev = kdj_df["K"].iloc[-2], kdj_df["D"].iloc[-2]
        k_now, d_now = kdj_df["K"].iloc[-1], kdj_df["D"].iloc[-1]
        
        kdj_cross = False
        if side == PositionSide.LONG and k_prev <= d_prev and k_now > d_now: kdj_cross = True
        elif side == PositionSide.SHORT and k_prev >= d_prev and k_now < d_now: kdj_cross = True
        if not kdj_cross:
            return SignalResult(side=PositionSide.FLAT, score=0, reasons=["KDJ 未出现同向金叉/死叉"])

        rsi_val = calculate_rsi(df['close'], self.indicator_cfg.rsi_period).iloc[-1]
        if side == PositionSide.LONG and rsi_val >= 70:
            return SignalResult(side=PositionSide.FLAT, score=0, reasons=["RSI 处于超买区，过滤做多"])
        if side == PositionSide.SHORT and rsi_val <= 30:
            return SignalResult(side=PositionSide.FLAT, score=0, reasons=["RSI 处于超卖区，过滤做空"])

        vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
        if df['volume'].iloc[-1] < vol_ma * 1.2:
            return SignalResult(side=PositionSide.FLAT, score=0, reasons=["成交量未放大到均量1.2倍以上"])

        # 3. 信号强度评分（0-100分）
        score = 0
        details = {}
        
        # (1) BTC + STX 三周期均线共振 25分
        score += 25; details['ma_resonance'] = 25
        
        # (2) KDJ 金叉/死叉 15分
        score += 15; details['kdj_cross'] = 15
        
        # (3) RSI 位置合理 10分
        score += 10; details['rsi_ok'] = 10
        
        # (4) 成交量放大 10分
        score += 10; details['volume_up'] = 10
        
        # (5) BTC 趋势与 STX 高度一致 15分
        score += 15; details['btc_aligned'] = 15
        
        # (6) Staking / sBTC 趋势支持 10分 (代理指标：暂用资金费率及链上趋势替代)
        funding_rate = self.exchange.fetch_funding_rate(symbol).get('fundingRate', 0.0)
        # 极端资金费率过滤：过热降低做多，过冷降低做空
        if side == PositionSide.LONG and funding_rate > 0.001:
            return SignalResult(side=PositionSide.FLAT, score=0, reasons=["资金费率极端正值，做多过热过滤"])
        if side == PositionSide.SHORT and funding_rate < -0.001:
            return SignalResult(side=PositionSide.FLAT, score=0, reasons=["资金费率极端负值，做空过热过滤"])
        score += 10; details['tokenomics_proxy'] = 10
        
        # (7) TVL / 活跃地址趋势支持 8分
        tvl_trend = self.ext_fetcher.fetch_tvl_trend()
        if (side == PositionSide.LONG and tvl_trend >= 0) or (side == PositionSide.SHORT and tvl_trend <= 0):
            score += 8; details['tvl_trend'] = 8
        else:
            details['tvl_trend'] = 0
        
        # (8) 情绪与资金费率不冲突 7分
        fng = self.ext_fetcher.fetch_fear_greed_index()
        if (side == PositionSide.LONG and fng < 25) or (side == PositionSide.SHORT and fng > 75):
            return SignalResult(side=PositionSide.FLAT, score=0, reasons=[f"恐惧贪婪指数({fng})极端，过滤信号"])
        score += 7; details['sentiment_ok'] = 7

        # 总分判断
        logger.info(f"信号评分完成 | 方向={side.value} | 得分={score} | 明细={details}")
        
        if score < 55:
            return SignalResult(side=PositionSide.FLAT, score=score, reasons=[f"评分 {score} < 55，放弃交易"])
        
        return SignalResult(side=side, score=score, details=details, reasons=[f"评分 {score} 达标"])
