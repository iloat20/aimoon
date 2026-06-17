"""Proprietary Factor: Market Microstructure Alpha

基于市场微观结构的私有因子，不依赖学术文献。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.factors.base import rank, ts_corr


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """计算市场微观结构因子。

    基于成交量、价格行为、流动性等微观结构特征。
    """
    close = panel.get("close")
    volume = panel.get("volume")
    high = panel.get("high")
    low = panel.get("low")

    if close is None or volume is None or high is None or low is None:
        return pd.DataFrame()

    # 因子 1: 成交量加权价格偏离 (VWAP Deviation)
    # 价格相对于成交量加权平均价的偏离程度
    vwap = (close * volume).rolling(window=20).sum() / volume.rolling(window=20).sum()
    vwap_deviation = (close - vwap) / vwap

    # 因子 2: 成交量动量 (Volume Momentum)
    # 成交量的变化率，反映市场关注度变化
    volume_ma20 = volume.rolling(window=20).mean()
    volume_ma5 = volume.rolling(window=5).mean()
    volume_momentum = (volume_ma5 - volume_ma20) / volume_ma20

    # 因子 3: 价格冲击 (Price Impact)
    # 成交量变化对价格的影响程度
    returns = close.pct_change(fill_method=None)
    volume_change = volume.pct_change(fill_method=None)
    price_impact = ts_corr(returns, volume_change, 20)

    # 因子 4: 流动性指标 (Liquidity Indicator)
    # 基于价格波动和成交量的流动性指标
    price_range = (high - low) / close
    liquidity = volume / price_range.rolling(window=20).mean()

    # 因子 5: 订单流不平衡 (Order Flow Imbalance)
    # 基于价格方向和成交量的订单流指标
    price_direction = np.sign(close.diff())
    order_flow = (price_direction * volume).rolling(window=20).sum()
    order_flow_normalized = order_flow / volume.rolling(window=20).sum()

    # 组合因子
    factors = pd.DataFrame(
        {
            "vwap_deviation": (
                vwap_deviation.iloc[-1] if len(vwap_deviation) > 0 else pd.Series(dtype=float)
            ),
            "volume_momentum": (
                volume_momentum.iloc[-1] if len(volume_momentum) > 0 else pd.Series(dtype=float)
            ),
            "price_impact": (
                price_impact.iloc[-1] if len(price_impact) > 0 else pd.Series(dtype=float)
            ),
            "liquidity": (liquidity.iloc[-1] if len(liquidity) > 0 else pd.Series(dtype=float)),
            "order_flow": (
                order_flow_normalized.iloc[-1]
                if len(order_flow_normalized) > 0
                else pd.Series(dtype=float)
            ),
        }
    )

    # 对每个因子进行截面排名
    for col in factors.columns:
        factors[col] = rank(factors[col].to_frame().T).iloc[0]

    return factors


# 注册因子元数据
__alpha_meta__ = {
    "id": "proprietary_microstructure",
    "nickname": "市场微观结构",
    "theme": ["microstructure", "liquidity", "volume"],
    "columns_required": ["close", "volume", "high", "low"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["A股"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "基于市场微观结构的私有因子，包括VWAP偏离、成交量动量、价格冲击、流动性指标、订单流不平衡",
}
