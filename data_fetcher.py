# ===================== data_fetcher.py =====================
"""
data_fetcher.py
================
市场数据拉取统一封装层。

设计目的：
1. 将 ccxt 的数据拉取逻辑集中封装，方便策略 / 主循环 / 回测器复用。
2. 内置重试机制与错误分类（网络错误重试、交易所错误直抛）。
3. 提供统一的 DataFrame 输出格式（timestamp 为 UTC datetime）。
4. Paper 模式默认无需任何 API 密钥。

注意：
- 本模块只负责行情读取，不涉及任何交易下单。
- 若 Live 模式需要鉴权行情（如私有持仓），应传入已鉴权的 exchange。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

import pandas as pd

try:
    import ccxt  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise ImportError("请先安装 ccxt: pip install ccxt") from exc

from config import AppConfig, CONFIG
from strategy import create_exchange

logger = logging.getLogger("stx_quant.data_fetcher")


class MarketDataFetcher:
    """
    行情数据拉取器：
    - fetch_ohlcv(symbol, timeframe, limit)：拉取 K 线 DataFrame
    - fetch_last_price(symbol)：拉取最新成交价
    - fetch_funding_rate(symbol)：拉取永续合约资金费率（不支持时返回 0）
    """

    def __init__(
        self,
        config: AppConfig = CONFIG,
        exchange: Optional[ccxt.Exchange] = None,
    ) -> None:
        self.config = config
        # 数据层只需要公开行情，因此若无外部传入，统一创建无鉴权实例
        self.exchange: ccxt.Exchange = exchange or create_exchange(config, need_auth=False)
        self._retry_times: int = config.risk.order_retry_times
        self._retry_backoff: float = config.risk.order_retry_backoff_sec
        logger.info(
            "MarketDataFetcher 初始化完成 | 交易所=%s | 模式=%s",
            config.exchange.name.value, config.mode.value,
        )

    # ---------------------- 通用重试包装 ----------------------

    def _with_retry(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        通用重试包装：
        - 网络错误 / 请求超时：按指数退避重试，达到上限后抛出。
        - 交易所业务错误（如符号不存在）：直接抛出，避免无意义重试。
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._retry_times + 1):
            try:
                return fn(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
                last_exc = exc
                logger.warning("数据拉取网络异常（第 %d/%d 次）: %s", attempt, self._retry_times, exc)
                time.sleep(self._retry_backoff * attempt)
            except ccxt.ExchangeError as exc:
                logger.error("数据拉取交易所异常: %s", exc)
                raise
        assert last_exc is not None
        raise last_exc

    # ---------------------- 对外接口 ----------------------

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 200,
    ) -> pd.DataFrame:
        """
        拉取 OHLCV，返回 DataFrame，列：timestamp / open / high / low / close / volume。
        - timestamp 已转换为 UTC datetime（Asia/Shanghai 用户可自行 tz_convert）。
        """
        raw = self._with_retry(
            self.exchange.fetch_ohlcv, symbol, timeframe, None, limit,
        )
        if not raw:
            raise ValueError(f"{symbol} {timeframe} 未获取到任何K线数据")
        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    def fetch_last_price(self, symbol: str) -> float:
        """拉取最新成交价（ticker.last，回退到 ticker.close）"""
        ticker = self._with_retry(self.exchange.fetch_ticker, symbol)
        price = ticker.get("last") or ticker.get("close")
        if price is None:
            raise ValueError(f"{symbol} ticker 未返回有效价格字段: {ticker}")
        return float(price)

    def fetch_funding_rate(self, symbol: str) -> float:
        """
        拉取永续合约资金费率，失败时返回 0.0（视为中性）。
        不同交易所返回结构存在差异，此处仅取 fundingRate 字段。
        """
        try:
            funding = self._with_retry(self.exchange.fetch_funding_rate, symbol)
            return float(funding.get("fundingRate") or 0.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("获取 %s 资金费率失败，按 0 处理: %s", symbol, exc)
            return 0.0

    def close(self) -> None:
        """释放 ccxt 底层连接资源（若支持）。"""
        try:
            close_fn = getattr(self.exchange, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception as exc:  # noqa: BLE001
            logger.debug("关闭交易所连接时发生异常（可忽略）: %s", exc)
