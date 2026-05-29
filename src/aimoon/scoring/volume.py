"""成交量信号"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_volume(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> Signal | None:
    vr = ti.volume_ratio()
    if vr > 2.0:
        return Signal("volume_surge", f"放量({vr:.1f}x)", +2)
    if vr > 1.5:
        return Signal("volume_mild", f"温和放量({vr:.1f}x)", +1)
    if vr < 0.5:
        return Signal("volume_shrink", f"缩量({vr:.1f}x)", -1)
    return None
