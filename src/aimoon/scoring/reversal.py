"""中期反转因子 — 严格条件，只捕捉确认反转的股票"""
from __future__ import annotations

import pandas as pd
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_reversal(
    ti: TechInd, *, code: str = "", ctx: dict | None = None,
) -> list[Signal]:
    """捕捉下跌后确认反转上涨的信号。条件严格，避免接飞刀。"""
    signals: list[Signal] = []
    close = ti._close
    if len(close) < 60:
        return signals

    # ── 1. 确认反转：回撤后站上MA20 + RSI回升 ──
    # 股票从60日高点回撤超过10%，现在价格站上MA20且RSI从低位回升
    mdd = ti.max_drawdown(60)
    ma20 = ti.ma(20)
    rsi = ti.rsi()
    if len(ma20) >= 2 and len(rsi) >= 3:
        c_now = close.iloc[-1]
        m_now = ma20.iloc[-1]
        m_prev = ma20.iloc[-2]
        rsi_now = rsi.iloc[-1]
        rsi_prev = rsi.iloc[-2]
        if not any(pd.isna(x) for x in [c_now, m_now, rsi_now, rsi_prev]):
            # 条件：大回撤 + 站上MA20 + RSI从低位上升
            if mdd < -10 and c_now > m_now and rsi_now > rsi_prev and rsi_now < 60:
                signals.append(Signal("reversal_confirmed",
                    f"确认反转(回撤{mdd:.0f}%,站上MA20,RSI{rsi_now:.0f})", +4))

    # ── 2. KDJ超卖区金叉（最可靠的反转信号之一） ──
    k, d, j = ti.kdj()
    if len(k) >= 2:
        j_now = j.iloc[-1]
        j_prev = j.iloc[-2]
        if not pd.isna(j_now) and not pd.isna(j_prev):
            if j_prev < 20 and j_now > j_prev and ti.kdj_golden_cross():
                # 额外确认：价格在MA20附近或上方
                if len(ma20) >= 1:
                    m = ma20.iloc[-1]
                    c = close.iloc[-1]
                    if not pd.isna(m) and c > m * 0.97:
                        signals.append(Signal("reversal_kdj_oversold_golden",
                            "KDJ超卖区金叉+价格企稳", +3))

    # ── 3. ROC20从负转正（中期趋势确认反转） ──
    if len(close) >= 25:
        roc20_series = ti.roc(20)
        if len(roc20_series) >= 5:
            r_now = roc20_series.iloc[-1]
            r_5ago = roc20_series.iloc[-5]
            if not pd.isna(r_now) and not pd.isna(r_5ago):
                if r_5ago < -5 and r_now > 2:
                    signals.append(Signal("reversal_roc20_turn",
                        f"中期动量转正(ROC20:{r_5ago:+.1f}%→{r_now:+.1f}%)", +3))

    # ── 4. 连续下跌后放量大阳线（恐慌出清后的反转） ──
    if len(close) >= 6:
        recent = close.iloc[-5:-1]
        down_days = sum(1 for i in range(1, len(recent)) if recent.iloc[i] < recent.iloc[i-1])
        today_return = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100 if close.iloc[-2] > 0 else 0
        vol_ratio = ti.volume_ratio()
        if down_days >= 3 and today_return > 2 and vol_ratio > 1.5:
            signals.append(Signal("reversal_vol_reversal",
                f"连跌后放量大阳(+{today_return:.1f}%,量比{vol_ratio:.1f}x)", +3))

    return signals
