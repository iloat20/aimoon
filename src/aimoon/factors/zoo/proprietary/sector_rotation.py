"""Proprietary Factor: Sector Rotation

基于板块轮动的私有因子。
"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import rank


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """计算板块轮动因子。

    基于价格动量、成交量变化、行业轮动等特征。
    """
    close = panel.get("close")
    volume = panel.get("volume")
    panel.get("high")
    panel.get("low")

    if close is None or volume is None:
        return pd.DataFrame()

    # 因子 1: 短期动量 (Short-term Momentum)
    # 5 日价格动量
    momentum_5d = close / close.shift(5) - 1

    # 因子 2: 中期动量 (Medium-term Momentum)
    # 20 日价格动量
    momentum_20d = close / close.shift(20) - 1

    # 因子 3: 动量反转 (Momentum Reversal)
    # 短期动量 - 中期动量，反映轮动信号
    momentum_reversal = momentum_5d - momentum_20d / 4

    # 因子 4: 成交量动量 (Volume Momentum)
    # 成交量的变化率
    volume_ma5 = volume.rolling(window=5).mean()
    volume_ma20 = volume.rolling(window=20).mean()
    volume_momentum = (volume_ma5 - volume_ma20) / volume_ma20

    # 因子 5: 价格波动率 (Price Volatility)
    # 价格波动率的变化
    returns = close.pct_change(fill_method=None)
    volatility_5d = returns.rolling(window=5).std()
    volatility_20d = returns.rolling(window=20).std()
    volatility_ratio = volatility_5d / volatility_20d

    # 因子 6: 板块动量 (Sector Momentum)
    # 基于价格和成交量的综合动量
    sector_momentum = momentum_5d * volume_momentum

    # 组合因子
    factors = pd.DataFrame(
        {
            "momentum_5d": (
                momentum_5d.iloc[-1] if len(momentum_5d) > 0 else pd.Series(dtype=float)
            ),
            "momentum_20d": (
                momentum_20d.iloc[-1] if len(momentum_20d) > 0 else pd.Series(dtype=float)
            ),
            "momentum_reversal": (
                momentum_reversal.iloc[-1] if len(momentum_reversal) > 0 else pd.Series(dtype=float)
            ),
            "volume_momentum": (
                volume_momentum.iloc[-1] if len(volume_momentum) > 0 else pd.Series(dtype=float)
            ),
            "volatility_ratio": (
                volatility_ratio.iloc[-1] if len(volatility_ratio) > 0 else pd.Series(dtype=float)
            ),
            "sector_momentum": (
                sector_momentum.iloc[-1] if len(sector_momentum) > 0 else pd.Series(dtype=float)
            ),
        }
    )

    # 对每个因子进行截面排名
    for col in factors.columns:
        factors[col] = rank(factors[col].to_frame().T).iloc[0]

    return factors


# 注册因子元数据
__alpha_meta__ = {
    "id": "proprietary_sector_rotation",
    "nickname": "板块轮动",
    "theme": ["sector", "rotation", "momentum"],
    "columns_required": ["close", "volume", "high", "low"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["A股"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 20,
    "notes": "基于板块轮动的私有因子，包括短期动量、中期动量、动量反转、成交量动量、价格波动率、板块动量",
}
