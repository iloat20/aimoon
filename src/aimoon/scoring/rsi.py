"""RSI 多空信号"""
from __future__ import annotations
import pandas as pd
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_rsi(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> Signal | None:
    val = ti.rsi().iloc[-1]
    if pd.isna(val):
        return None
    if val > 60:
        return Signal("rsi_strong", f"RSI强势({val:.0f})", +2)
    if val > 50:
        return Signal("rsi_bullish", f"RSI偏多({val:.0f})", +1)
    if val < 40:
        return Signal("rsi_weak", f"RSI弱势({val:.0f})", -2)
    if val < 50:
        return Signal("rsi_bearish", f"RSI偏空({val:.0f})", -1)
    return None
