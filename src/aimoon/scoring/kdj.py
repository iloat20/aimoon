"""KDJ 金叉/死叉 + 超买超卖"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_kdj(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> list[Signal]:
    signals: list[Signal] = []
    if ti.kdj_golden_cross():
        signals.append(Signal("kdj_golden", "KDJ金叉", +1))
    if ti.kdj_death_cross():
        signals.append(Signal("kdj_death", "KDJ死叉", -1))
    if ti.kdj_oversold():
        signals.append(Signal("kdj_oversold", "KDJ超卖", +1))
    if ti.kdj_overbought():
        signals.append(Signal("kdj_overbought", "KDJ超买", -1))
    return signals
