"""动量信号 — ROC + 新高/新低 + ADX"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_momentum(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> list[Signal]:
    signals: list[Signal] = []
    for period, weight in [(5, 4), (10, 2), (20, 1)]:
        val = ti.roc_signal(period)
        if val > 5:
            signals.append(Signal(f"roc{period}_strong", f"ROC{period}强势({val:+.1f}%)", +weight))
        elif val > 2:
            signals.append(Signal(f"roc{period}_up", f"ROC{period}上升({val:+.1f}%)", +(weight // 2 or 1)))
        elif val < -5:
            signals.append(Signal(f"roc{period}_weak", f"ROC{period}弱势({val:+.1f}%)", -weight))
        elif val < -2:
            signals.append(Signal(f"roc{period}_down", f"ROC{period}下降({val:+.1f}%)", -(weight // 2 or 1)))
    accel = ti.momentum_acceleration(5, 20)
    if accel > 3:
        signals.append(Signal("accel_fast", "动量加速", +3))
    elif accel > 0:
        signals.append(Signal("accel_mild", "动量偏强", +1))
    elif accel < -3:
        signals.append(Signal("decel_fast", "动量减速", -3))
    elif accel < 0:
        signals.append(Signal("decel_mild", "动量偏弱", -1))
    for days, weight in [(5, 3), (10, 2), (20, 1)]:
        if ti.new_high(days):
            signals.append(Signal(f"high_{days}d", f"{days}日新高", +weight))
            break
    for days, weight in [(5, 3), (10, 2), (20, 1)]:
        if ti.new_low(days):
            signals.append(Signal(f"low_{days}d", f"{days}日新低", -weight))
            break
    if ti.adx(14) > 25:
        signals.append(Signal("adx_strong", "ADX强趋势", +2))
    return signals
