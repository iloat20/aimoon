"""Stock screener - strategy-based filtering"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from aimoon.config import CONFIG
from aimoon.indicators.technical import TechnicalIndicators

logger = logging.getLogger(__name__)


@dataclass
class SignalScore:
    stock_code: str
    stock_name: str
    price: float
    pct_change: float
    turnover: float
    pe: float = 0.0
    pb: float = 0.0
    total_market_cap_yi: float = 0.0
    float_market_cap_yi: float = 0.0
    trend_score: int = 0
    rsi_score: int = 0
    macd_score: int = 0
    kdj_score: int = 0
    volume_score: int = 0
    boll_score: int = 0
    total_score: int = 0
    signals: list[str] = field(default_factory=list)
    suggestion: str = "观望"
    confidence: str = "低"


class StockScreener:
    def __init__(self) -> None:
        self.results: list[SignalScore] = []

    def screen_stock(
        self, stock_code: str, stock_name: str,
        kline_df: pd.DataFrame, spot_row: pd.Series | None = None,
    ) -> SignalScore | None:
        if kline_df is None or len(kline_df) < CONFIG.ma_long:
            return None
        try:
            ti = TechnicalIndicators(kline_df)
        except Exception:
            return None
        price = float(kline_df["close"].iloc[-1])
        pct_change = float(kline_df["pct_change"].iloc[-1]) if "pct_change" in kline_df.columns else 0.0
        turnover = float(kline_df["turnover"].iloc[-1]) if "turnover" in kline_df.columns else 0.0
        pe = float(spot_row["pe"]) if spot_row is not None and "pe" in spot_row.index and pd.notna(spot_row["pe"]) else 0.0
        pb = float(spot_row["pb"]) if spot_row is not None and "pb" in spot_row.index and pd.notna(spot_row["pb"]) else 0.0
        total_cap = float(spot_row["total_market_cap"]) / 1e8 if spot_row is not None and "total_market_cap" in spot_row.index and pd.notna(spot_row["total_market_cap"]) else 0.0
        float_cap = float(spot_row["float_market_cap"]) / 1e8 if spot_row is not None and "float_market_cap" in spot_row.index and pd.notna(spot_row["float_market_cap"]) else 0.0
        score = SignalScore(
            stock_code=stock_code, stock_name=stock_name,
            price=price, pct_change=pct_change, turnover=turnover,
            pe=pe, pb=pb, total_market_cap_yi=total_cap, float_market_cap_yi=float_cap,
        )
        self._score_trend(ti, score)
        self._score_rsi(ti, score)
        self._score_macd(ti, score)
        self._score_kdj(ti, score)
        self._score_volume(ti, score)
        self._score_bollinger(ti, score)
        score.total_score = (
            score.trend_score + score.rsi_score + score.macd_score +
            score.kdj_score + score.volume_score + score.boll_score
        )
        score.suggestion, score.confidence = self._generate_suggestion(score)
        return score
    def _score_trend(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        trend = ti.ma_trend()
        if trend == "bullish":
            score.trend_score = 2
            score.signals.append("均线多头排列")
        elif trend == "bearish":
            score.trend_score = -2
            score.signals.append("均线空头排列")
        if ti.ma_golden_cross():
            score.trend_score += 2
            score.signals.append("MA金叉")
        if ti.ma_death_cross():
            score.trend_score -= 2
            score.signals.append("MA死叉")

    def _score_rsi(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        sig = ti.rsi_signal()
        if sig == "oversold":
            score.rsi_score = 2
            score.signals.append("RSI超卖")
        elif sig == "overbought":
            score.rsi_score = -2
            score.signals.append("RSI超买")

    def _score_macd(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        if ti.macd_golden_cross():
            score.macd_score = 2
            score.signals.append("MACD金叉")
        if ti.macd_death_cross():
            score.macd_score -= 2
            score.signals.append("MACD死叉")
        if ti.macd_above_zero():
            score.macd_score += 1
            score.signals.append("MACD零轴上方")
        else:
            score.macd_score -= 1

    def _score_kdj(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        if ti.kdj_golden_cross():
            score.kdj_score = 2
            score.signals.append("KDJ金叉")
        if ti.kdj_oversold():
            score.kdj_score += 1
            score.signals.append("KDJ超卖")
        if ti.kdj_overbought():
            score.kdj_score -= 1
            score.signals.append("KDJ超买")
    def _score_volume(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        vr = ti.volume_ratio()
        if vr > 2.0:
            score.volume_score = 2
            score.signals.append("放量(2x+)")
        elif vr > 1.5:
            score.volume_score = 1
            score.signals.append("温和放量")
        elif vr < 0.5:
            score.volume_score = -1
            score.signals.append("缩量")

    def _score_bollinger(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        pos = ti.bollinger_position()
        if pos == "below":
            score.boll_score = 1
            score.signals.append("触及布林下轨")
        elif pos == "above":
            score.boll_score = -1
            score.signals.append("触及布林上轨")

    def _generate_suggestion(self, score: SignalScore) -> tuple[str, str]:
        total = score.total_score
        if total >= 6:
            return "强烈买入", "高"
        elif total >= 4:
            return "买入", "中高"
        elif total >= 2:
            return "建议买入", "中"
        elif total >= 0:
            return "观望", "低"
        elif total >= -2:
            return "谨慎", "中"
        elif total >= -4:
            return "建议卖出", "中高"
        else:
            return "强烈卖出", "高"

    def get_top_picks(self, n: int | None = None) -> list[SignalScore]:
        n = n or CONFIG.top_n
        sorted_results = sorted(self.results, key=lambda x: x.total_score, reverse=True)
        return sorted_results[:n]