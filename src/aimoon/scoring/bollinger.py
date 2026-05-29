"""布林带位置信号"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_bollinger(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> Signal | None:
    pos = ti.bollinger_position()
    if pos == "below":
        return Signal("boll_below", "触及布林下轨", +1)
    if pos == "above":
        return Signal("boll_above", "触及布林上轨", -1)
    return None
