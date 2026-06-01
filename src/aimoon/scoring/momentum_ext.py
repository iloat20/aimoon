"""扩展动量因子 — 多周期 ROC、波动率调整动量、OBV 趋势等"""
from __future__ import annotations

import pandas as pd
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_momentum_ext(
    ti: TechInd, *, code: str = "", ctx: dict | None = None,
) -> list[Signal]:
    signals: list[Signal] = []

    # ── 1. 多周期 ROC（3/40/60/120 日） ──
    for period, weight in [(3, 3), (40, 1), (60, 1), (120, 1)]:
        val = ti.roc_signal(period)
        if val >= 5:
            signals.append(Signal(f"roc{period}_strong", f"ROC{period}强势({val:+.1f}%)", +weight))
        elif val >= 2:
            signals.append(Signal(f"roc{period}_up", f"ROC{period}上升({val:+.1f}%)", +(weight // 2 or 1)))
        elif val <= -5:
            signals.append(Signal(f"roc{period}_weak", f"ROC{period}弱势({val:+.1f}%)", -weight))
        elif val <= -2:
            signals.append(Signal(f"roc{period}_down", f"ROC{period}下降({val:+.1f}%)", -(weight // 2 or 1)))

    # ── 2. RPS 扩展（更多周期的相对强度标记） ──
    # RPS 本身在 rps.py 的 compute_rps 中计算，这里用 ROC 近似
    # 周期: 3/40/60/120（补充 rps.py 中 5/10/15/20 的覆盖）
    rps_ext_periods = [3, 40, 60, 120]
    rps_strong_count = 0
    for p in rps_ext_periods:
        val = ti.roc_signal(p)
        if val > 8:  # 阈值对应"涨幅排前 10%"
            rps_strong_count += 1
    if rps_strong_count >= 3:
        signals.append(Signal("rps_ext_triple", f"长周期动量确认({rps_strong_count}/4)", +3))
    elif rps_strong_count >= 2:
        signals.append(Signal("rps_ext_double", f"长周期动量偏强({rps_strong_count}/4)", +2))

    # ── 3. 波动率调整动量（Sharpe-like） ──
    atr_val = ti.atr_pct(20)
    roc20 = ti.roc_signal(20)
    if atr_val > 0:
        vol_adj_mom = roc20 / atr_val
        if vol_adj_mom > 2:
            signals.append(Signal("vol_adj_mom_strong", "高性价比动量", +2))
        elif vol_adj_mom > 1:
            signals.append(Signal("vol_adj_mom_good", "动量性价比中等", +1))
        elif vol_adj_mom < -2:
            signals.append(Signal("vol_adj_mom_bad", "下跌且波动大", -2))

    # ── 4. 动量持久度 ──
    persistence = ti.momentum_persistence(20)
    if persistence > 0.7:
        signals.append(Signal("persist_high", f"动量持续({persistence:.0%})", +2))
    elif persistence > 0.6:
        signals.append(Signal("persist_good", f"动量较持续({persistence:.0%})", +1))
    elif persistence < 0.3:
        signals.append(Signal("persist_low", f"动量衰退({persistence:.0%})", -2))

    # ── 5. 收益偏度 ──
    skew = ti.return_skew(20)
    if skew > 0.5:
        signals.append(Signal("skew_pos", "正偏度(右尾厚)", +1))
    elif skew < -0.5:
        signals.append(Signal("skew_neg", "负偏度(左尾厚)", -1))

    # ── 6. 上涨/下跌成交量比 ──
    ud_ratio = ti.up_down_volume_ratio(20)
    if ud_ratio > 1.5:
        signals.append(Signal("ud_vol_high", "上涨量大于下跌量", +2))
    elif ud_ratio > 1.2:
        signals.append(Signal("ud_vol_good", "量价配合", +1))
    elif ud_ratio < 0.7:
        signals.append(Signal("ud_vol_low", "下跌放量", -2))

    # ── 7. OBV 趋势 ──
    obv_slope = ti.obv_slope(10)
    if obv_slope > 5:
        signals.append(Signal("obv_up", "OBV上升(资金流入)", +2))
    elif obv_slope > 0:
        signals.append(Signal("obv_mild_up", "OBV偏强", +1))
    elif obv_slope < -5:
        signals.append(Signal("obv_down", "OBV下降(资金流出)", -2))

    # ── 8. VWAP 偏离 ──
    vwap_dev = ti.vwap_deviation(20)
    if vwap_dev > 3:
        signals.append(Signal("vwap_above", "价格强于VWAP", +2))
    elif vwap_dev > 0:
        signals.append(Signal("vwap_mild_above", "价格略强于VWAP", +1))
    elif vwap_dev < -3:
        signals.append(Signal("vwap_below", "价格弱于VWAP", -2))

    # ── 9. 最大回撤恢复 ──
    recovery = ti.drawdown_recovery(60)
    mdd = ti.max_drawdown(60)
    if mdd < -20 and recovery > 50:
        signals.append(Signal("recovery_strong", "深跌后强势反弹", +2))
    elif mdd < -10 and recovery > 30:
        signals.append(Signal("recovery_good", "回撤后恢复较好", +1))

    # ── 10. 新高新低比 ──
    highs, lows = ti.high_low_count(60)
    net = highs - lows
    if net >= 3:
        signals.append(Signal("hl_net_high", f"净新高({net})", +2))
    elif net >= 1:
        signals.append(Signal("hl_net_mild", f"新高略多({net})", +1))
    elif net <= -3:
        signals.append(Signal("hl_net_low", f"净新低({net})", -2))

    # ── 11. 动量反转过滤（超跌保护） ──
    roc5 = ti.roc_signal(5)
    if roc5 < -10:
        signals.append(Signal("crash_filter", f"5日暴跌{roc5:.1f}%,超跌风险", -3))

    # ── 12. 动量耗尽过滤（布林+ROC，替代原 RSI 依赖） ──
    boll_pos = ti.bollinger_position()
    if boll_pos == "above" and roc5 > 8:
        signals.append(Signal("momentum_exhaustion", "动量过热(触上轨+短期急涨)", -3))
    elif boll_pos == "above" and roc5 > 5:
        signals.append(Signal("momentum_overextended", "短期过快上涨", -2))

    # ── 13. 短期均值回归信号（IC 有效：反向预测） ──
    roc10 = ti.roc_signal(10)
    if roc5 < -8 and roc10 < -5:
        signals.append(Signal("mean_rev_oversold", f"短期超跌(ROC5:{roc5:.1f}%,ROC10:{roc10:.1f}%)", +2))
    elif roc5 > 12 and roc10 > 8:
        signals.append(Signal("mean_rev_overbought", f"短期超买(ROC5:{roc5:.1f}%,ROC10:{roc10:.1f}%)", -2))

    # ── 14. 量价背离（IC 有效：量是反向指标） ──
    vr = ti.volume_ratio()
    if roc5 > 3 and vr < 0.7:
        signals.append(Signal("vp_bull_diverge", "量价背离(价涨量缩)", +2))
    elif roc5 < -3 and vr > 1.5:
        signals.append(Signal("vp_bear_diverge", "量价背离(价跌量增)", -2))

    return signals
