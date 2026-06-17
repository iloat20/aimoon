"""均线趋势 + 金叉/死叉"""

from __future__ import annotations

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_trend(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> list[Signal]:
    signals: list[Signal] = []
    trend = ti.ma_trend()
    if trend == "bullish":
        signals.append(Signal("trend_bullish", "均线多头排列", +2, category="reversal"))
    elif trend == "bearish":
        signals.append(Signal("trend_bearish", "均线空头排列", -2, category="reversal"))
    if ti.ma_golden_cross():
        signals.append(Signal("ma_golden", "MA金叉", +2, category="reversal"))
    if ti.ma_death_cross():
        signals.append(Signal("ma_death", "MA死叉", -2, category="reversal"))
    return signals
