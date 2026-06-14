"""布林带位置信号 — 加强反转权重"""

from __future__ import annotations

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_bollinger(
    ti: TechInd, *, code: str = "", ctx: dict | None = None
) -> Signal | None:
    pos = ti.bollinger_position()
    if pos == "below":
        return Signal("boll_below", "触及布林下轨", +2, category="reversal")
    if pos == "above":
        return Signal("boll_above", "触及布林上轨", -2, category="reversal")
    return None

