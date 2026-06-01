"""扩展趋势因子 — 多均线排列、ADX 方向、MACD 柱体等"""
from __future__ import annotations

import pandas as pd

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_trend_ext(
    ti: TechInd, *, code: str = "", ctx: dict | None = None,
) -> list[Signal]:
    signals: list[Signal] = []

    # ── 1. 多均线排列度（MA5/10/20/60） ──
    ma5 = ti.ma(5)
    ma10 = ti.ma(10)
    ma20 = ti.ma(20)
    ma60 = ti.ma(60)
    if not any(pd.isna(v.iloc[-1]) for v in [ma5, ma10, ma20, ma60]):
        bullish_pairs = 0
        if ma5.iloc[-1] > ma10.iloc[-1]:
            bullish_pairs += 1
        if ma10.iloc[-1] > ma20.iloc[-1]:
            bullish_pairs += 1
        if ma20.iloc[-1] > ma60.iloc[-1]:
            bullish_pairs += 1
        if bullish_pairs == 3:
            signals.append(Signal("ma_align_bull", "均线完美多头排列", +3))
        elif bullish_pairs == 2:
            signals.append(Signal("ma_align_partial", "均线偏多排列", +1))
        elif bullish_pairs == 0:
            signals.append(Signal("ma_align_bear", "均线空头排列", -2))

    # ── 2. 价格相对均线位置 ──
    close = float(ti._close.iloc[-1])
    above_ma20 = not pd.isna(ma20.iloc[-1]) and close > float(ma20.iloc[-1])
    above_ma60 = not pd.isna(ma60.iloc[-1]) and close > float(ma60.iloc[-1])
    if above_ma20 and above_ma60:
        signals.append(Signal("above_ma20_60", "价格在MA20和MA60上方", +2))
    elif above_ma20:
        signals.append(Signal("above_ma20", "价格在MA20上方", +1))
    elif not above_ma20 and not above_ma60:
        signals.append(Signal("below_ma20_60", "价格在MA20和MA60下方", -2))

    # ── 3. ADX 趋势方向（+DI vs -DI） ──
    adx_val, plus_di, minus_di = _adx_detail(ti, 14)
    if adx_val > 25:
        if plus_di > minus_di:
            signals.append(Signal("adx_bull_trend", f"ADX强多头趋势({adx_val:.0f})", +2))
        else:
            signals.append(Signal("adx_bear_trend", f"ADX强空头趋势({adx_val:.0f})", -2))

    # ── 4. MACD 柱体连续方向 ──
    _, _, hist = ti.macd()
    if len(hist) >= 5:
        recent = hist.iloc[-5:]
        red_count = sum(1 for v in recent if not pd.isna(v) and v > 0)
        green_count = sum(1 for v in recent if not pd.isna(v) and v < 0)
        if red_count >= 4:
            signals.append(Signal("macd_red_streak", f"MACD连续{red_count}根红柱", +2))
        elif green_count >= 4:
            signals.append(Signal("macd_green_streak", f"MACD连续{green_count}根绿柱", -2))

    # ── 5. EMA20 斜率 ──
    ema20 = ti.ema(20)
    if len(ema20) >= 5:
        slope = (float(ema20.iloc[-1]) - float(ema20.iloc[-5])) / float(ema20.iloc[-5]) * 100 if float(ema20.iloc[-5]) > 0 else 0
        if slope > 2:
            signals.append(Signal("ema_slope_up", f"EMA20上升({slope:+.1f}%)", +1))
        elif slope < -2:
            signals.append(Signal("ema_slope_down", f"EMA20下降({slope:+.1f}%)", -1))

    return signals


def _adx_detail(ti: TechInd, period: int = 14) -> tuple[float, float, float]:
    """返回 (ADX, +DI, -DI)。"""
    if len(ti._close) < period * 2:
        return 0.0, 0.0, 0.0
    high = ti._high
    low = ti._low
    close = ti._close
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean().replace(0, 1e-10)
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)
    adx_val = dx.ewm(span=period, adjust=False).mean()
    return (
        float(adx_val.iloc[-1]) if not pd.isna(adx_val.iloc[-1]) else 0.0,
        float(plus_di.iloc[-1]) if not pd.isna(plus_di.iloc[-1]) else 0.0,
        float(minus_di.iloc[-1]) if not pd.isna(minus_di.iloc[-1]) else 0.0,
    )
