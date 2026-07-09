"""技术指标计算工具（纯函数）。

输入 KlineData + CapitalFlowData,输出均线/MACD/RSI/布林带/量比/主力净流入/趋势判断。
失败或数据不足时返回 ``{"__partial__": "<reason>"}``（上游降级契约）。
"""
from __future__ import annotations

import logging

import numpy as np

from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.kline import KlineData

logger = logging.getLogger(__name__)

MIN_BARS = 20


def run(kline: KlineData | None, capital_flow: CapitalFlowData | None) -> dict[str, object]:
    try:
        if kline is None or not kline.bars:
            return {"__partial__": "insufficient_bars", "bar_count": 0}

        closes = np.array([b.close for b in kline.bars], dtype=np.float64)
        volumes = np.array([b.volume for b in kline.bars], dtype=np.float64)
        bar_count = int(len(closes))

        if bar_count < MIN_BARS:
            return {"__partial__": "insufficient_bars", "bar_count": bar_count}

        ma5 = _sma(closes, 5)
        ma10 = _sma(closes, 10)
        ma20 = _sma(closes, 20)
        ma60 = _sma(closes, 60)[-1] if bar_count >= 60 else ma20[-1]
        rsi14 = _rsi(closes, 14)
        macd = _macd(closes)
        bb = _bollinger(closes, 20)
        volume_ratio_5 = _volume_ratio(volumes, 5)
        trend = _detect_trend(closes, ma5, ma20)

        flow = capital_flow or CapitalFlowData(symbol=getattr(kline, "symbol", ""))
        return {
            "bar_count": bar_count,
            "ma5": _round(ma5[-1]),
            "ma10": _round(ma10[-1]),
            "ma20": _round(ma20[-1]),
            "ma60": _round(ma60),
            "macd": {
                "macd": _round(macd["macd"][-1]),
                "signal": _round(macd["signal"][-1]),
                "histogram": _round(macd["histogram"][-1]),
            },
            "rsi14": _round(rsi14),
            "bollinger": {
                "mid": _round(bb["mid"][-1]),
                "upper": _round(bb["upper"][-1]),
                "lower": _round(bb["lower"][-1]),
            },
            "volume_ratio_5": _round(volume_ratio_5),
            "main_net_5d": round(flow.main_net_5d, 2),
            "main_net_3d": round(flow.main_net_3d, 2),
            "main_net_10d": round(flow.main_net_10d, 2),
            "main_net_20d": round(flow.main_net_20d, 2),
            "trend": trend,
        }
    except Exception as e:  # 纯函数降级:任何异常都不抛
        logger.debug("[technicals] partial: %s: %s", type(e).__name__, e)
        return {
            "__partial__": "computation_error",
            "bar_count": len(kline.bars) if kline is not None and kline.bars else 0,
        }


def _sma(arr: np.ndarray, window: int) -> np.ndarray:
    """简单移动均值，长度不足时返回 NaN 填充的有效卷积。"""
    if len(arr) < window:
        out = np.full_like(arr, np.nan, dtype=np.float64)
        return out
    cumsum = np.cumsum(arr, dtype=np.float64)
    cumsum[window:] = cumsum[window:] - cumsum[:-window]
    out = np.full_like(arr, np.nan, dtype=np.float64)
    out[window - 1 :] = cumsum[window - 1 :] / window
    return out


def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    n = len(arr)
    out = np.empty(n, dtype=np.float64)
    alpha = 2.0 / (span + 1)
    out[0] = arr[0]
    for i in range(1, n):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _macd(closes: np.ndarray) -> dict[str, np.ndarray]:
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    hist = (dif - dea) * 2
    return {"macd": dif, "signal": dea, "histogram": hist}


def _rsi(closes: np.ndarray, period: int) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    # Wilder's smoothing: seed with the simple mean of the first `period` bars,
    # then recursively average the rest.
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def _bollinger(closes: np.ndarray, window: int) -> dict[str, np.ndarray]:
    out_mid = np.full_like(closes, np.nan, dtype=np.float64)
    out_upper = np.full_like(closes, np.nan, dtype=np.float64)
    out_lower = np.full_like(closes, np.nan, dtype=np.float64)
    for i in range(window - 1, len(closes)):
        seg = closes[i - window + 1 : i + 1]
        mu = float(np.mean(seg))
        sigma = float(np.std(seg, ddof=0))
        out_mid[i] = mu
        out_upper[i] = mu + 2 * sigma
        out_lower[i] = mu - 2 * sigma
    return {"mid": out_mid, "upper": out_upper, "lower": out_lower}


def _volume_ratio(volumes: np.ndarray, window: int) -> float:
    """近 window 根量比：近 N 日均量 / 前 N 日均量。"""
    if len(volumes) < window * 2:
        return 1.0
    recent = float(np.mean(volumes[-window:]))
    prev = float(np.mean(volumes[-window * 2 : -window]))
    if prev <= 0:
        return 1.0
    return recent / prev


def _detect_trend(_closes: np.ndarray, ma5: np.ndarray, ma20: np.ndarray) -> str:
    """趋势判定:MA20 近 5 根斜率 + MA5 相对 MA20 位置。"""
    valid_ma20 = ma20[~np.isnan(ma20)]
    if len(valid_ma20) < 5:
        return "震荡"
    slope = valid_ma20[-1] - valid_ma20[-5]
    last_ma5 = ma5[~np.isnan(ma5)]
    if last_ma5.size == 0:
        return "震荡"
    ma5_above = last_ma5[-1] > valid_ma20[-1]

    if slope > 0 and ma5_above:
        return "多头"
    if slope < 0 and not ma5_above:
        return "空头"
    return "震荡"


def _round(value: float) -> float:
    if np.isnan(value) or np.isinf(value):
        return 0.0
    return round(float(value), 4)
