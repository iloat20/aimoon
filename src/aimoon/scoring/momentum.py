"""动量信号 — 适配 A 股的精简动量指标

A股特征：
- 散户市，短期反转效应强（5-10天涨多了会跌）
- 中期动量有效（20-60天趋势跟随）
- 超跌反弹在A股很常见

设计原则：每个信号捕获独立信息，不重复计算
- 短期反转（5天 ROC，反向）
- 中期动量（20天 ROC，正向）
- 长期趋势（60天 ROC，正向）
- 动量加减速（ROC 变化率）
- 波动率调整动量（ROC/ATR）
- 超跌/超买（综合 5天+10天，反向）
- 新高新低（方向确认）
"""

from __future__ import annotations

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_momentum(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> list[Signal]:
    signals: list[Signal] = []

    roc5 = ti.roc_signal(5)
    roc10 = ti.roc_signal(10)
    roc20 = ti.roc_signal(20)
    roc60 = ti.roc_signal(60)

    # ── 1. 短期反转（A股最强因子，5天 ROC 反向） ──
    # 只用 5 天，不与 10 天重复
    if roc5 > 10:
        signals.append(
            Signal("reversal_hot", f"5日暴涨{roc5:+.1f}%(反转信号)", -4, category="momentum")
        )
    elif roc5 > 6:
        signals.append(
            Signal("reversal_warm", f"5日大涨{roc5:+.1f}%(反转信号)", -2, category="momentum")
        )
    elif roc5 < -10:
        signals.append(
            Signal("reversal_oversold", f"5日暴跌{roc5:+.1f}%(反弹信号)", +4, category="momentum")
        )
    elif roc5 < -6:
        signals.append(
            Signal("reversal_weak", f"5日大跌{roc5:+.1f}%(反弹信号)", +2, category="momentum")
        )

    # ── 2. 中期动量（20天 ROC，正向跟随） ──
    if roc20 > 10:
        signals.append(
            Signal("mom_20d_strong", f"20日强动量({roc20:+.1f}%)", +3, category="momentum")
        )
    elif roc20 > 5:
        signals.append(Signal("mom_20d_up", f"20日上升({roc20:+.1f}%)", +2, category="momentum"))
    elif roc20 < -10:
        signals.append(Signal("mom_20d_weak", f"20日弱势({roc20:+.1f}%)", -3, category="momentum"))
    elif roc20 < -5:
        signals.append(Signal("mom_20d_down", f"20日下降({roc20:+.1f}%)", -2, category="momentum"))

    # ── 3. 长期趋势（60天 ROC，正向） ──
    if roc60 > 20:
        signals.append(
            Signal("mom_60d_strong", f"60日强趋势({roc60:+.1f}%)", +2, category="momentum")
        )
    elif roc60 > 10:
        signals.append(Signal("mom_60d_up", f"60日上升({roc60:+.1f}%)", +1, category="momentum"))
    elif roc60 < -20:
        signals.append(Signal("mom_60d_weak", f"60日弱势({roc60:+.1f}%)", -2, category="momentum"))

    # ── 4. 动量加减速（ROC 变化率，捕获动量的二阶导） ──
    accel = ti.momentum_acceleration(5, 20)
    if accel > 5:
        signals.append(Signal("accel_fast", f"动量加速({accel:+.1f})", +3, category="momentum"))
    elif accel > 2:
        signals.append(Signal("accel_mild", f"动量偏强({accel:+.1f})", +1, category="momentum"))
    elif accel < -5:
        signals.append(Signal("decel_fast", f"动量急减速({accel:+.1f})", -3, category="momentum"))
    elif accel < -2:
        signals.append(Signal("decel_mild", f"动量偏弱({accel:+.1f})", -1, category="momentum"))

    # ── 5. 波动率调整动量（Sharpe-like，A股有效） ──
    atr_val = ti.atr_pct(20)
    if atr_val > 0:
        vol_adj = roc20 / atr_val
        if vol_adj > 2.5:
            signals.append(
                Signal("vol_adj_strong", f"高性价比动量({vol_adj:.1f})", +3, category="momentum")
            )
        elif vol_adj > 1.5:
            signals.append(
                Signal("vol_adj_good", f"动量性价比中等({vol_adj:.1f})", +1, category="momentum")
            )
        elif vol_adj < -2.5:
            signals.append(
                Signal("vol_adj_bad", f"下跌且高波动({vol_adj:.1f})", -3, category="momentum")
            )

    # ── 6. 超跌反弹（综合 5天+10天，只在极端情况触发） ──
    if roc5 < -8 and roc10 < -5:
        signals.append(
            Signal(
                "oversold_bounce",
                f"超跌反弹(ROC5:{roc5:+.1f}%,ROC10:{roc10:+.1f}%)",
                +3,
                category="momentum",
            )
        )
    elif roc5 > 12 and roc10 > 8:
        signals.append(
            Signal(
                "overbought_react",
                f"超买回调(ROC5:{roc5:+.1f}%,ROC10:{roc10:+.1f}%)",
                -3,
                category="momentum",
            )
        )

    # ── 7. 新高/新低（方向确认，只取最短周期） ──
    for days, weight in [(5, 3), (10, 2), (20, 1)]:
        if ti.new_high(days):
            signals.append(Signal(f"high_{days}d", f"{days}日新高", +weight, category="momentum"))
            break
    for days, weight in [(5, 3), (10, 2), (20, 1)]:
        if ti.new_low(days):
            signals.append(Signal(f"low_{days}d", f"{days}日新低", -weight, category="momentum"))
            break

    return signals
