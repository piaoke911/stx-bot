# ===================== precision_utils.py =====================
"""
precision_utils.py
==================
数量 / 价格精度对齐与下单合规性校验。

为什么必须有这个模块：
------------------------------------------------------------------
交易所对下单参数有严格约束，直接传浮点数会以极高概率被拒单：
1. 数量精度（lotSz / stepSize）：如 STX 合约 stepSize=0.1，
   传入 12.3456 会被拒。
2. 价格精度（tickSz）：如 OKX STX 永续 tickSz=0.0001，
   传入 2.12345678 会被拒。
3. 最小下单量（minSz）：如 minSz=0.1，传入 0.05 会被拒。
4. 最小名义价值（minCost）：如 OKX 要求 ≥ 5 USDT，
   数量 * 价格 < 5 会被拒。

本模块封装 ccxt 的 amount_to_precision / price_to_precision，
并在 Paper 模式下（无真实交易所实例时）自动降级为 no-op。
------------------------------------------------------------------
"""

from __future__ import annotations

import logging
import math
from typing import Optional

try:
    import ccxt  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise ImportError("请先安装 ccxt: pip install ccxt") from exc

logger = logging.getLogger("stx_quant.precision")


class PrecisionUtils:
    """
    交易所精度工具类。

    使用方式：
        p = PrecisionUtils(exchange, symbol="STX/USDT:USDT")
        size_ok = p.round_amount(size, round_down=True)   # 保守向下取整
        price_ok = p.round_price(price)
        ok, msg = p.validate_order(size_ok, price_ok)
        if not ok:
            logger.warning("下单校验失败: %s", msg)
    """

    def __init__(
        self,
        exchange: Optional[ccxt.Exchange],
        symbol: str,
        fallback_amount_precision: int = 8,
        fallback_price_precision: int = 8,
        fallback_min_amount: float = 0.0,
        fallback_min_cost: float = 0.0,
    ) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self._markets_loaded: bool = False
        self._market: Optional[dict] = None

        # 降级参数（Paper 模式 / 加载市场失败时使用）
        self.fallback_amount_precision = fallback_amount_precision
        self.fallback_price_precision = fallback_price_precision
        self.fallback_min_amount = fallback_min_amount
        self.fallback_min_cost = fallback_min_cost

        if self.exchange is not None:
            self._try_load_markets()

    # ---------------------- 内部加载 ----------------------

    def _try_load_markets(self) -> None:
        """尝试加载市场元数据。失败不阻断，仅降级。"""
        if self.exchange is None:
            return
        try:
            if not getattr(self.exchange, "markets", None):
                self.exchange.load_markets()
            self._market = self.exchange.market(self.symbol)
            self._markets_loaded = True
            limits = self._market.get("limits", {}) if self._market else {}
            logger.info(
                "已加载市场元数据 | symbol=%s | 最小数量=%s | 最小名义=%s",
                self.symbol,
                limits.get("amount", {}).get("min"),
                limits.get("cost", {}).get("min"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "加载市场 %s 元数据失败，将使用降级精度参数: %s",
                self.symbol, exc,
            )
            self._markets_loaded = False
            self._market = None

    # ---------------------- 精度对齐 ----------------------

    def round_amount(self, amount: float, round_down: bool = True) -> float:
        """
        对齐数量到交易所允许的 stepSize。

        参数：
            round_down: True 时使用 floor（保守，避免超出可下数量）；
                        False 时使用 ccxt 默认四舍五入（不推荐）。
        """
        if amount <= 0:
            return 0.0

        # 优先用交易所精度
        if self._markets_loaded and self.exchange is not None:
            try:
                if round_down:
                    # 用 floor 保证不超过预期数量
                    step = self._get_amount_step()
                    if step > 0:
                        aligned = math.floor(amount / step) * step
                        # 修正浮点误差
                        return float(self.exchange.amount_to_precision(self.symbol, aligned))
                return float(self.exchange.amount_to_precision(self.symbol, amount))
            except Exception as exc:  # noqa: BLE001
                logger.warning("amount_to_precision 失败，降级处理: %s", exc)

        # 降级：按固定小数位截断
        factor = 10 ** self.fallback_amount_precision
        if round_down:
            return math.floor(amount * factor) / factor
        return round(amount, self.fallback_amount_precision)

    def round_price(self, price: float) -> float:
        """对齐价格到交易所允许的 tickSz。"""
        if price <= 0:
            return 0.0

        if self._markets_loaded and self.exchange is not None:
            try:
                return float(self.exchange.price_to_precision(self.symbol, price))
            except Exception as exc:  # noqa: BLE001
                logger.warning("price_to_precision 失败，降级处理: %s", exc)

        return round(price, self.fallback_price_precision)

    # ---------------------- 合规性校验 ----------------------

    def get_limits(self) -> dict:
        """获取当前 symbol 的下单限制（含降级值）"""
        if self._markets_loaded and self._market is not None:
            limits = self._market.get("limits", {}) or {}
            return {
                "min_amount": limits.get("amount", {}).get("min") or 0.0,
                "max_amount": limits.get("amount", {}).get("max") or 0.0,
                "min_cost": limits.get("cost", {}).get("min") or 0.0,
                "max_cost": limits.get("cost", {}).get("max") or 0.0,
            }
        return {
            "min_amount": self.fallback_min_amount,
            "max_amount": 0.0,
            "min_cost": self.fallback_min_cost,
            "max_cost": 0.0,
        }

    def validate_order(self, amount: float, price: float) -> tuple[bool, str]:
        """
        校验下单是否满足交易所最小/最大限制。
        返回 (是否合法, 说明信息)。
        """
        if amount <= 0:
            return False, f"数量 {amount} 必须为正数"
        if price <= 0:
            return False, f"价格 {price} 必须为正数"

        limits = self.get_limits()
        notional = amount * price

        min_amount = float(limits.get("min_amount") or 0.0)
        max_amount = float(limits.get("max_amount") or 0.0)
        min_cost = float(limits.get("min_cost") or 0.0)
        max_cost = float(limits.get("max_cost") or 0.0)

        if min_amount > 0 and amount < min_amount:
            return False, f"数量 {amount} < 最小下单量 {min_amount}"
        if max_amount > 0 and amount > max_amount:
            return False, f"数量 {amount} > 最大下单量 {max_amount}"
        if min_cost > 0 and notional < min_cost:
            return False, f"名义价值 {notional:.4f} < 最小名义 {min_cost}"
        if max_cost > 0 and notional > max_cost:
            return False, f"名义价值 {notional:.4f} > 最大名义 {max_cost}"

        return True, "OK"

    # ---------------------- 内部工具 ----------------------

    def _get_amount_step(self) -> float:
        """从市场元数据提取 stepSize（数量精度）。"""
        if self._market is None:
            return 0.0
        try:
            precision = self._market.get("precision", {}) or {}
            amount_prec = precision.get("amount")
            if isinstance(amount_prec, (int, float)) and amount_prec > 0:
                return float(amount_prec)
        except Exception:
            pass
        return 0.0


def create_precision_utils(
    exchange: Optional[ccxt.Exchange], symbol: str,
) -> PrecisionUtils:
    """工厂函数：便捷创建 PrecisionUtils 实例。"""
    return PrecisionUtils(exchange, symbol)
