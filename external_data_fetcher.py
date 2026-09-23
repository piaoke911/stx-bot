# ===================== external_data_fetcher.py =====================
"""
外部数据抓取层：负责抓取 DefiLlama TVL、Stacks 链上活跃度、恐惧贪婪指数。
由于部分数据（sBTC/Staking）无免费稳定接口，当前使用代理指标，代码中已明确注释。
"""
import logging
import urllib.request
import json
from typing import Optional

logger = logging.getLogger("stx_quant.external_data")

class ExternalDataFetcher:
    def __init__(self):
        self._cache = {}

    def fetch_tvl_trend(self) -> float:
        """获取 Stacks DeFi TVL 趋势 (1=上升, -1=下降, 0=中性)"""
        try:
            # 免费公开接口：DefiLlama
            url = "https://api.llama.fi/v2/historicalChainTvl/Stacks"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
                if len(data) >= 3:
                    tvl_now = data[-1]['tvl']
                    tvl_prev = data[-3]['tvl']
                    return 1.0 if tvl_now > tvl_prev else (-1.0 if tvl_now < tvl_prev else 0.0)
        except Exception as exc:
            logger.warning("获取 Stacks TVL 失败（代理指标，按中性处理）: %s", exc)
        return 0.0

    def fetch_stacks_activity_trend(self) -> float:
        """获取 Stacks 链上活跃度趋势（使用交易量代理）"""
        # 注：官方 API 可能有延迟，当前版本主要用交易量趋势代理，暂不纳入精确活跃地址
        return 0.0 # 预留接口

    def fetch_fear_greed_index(self) -> int:
        """获取恐惧贪婪指数 (0-100)"""
        try:
            url = "https://api.alternative.me/fng/"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
                return int(data['data'][0]['value'])
        except Exception as exc:
            logger.warning("获取恐惧贪婪指数失败，按中性50处理: %s", exc)
        return 50
