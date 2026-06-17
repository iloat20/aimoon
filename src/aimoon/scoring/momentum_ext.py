"""扩展动量因子 — 捕获 momentum.py 未覆盖的独立信息

与 momentum.py 的分工：
- momentum.py：单周期 ROC 反转/动量/趋势
- momentum_ext.py：多周期一致性、量价关系、资金流向、尾部风险

设计原则：不重复 momentum.py 已有的信号
"""

from __future__ import annotations

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_momentum_ext(
    ti: TechInd,
    *,
    code: str = "",
    ctx: dict | None = None,
) -> list[Signal]:
    signals: list[Signal] = []

    roc5 = ti.roc_signal(5)

    # ── 1. 多周期一致性（独立于单周期信号） ──
    # 4 个周期同向 → 强确认信号
    up_count = sum(1 for p in [5, 10, 20, 60] if ti.roc_signal(p) > 2)
    down_count = sum(1 for p in [5, 10, 20, 60] if ti.roc_signal(p) < -2)

    if up_count >= 4:
        signals.append(Signal("align_up", "全周期动量一致看多", +4, category="momentum"))
    elif up_count >= 3:
        signals.append(Signal("align_mild_up", "多数周期看多", +2, category="momentum"))
    elif down_count >= 4:
        signals.append(Signal("align_down", "全周期动量一致看空", -4, category="momentum"))
    elif down_count >= 3:
        signals.append(Signal("align_mild_down", "多数周期看空", -2, category="momentum"))

    # ── 2. 动量持续性（上涨日比例，独立于 ROC 方向） ──
    persistence = ti.momentum_persistence(20)
    if persistence > 0.7:
        signals.append(
            Signal("persist_high", f"动量持续({persistence:.0%})", +2, category="momentum")
        )
    elif persistence > 0.6:
        signals.append(
            Signal("persist_good", f"动量较持续({persistence:.0%})", +1, category="momentum")
        )
    elif persistence < 0.3:
        signals.append(
            Signal("persist_low", f"动量衰退({persistence:.0%})", -2, category="momentum")
        )

    # ── 3. 上涨/下跌成交量比（量价关系，独立于价格动量） ──
    ud_ratio = ti.up_down_volume_ratio(20)
    if ud_ratio > 1.5:
        signals.append(Signal("ud_vol_high", "上涨量大于下跌量", +2, category="momentum"))
    elif ud_ratio > 1.2:
        signals.append(Signal("ud_vol_good", "量价配合", +1, category="momentum"))
    elif ud_ratio < 0.7:
        signals.append(Signal("ud_vol_low", "下跌放量", -2, category="momentum"))

    # ── 4. OBV 资金流向（独立于价格） ──
    obv_slope = ti.obv_slope(10)
    if obv_slope > 5:
        signals.append(Signal("obv_up", "OBV上升(资金流入)", +2, category="momentum"))
    elif obv_slope > 0:
        signals.append(Signal("obv_mild_up", "OBV偏强", +1, category="momentum"))
    elif obv_slope < -5:
        signals.append(Signal("obv_down", "OBV下降(资金流出)", -2, category="momentum"))

    # ── 5. 收益偏度（尾部风险，独立于方向） ──
    skew = ti.return_skew(20)
    if skew > 0.5:
        signals.append(Signal("skew_pos", "正偏度(右尾厚)", +1, category="momentum"))
    elif skew < -0.5:
        signals.append(Signal("skew_neg", "负偏度(左尾厚)", -1, category="momentum"))

    # ── 6. 量价背离（价涨量缩/价跌量增，反转确认） ──
    vr = ti.volume_ratio()
    if roc5 > 3 and vr < 0.7:
        signals.append(Signal("vp_diverge_bear", "量价背离(价涨量缩)", -2, category="momentum"))
    elif roc5 < -5 and vr > 1.5:
        signals.append(Signal("vp_diverge_bull", "恐慌抛售(价跌量增)", +2, category="momentum"))

    # ── 7. 动量过热/暴跌过滤（极端情况保护） ──
    boll_pos = ti.bollinger_position()
    if boll_pos == "above" and roc5 > 8:
        signals.append(
            Signal("momentum_exhaustion", "动量过热(触上轨+急涨)", -3, category="momentum")
        )
    if roc5 < -10:
        signals.append(Signal("crash_filter", f"5日暴跌{roc5:.1f}%", -3, category="momentum"))

    # ── 8. 最大回撤恢复（深跌后反弹能力） ──
    recovery = ti.drawdown_recovery(60)
    mdd = ti.max_drawdown(60)
    if mdd < -20 and recovery > 50:
        signals.append(Signal("recovery_strong", "深跌后强势反弹", +2, category="momentum"))
    elif mdd < -10 and recovery > 30:
        signals.append(Signal("recovery_good", "回撤后恢复较好", +1, category="momentum"))

    return signals
