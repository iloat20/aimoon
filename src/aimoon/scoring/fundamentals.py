"""基本面因子评分 — PE/PB 估值信号。"""

from __future__ import annotations

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_fundamentals(
    ti: TechInd,
    *,
    code: str = "",
    ctx: dict | None = None,
) -> list[Signal] | None:
    """基于 PE/PB 的估值评分。"""
    # No ctx needed

    pe = 0.0
    pb = 0.0
    if ctx is not None:
        row = ctx.get("spot_row") if isinstance(ctx, dict) else None
        if row is not None:
            pe = _get_float(row, "pe")
            pb = _get_float(row, "pb")

    if pe == 0.0 and pb == 0.0:
        return None

    signals: list[Signal] = []

    # PE scoring
    if pe > 0:
        if 5 <= pe <= 20:
            signals.append(Signal("pe_low", f"低PE({pe:.1f})", +2, category="alpha"))
        elif 20 < pe <= 40:
            signals.append(Signal("pe_mid", f"中PE({pe:.1f})", +1, category="alpha"))
        elif pe > 80:
            signals.append(Signal("pe_high", f"高PE({pe:.1f})", -2, category="alpha"))
    elif pe < 0:
        signals.append(Signal("pe_negative", "亏损(PE<0)", -3, category="alpha"))

    # PB scoring
    if pb > 0:
        if pb <= 3:
            signals.append(Signal("pb_low", f"低PB({pb:.1f})", +1, category="alpha"))
        elif pb > 10:
            signals.append(Signal("pb_high", f"高PB({pb:.1f})", -2, category="alpha"))

    return signals if signals else None


def _get_float(row, key: str) -> float:
    """兼容 pd.Series 和 dict。"""
    try:
        val = row.get(key, 0.0) if hasattr(row, "get") else getattr(row, key, 0.0)
        return float(val) if val is not None and val == val else 0.0
    except (TypeError, ValueError):
        return 0.0




