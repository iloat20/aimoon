"""RSI 多空信号 — 梯度评分（低权重）+ 超买超卖反转"""

from __future__ import annotations

import pandas as pd

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_rsi(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> Signal | None:
    val = ti.rsi().iloc[-1]
    if pd.isna(val):
        return None
    # Extreme overbought: mean-reversion sell signal
    if val >= 80:
        return Signal("rsi_extreme_overbought", f"RSI极度超买({val:.0f})", -2, category="reversal")
    if val >= 70:
        return Signal("rsi_overbought", f"RSI超买({val:.0f})", -1, category="reversal")
    # Bullish gradient zone (55-70): gradual scoring
    if val >= 65:
        return Signal("rsi_strong", f"RSI强势({val:.0f})", +1, category="reversal")
    if val >= 55:
        return Signal("rsi_mild_bull", f"RSI偏多({val:.0f})", +1, category="reversal")
    if val >= 50:
        return Signal("rsi_neutral_bull", f"RSI中性偏多({val:.0f})", 0, category="reversal")
    # Extreme oversold: mean-reversion buy signal
    if val <= 20:
        return Signal("rsi_extreme_oversold", f"RSI极度超卖({val:.0f})", +2, category="reversal")
    if val <= 30:
        return Signal("rsi_oversold", f"RSI超卖({val:.0f})", +1, category="reversal")
    # Bearish gradient zone (30-45): gradual scoring
    if val <= 45:
        return Signal("rsi_mild_bear", f"RSI偏空({val:.0f})", -1, category="reversal")
    # 45-50: slightly bearish
    return Signal("rsi_slight_bear", f"RSI略偏空({val:.0f})", -1, category="reversal")
