"""Lightweight technical indicators computed from K-line data.

No talib/pandas_ta dependency. Computes MA/MACD/RSI/KDJ/
Bollinger/support-resistance/trend and produces a rule-based
1-5 technical score for the report.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..models.stock import KlineData


def _to_df(kline: KlineData) -> pd.DataFrame:
    """Convert KlineData bars to a pandas DataFrame indexed by date."""
    if not kline.bars:
        return pd.DataFrame()
    df = pd.DataFrame([b.model_dump() for b in kline.bars])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _kdj(df: pd.DataFrame, n: int = 9) -> dict[str, Any]:
    """Compute KDJ (stochastic oscillator) for the latest bar.

    Returns dict with k, d, j values and cross/dull signals, or empty dict.
    """
    if len(df) < n + 1:
        return {}
    high = df["high"]
    low = df["low"]
    close = df["close"]

    hh = high.rolling(n).max()
    ll = low.rolling(n).min()
    rsv = (close - ll) / (hh - ll).replace(0, pd.NA) * 100

    k_series: list[float] = []
    d_series: list[float] = []
    prev_k = 50.0
    prev_d = 50.0
    for v in rsv.dropna():
        if pd.isna(v):
            continue
        k = 2 / 3 * prev_k + 1 / 3 * v
        d = 2 / 3 * prev_d + 1 / 3 * k
        k_series.append(k)
        d_series.append(d)
        prev_k = k
        prev_d = d

    if len(k_series) < 3:
        return {}

    k_val = round(k_series[-1], 2)
    d_val = round(d_series[-1], 2)
    j_val = round(3 * k_series[-1] - 2 * d_series[-1], 2)

    prev_k_val = k_series[-2]
    prev_d_val = d_series[-2]
    prev2_k_val = k_series[-3] if len(k_series) >= 3 else prev_k_val

    result: dict[str, Any] = {
        "kdj_k": k_val,
        "kdj_d": d_val,
        "kdj_j": j_val,
    }

    # Cross detection
    if prev_k_val <= prev_d_val and k_val > d_val:
        result["kdj_cross"] = "金叉"
    elif prev_k_val >= prev_d_val and k_val < d_val:
        result["kdj_cross"] = "死叉"
    else:
        result["kdj_cross"] = "无"

    # Dull detection: K/D in extreme zone with < 3% change over 3 periods
    in_extreme = (k_val > 80 and d_val > 80) or (k_val < 20 and d_val < 20)
    k_range = abs(k_val - prev2_k_val) if not pd.isna(prev2_k_val) else 999
    if in_extreme and k_range < 3:
        result["kdj_dull"] = True
        result["kdj_cross"] = "钝化"

    return result


def compute_indicators(kline: KlineData) -> dict[str, Any]:
    """Return a snapshot of technical indicators at the latest bar.

    Returns an empty dict if insufficient data. All numeric values are floats;
    cross/golden/death signals are bools; trend is one of "上升"/"下降"/"震荡".
    """
    df = _to_df(kline)
    ind: dict[str, Any] = {"bars": len(df)}

    if len(df) < 10:
        return ind

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # --- Moving Averages ---
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = (
        close.rolling(60).mean()
        if len(df) >= 60
        else close.rolling(min(len(df), 60)).mean()
    )

    last_close = float(close.iloc[-1])
    ind.update(
        {
            "price": round(last_close, 2),
            "ma5": round(float(ma5.iloc[-1]), 2) if pd.notna(ma5.iloc[-1]) else 0.0,
            "ma10": round(float(ma10.iloc[-1]), 2) if pd.notna(ma10.iloc[-1]) else 0.0,
            "ma20": round(float(ma20.iloc[-1]), 2) if pd.notna(ma20.iloc[-1]) else 0.0,
            "ma60": round(float(ma60.iloc[-1]), 2) if pd.notna(ma60.iloc[-1]) else 0.0,
            "above_ma5": bool(
                last_close > float(ma5.iloc[-1]) if pd.notna(ma5.iloc[-1]) else False
            ),
            "above_ma20": bool(
                last_close > float(ma20.iloc[-1]) if pd.notna(ma20.iloc[-1]) else False
            ),
            "ma_golden_cross": bool(
                pd.notna(ma5.iloc[-1])
                and pd.notna(ma10.iloc[-1])
                and pd.notna(ma5.iloc[-2])
                and pd.notna(ma10.iloc[-2])
                and ma5.iloc[-1] > ma10.iloc[-1]
                and ma5.iloc[-2] <= ma10.iloc[-2]
            ),
        }
    )

    # --- Trend ---
    if pd.notna(ma5.iloc[-1]) and pd.notna(ma10.iloc[-1]) and pd.notna(ma20.iloc[-1]):
        if ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1]:
            ind["trend"] = "上升"
        elif ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1]:
            ind["trend"] = "下降"
        else:
            ind["trend"] = "震荡"
    else:
        ind["trend"] = "震荡"

    # --- MACD (12, 26, 9) ---
    if len(df) >= 35:
        dif = _ema(close, 12) - _ema(close, 26)
        dea = _ema(dif, 9)
        hist = (dif - dea) * 2
        ind.update(
            {
                "macd_dif": round(float(dif.iloc[-1]), 3),
                "macd_dea": round(float(dea.iloc[-1]), 3),
                "macd_hist": round(float(hist.iloc[-1]), 3),
                "macd_above_zero": bool(dif.iloc[-1] > 0),
                "macd_golden_cross": bool(  # pylint: disable=R1716
                    hist.iloc[-1] > 0 and hist.iloc[-2] <= 0
                ),
            }
        )

    # --- KDJ (9, 3, 3) ---
    kdj = _kdj(df)
    ind.update(kdj)

    # --- RSI (6, 14) ---
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    for period in (6, 14):
        if len(df) >= period + 1:
            avg_gain = gain.rolling(period).mean()
            avg_loss = loss.rolling(period).mean()
            rs = avg_gain / avg_loss.replace(0, pd.NA)
            rsi = 100 - (100 / (1 + rs))
            v = rsi.iloc[-1]
            ind[f"rsi{period}"] = round(float(v), 1) if pd.notna(v) else 50.0
        else:
            ind[f"rsi{period}"] = 50.0
    ind["rsi_oversold"] = bool(ind.get("rsi14", 50) < 30)
    ind["rsi_overbought"] = bool(ind.get("rsi14", 50) > 70)

    # --- Bollinger Bands (20, 2) ---
    if len(df) >= 20:
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        last_upper = float(upper.iloc[-1]) if pd.notna(upper.iloc[-1]) else last_close
        last_lower = float(lower.iloc[-1]) if pd.notna(lower.iloc[-1]) else last_close
        last_mid = float(mid.iloc[-1]) if pd.notna(mid.iloc[-1]) else last_close
        ind.update(
            {
                "boll_upper": round(last_upper, 2),
                "boll_mid": round(last_mid, 2),
                "boll_lower": round(last_lower, 2),
                "boll_position": (
                    "above"
                    if last_close > last_upper
                    else "below" if last_close < last_lower else "middle"
                ),
            }
        )

    # --- Support / Resistance (60-day range) ---
    lookback = min(60, len(df))
    recent_low = float(low.tail(lookback).min())
    recent_high = float(high.tail(lookback).max())
    ind.update(
        {
            "support": round(recent_low, 2),
            "resistance": round(recent_high, 2),
        }
    )

    # --- Volume ratio (today vs MA20) ---
    if len(df) >= 21:
        vol_ma20 = volume.rolling(20).mean()
        v0 = float(volume.iloc[-1])
        vm = float(vol_ma20.iloc[-1]) if pd.notna(vol_ma20.iloc[-1]) else 0
        ind["volume_ratio"] = round(v0 / vm, 2) if vm > 0 else 1.0
    else:
        ind["volume_ratio"] = 1.0

    # --- Recent returns ---
    if len(df) >= 61:
        ind["ret_5d"] = round(float((close.iloc[-1] / close.iloc[-6] - 1) * 100), 2)
        ind["ret_20d"] = round(float((close.iloc[-1] / close.iloc[-21] - 1) * 100), 2)
        ind["ret_60d"] = round(float((close.iloc[-1] / close.iloc[-61] - 1) * 100), 2)
    elif len(df) >= 21:
        ind["ret_5d"] = round(float((close.iloc[-1] / close.iloc[-6] - 1) * 100), 2)
        ind["ret_20d"] = round(float((close.iloc[-1] / close.iloc[-21] - 1) * 100), 2)
    elif len(df) >= 6:
        ind["ret_5d"] = round(float((close.iloc[-1] / close.iloc[-6] - 1) * 100), 2)

    return ind


def technical_score(ind: dict[str, Any]) -> tuple[int, str, float, float, str]:
    """Produce a rule-based 1-5 technical score.

    Returns (score 1-5, detail_text, support_price, resistance_price, trend).
    Weights: MA趋势 30% + MACD 25% + RSI 15% + 量能 15% + 布林位置 15%.
    """
    if not ind or "price" not in ind:
        return 3, "K线数据不足，技术面暂无法分析。", 0.0, 0.0, "震荡"

    # Sub-scores on a 1-5 scale
    # 1) MA trend
    trend = ind.get("trend", "震荡")
    above_ma5 = ind.get("above_ma5", False)
    above_ma20 = ind.get("above_ma20", False)
    if trend == "上升" and above_ma5 and above_ma20:
        ma_sub = 5
    elif trend == "上升" and above_ma5:
        ma_sub = 4
    elif trend == "震荡" and above_ma20:
        ma_sub = 3
    elif trend == "下降" and above_ma5:
        ma_sub = 2
    elif trend == "下降":
        ma_sub = 1
    else:
        ma_sub = 3

    # 2) MACD
    macd_above_zero = ind.get("macd_above_zero")
    macd_golden = ind.get("macd_golden_cross")
    macd_hist = ind.get("macd_hist", 0)
    if macd_above_zero is None:
        macd_sub = 3
    elif macd_golden and macd_above_zero:
        macd_sub = 5
    elif macd_above_zero and macd_hist > 0:
        macd_sub = 4
    elif macd_hist > 0:
        macd_sub = 3
    elif macd_above_zero:
        macd_sub = 3
    else:
        macd_sub = 2

    # 3) RSI
    rsi14 = ind.get("rsi14", 50)
    if rsi14 < 30:
        rsi_sub = 4  # oversold → rebound potential
    elif rsi14 > 70:
        rsi_sub = 2  # overbought → pullback risk
    elif 45 <= rsi14 <= 60:
        rsi_sub = 4
    elif 40 <= rsi14 <= 65:
        rsi_sub = 3
    else:
        rsi_sub = 3

    # 4) Volume
    vr = ind.get("volume_ratio", 1.0)
    if vr > 2.0 and ind.get("ret_5d", 0) > 0:
        vol_sub = 5  # 量价齐升
    elif vr > 1.5 and ind.get("ret_5d", 0) > 0:
        vol_sub = 4
    elif vr > 1.5 and ind.get("ret_5d", 0) < -2:
        vol_sub = 2  # 放量下跌
    elif vr < 0.5:
        vol_sub = 3  # 缩量观望
    elif vr >= 1.0:
        vol_sub = 4
    else:
        vol_sub = 3

    # 5) Bollinger position
    pos = ind.get("boll_position", "middle")
    if pos == "above":
        boll_sub = 4  # 突破上轨，强势
    elif pos == "below":
        boll_sub = 2  # 跌破下轨，弱势
    else:
        boll_sub = 3

    # Weighted total on 1-5 scale
    total = (
        ma_sub * 0.30
        + macd_sub * 0.25
        + rsi_sub * 0.15
        + vol_sub * 0.15
        + boll_sub * 0.15
    )
    score = max(1, min(5, round(total)))

    # Build human-readable detail
    lines: list[str] = []

    # MA alignment
    ma5 = ind.get("ma5", 0)
    ma10 = ind.get("ma10", 0)
    ma20 = ind.get("ma20", 0)
    ma60 = ind.get("ma60", 0)
    price = ind.get("price", 0)

    # MA排列形态
    if ma5 < ma10 < ma20 < ma60 and all(v > 0 for v in (ma5, ma10, ma20, ma60)):
        ma_morph = "空头排列"
    elif ma5 > ma10 > ma20 > ma60 and all(v > 0 for v in (ma5, ma10, ma20, ma60)):
        ma_morph = "多头排列"
    else:
        ma_morph = "粘合交织"

    ma_parts = [f"MA5={ma5}", f"MA10={ma10}", f"MA20={ma20}"]
    if ma60 > 0:
        ma_parts.append(f"MA60={ma60}")

    rel = ""
    rel_parts = []
    if price and ma5:
        rel_parts.append(f"{'上穿' if price > ma5 else '下破'}MA5")
    if price and ma20:
        rel_parts.append(f"{'上穿' if price > ma20 else '下破'}MA20")
    if rel_parts:
        rel = f"，当前{','.join(rel_parts)}"

    lines.append(f"均线{ma_morph}（{' '.join(ma_parts)}）{rel}")

    # MACD
    if "macd_dif" in ind:
        macd_str = (
            f"MACD DIF={ind['macd_dif']} DEA={ind['macd_dea']} "
            f"柱={ind['macd_hist']}，{macd_golden and '金叉' or '非金叉'}"
            f"（零轴{'上' if macd_above_zero else '下'}）"
        )
        lines.append(macd_str)

    # KDJ
    if "kdj_k" in ind:
        kdj_cross = ind.get("kdj_cross", "无")
        kdj_dull = ind.get("kdj_dull", False)
        kdj_str = f"KDJ K={ind['kdj_k']} D={ind['kdj_d']} J={ind['kdj_j']}"
        if kdj_dull:
            kdj_str += "，钝化"
        elif kdj_cross in ("金叉", "死叉"):
            kdj_str += f"，{kdj_cross}"
        lines.append(kdj_str)

    # RSI
    if "rsi14" in ind:
        lines.append(f"RSI14={ind['rsi14']}")

    # Bollinger
    if "boll_upper" in ind:
        pos_cn = {"above": "上轨上方", "below": "下轨下方", "middle": "中轨附近"}
        pos_label = pos_cn.get(ind.get("boll_position", ""), "未知")
        boll_width = (
            round(ind["boll_upper"] - ind["boll_lower"], 2)
            if ind.get("boll_lower")
            else 0
        )
        lines.append(f"布林{pos_label}（带宽{boll_width})")

    # Volume
    vr = ind.get("volume_ratio", 1.0)
    vol_note = f"量比{vr}"
    if vr < 0.5:
        vol_note += "缩量"
    elif vr > 1.5:
        vol_note += "放量"
    else:
        vol_note += "常态"
    lines.append(vol_note)

    # Returns
    ret_parts = []
    if "ret_5d" in ind:
        ret_parts.append(f"5日{ind['ret_5d']}%")
    if "ret_20d" in ind:
        ret_parts.append(f"20日{ind['ret_20d']}%")
    if ret_parts:
        lines.append(f"涨跌幅 {' '.join(ret_parts)}")

    # Conclusion line
    support = ind.get("support", 0)
    resistance = ind.get("resistance", 0)
    reversal_signals: list[str] = []
    if vr is not None and vr < 0.6:
        reversal_signals.append("缩量")
    if ind.get("boll_position") == "middle" and ind.get("macd_hist", 0) is not None:
        boll_width_val = ind.get("boll_upper", 0) - ind.get("boll_lower", 0)
        if boll_width_val and price and boll_width_val / price < 0.05:
            reversal_signals.append("布林收口")
    if "kdj_dull" in ind and ind["kdj_dull"]:
        reversal_signals.append("KDJ钝化")
    reversal_str = (
        f"，变盘信号：{' '.join(reversal_signals)}" if reversal_signals else ""
    )
    sup_str = f"支撑{support}" if support else ""
    res_str = f"阻力{resistance}" if resistance else ""
    lines.insert(0, f"短期{trend}趋势{reversal_str}")
    if sup_str or res_str:
        lines.insert(0, f"关键位：{' '.join(filter(None, [sup_str, res_str]))}")
        if price:
            lines.insert(0, f"当前价{price}")

    detail = "；".join(lines) + "。"

    return score, detail, ind.get("support", 0.0), ind.get("resistance", 0.0), trend
