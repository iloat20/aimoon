"""成交量信号 — 反向（IC=-0.16，放量预测下跌）"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_volume(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> Signal | None:
    vr = ti.volume_ratio()
    # IC 分析显示成交量是反向指标：放量预测下跌，缩量预测上涨
    if vr > 2.0:
        return Signal("volume_surge", f"放量警示({vr:.1f}x)", -2)
    if vr > 1.5:
        return Signal("volume_mild", f"温和放量({vr:.1f}x)", -1)
    if vr < 0.5:
        return Signal("volume_shrink", f"缩量蓄势({vr:.1f}x)", +1)
    return None
