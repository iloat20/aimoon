"""MACD 金叉/死叉 + 零轴位置"""

from __future__ import annotations

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_macd(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> list[Signal]:
    signals: list[Signal] = []
    if ti.macd_golden_cross():
        signals.append(Signal("macd_golden", "MACD金叉", +2, category="reversal"))
    if ti.macd_death_cross():
        signals.append(Signal("macd_death", "MACD死叉", -2, category="reversal"))
    if ti.macd_above_zero():
        signals.append(Signal("macd_above_zero", "MACD零轴上方", +1, category="reversal"))
    else:
        signals.append(Signal("macd_below_zero", "MACD零轴下方", -1, category="reversal"))
    return signals
