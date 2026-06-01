"""Market regime detection."""
from __future__ import annotations
import pandas as pd
from aimoon.indicators.technical import TechInd

class MarketRegime:
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    HIGH_VOL = "high_volatility"

    def __init__(self, state: str, confidence: float, details: dict) -> None:
        self.state = state
        self.confidence = confidence
        self.details = details

    def __repr__(self) -> str:
        return f"MarketRegime({self.state}, conf={self.confidence:.0%})"

    @property
    def is_trending(self) -> bool:
        return self.state in (self.BULL, self.BEAR)

    @property
    def is_risky(self) -> bool:
        return self.state in (self.HIGH_VOL, self.BEAR)


def detect_regime(kline: pd.DataFrame, lookback: int = 120) -> MarketRegime:
    if len(kline) < 60:
        return MarketRegime(MarketRegime.SIDEWAYS, 0.0, {"error": "insufficient data"})
    ti = TechInd(kline)
    close = ti._close
    high = ti._high
    low = ti._low
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr20 = tr.rolling(window=20).mean()
    atr_current = float(atr20.iloc[-1])
    atr_history = atr20.dropna().iloc[-lookback:]
    atr_p75 = float(atr_history.quantile(0.75)) if len(atr_history) > 0 else atr_current
    vol_ratio = atr_current / atr_p75 if atr_p75 > 0 else 1.0
    is_high_vol = vol_ratio > 1.2
    ma5 = ti.ma(5)
    ma20 = ti.ma(20)
    ma60 = ti.ma(60)
    if pd.isna(ma5.iloc[-1]) or pd.isna(ma20.iloc[-1]) or pd.isna(ma60.iloc[-1]):
        return MarketRegime(MarketRegime.SIDEWAYS, 0.3, {"atr_pct": vol_ratio})
    align_score = 0
    if float(ma5.iloc[-1]) > float(ma20.iloc[-1]):
        align_score += 1
    else:
        align_score -= 1
    if float(ma20.iloc[-1]) > float(ma60.iloc[-1]):
        align_score += 1
    else:
        align_score -= 1
    price_vs_ma60 = float(close.iloc[-1]) / float(ma60.iloc[-1]) - 1.0
    if is_high_vol and align_score <= 0:
        return MarketRegime(MarketRegime.HIGH_VOL, min(vol_ratio - 0.2, 1.0), {"atr_pct": vol_ratio, "align": align_score})
    elif align_score >= 1 and price_vs_ma60 > 0.02:
        return MarketRegime(MarketRegime.BULL, min(0.5 + price_vs_ma60, 1.0), {"atr_pct": vol_ratio, "align": align_score, "price_vs_ma60": price_vs_ma60})
    elif align_score <= -1 and price_vs_ma60 < -0.02:
        return MarketRegime(MarketRegime.BEAR, min(0.5 - price_vs_ma60, 1.0), {"atr_pct": vol_ratio, "align": align_score, "price_vs_ma60": price_vs_ma60})
    else:
        return MarketRegime(MarketRegime.SIDEWAYS, 0.5, {"atr_pct": vol_ratio, "align": align_score, "price_vs_ma60": price_vs_ma60})
