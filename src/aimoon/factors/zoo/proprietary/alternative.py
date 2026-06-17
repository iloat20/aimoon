"""Proprietary Factor: Alternative Data Alpha

基于另类数据的私有因子，包括：
1. 市场情绪指标
2. 资金流向指标
3. 板块轮动指标
"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import rank


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """计算另类数据因子。

    基于市场情绪、资金流向、板块轮动等另类数据。
    """
    close = panel.get("close")
    volume = panel.get("volume")
    high = panel.get("high")
    low = panel.get("low")
    panel.get("amount")

    if close is None or volume is None or high is None or low is None:
        return pd.DataFrame()

    # 因子 1: 市场情绪指标 (Market Sentiment)
    # 基于价格波动和成交量的市场情绪指标
    returns = close.pct_change(fill_method=None)
    volatility = returns.rolling(window=20).std()
    volume_ratio = volume / volume.rolling(window=20).mean()
    sentiment = volatility * volume_ratio  # 高波动 + 高成交量 = 极端情绪

    # 因子 2: 资金流向指标 (Money Flow)
    # 基于成交量和价格方向的资金流向
    typical_price = (high + low + close) / 3
    money_flow = typical_price * volume
    positive_flow = money_flow.where(close > close.shift(1), 0)
    negative_flow = money_flow.where(close < close.shift(1), 0)
    money_ratio = positive_flow.rolling(window=20).sum() / negative_flow.rolling(window=20).sum()

    # 因子 3: 板块轮动指标 (Sector Rotation)
    # 基于价格动量的板块轮动信号
    momentum_5d = close / close.shift(5) - 1
    momentum_20d = close / close.shift(20) - 1
    rotation_signal = momentum_5d - momentum_20d  # 短期动量 - 长期动量

    # 因子 4: 价格动量反转 (Momentum Reversal)
    # 基于价格过度偏离的反转信号
    ma20 = close.rolling(window=20).mean()
    price_deviation = (close - ma20) / ma20
    reversal_signal = -price_deviation  # 过度偏离时反转

    # 因子 5: 成交量异常 (Volume Anomaly)
    # 基于成交量异常的因子
    volume_ma20 = volume.rolling(window=20).mean()
    volume_std20 = volume.rolling(window=20).std()
    volume_zscore = (volume - volume_ma20) / volume_std20
    volume_anomaly = volume_zscore.abs()  # 成交量异常程度

    # 组合因子
    factors = pd.DataFrame(
        {
            "sentiment": (sentiment.iloc[-1] if len(sentiment) > 0 else pd.Series(dtype=float)),
            "money_flow": (
                money_ratio.iloc[-1] if len(money_ratio) > 0 else pd.Series(dtype=float)
            ),
            "rotation": (
                rotation_signal.iloc[-1] if len(rotation_signal) > 0 else pd.Series(dtype=float)
            ),
            "reversal": (
                reversal_signal.iloc[-1] if len(reversal_signal) > 0 else pd.Series(dtype=float)
            ),
            "volume_anomaly": (
                volume_anomaly.iloc[-1] if len(volume_anomaly) > 0 else pd.Series(dtype=float)
            ),
        }
    )

    # 对每个因子进行截面排名
    for col in factors.columns:
        factors[col] = rank(factors[col].to_frame().T).iloc[0]

    return factors


# 注册因子元数据
__alpha_meta__ = {
    "id": "proprietary_alternative",
    "nickname": "另类数据",
    "theme": ["sentiment", "money_flow", "rotation", "reversal"],
    "columns_required": ["close", "volume", "high", "low"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["A股"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 20,
    "notes": "基于另类数据的私有因子，包括市场情绪、资金流向、板块轮动、价格动量反转、成交量异常",
}
