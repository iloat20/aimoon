"""技术面策略 - 基于均线、RSI、MACD、KDJ、成交量、布林带打分"""
from __future__ import annotations

import logging

import pandas as pd

from aimoon.config import CONFIG
from aimoon.indicators.technical import TechnicalIndicators
from aimoon.strategies.base import Strategy
from aimoon.strategies.screener import SignalScore

logger = logging.getLogger(__name__)


class TechnicalStrategy(Strategy):
    """技术面打分策略。"""

    @property
    def name(self) -> str:
        return "technical"

    def score(
        self,
        code: str,
        name: str,
        kline: pd.DataFrame,
        spot: pd.Series | None = None,
        market_context: dict | None = None,
    ) -> SignalScore | None:
        if kline is None or len(kline) < CONFIG.ma_long:
            return None
        try:
            ti = TechnicalIndicators(kline)
        except Exception:
            return None
        price = float(kline["close"].iloc[-1])
        pct_change = (
            float(kline["pct_change"].iloc[-1])
            if "pct_change" in kline.columns
            else 0.0
        )
        turnover = (
            float(kline["turnover"].iloc[-1])
            if "turnover" in kline.columns
            else 0.0
        )
        fields = self._extract_spot_fields(spot)
        result = SignalScore(
            stock_code=code, stock_name=name,
            price=price, pct_change=pct_change, turnover=turnover,
            pe=fields["pe"], pb=fields["pb"],
            total_market_cap_yi=fields["total_cap"],
            float_market_cap_yi=fields["float_cap"],
        )
        self._score_trend(ti, result)
        self._score_rsi(ti, result)
        self._score_macd(ti, result)
        self._score_kdj(ti, result)
        self._score_volume(ti, result)
        self._score_bollinger(ti, result)
        self._score_momentum(ti, result)
        self._score_sector_momentum(code, result, market_context)
        result.total_score = (
            result.trend_score + result.rsi_score + result.macd_score +
            result.kdj_score + result.volume_score + result.boll_score +
            result.momentum_score
        )
        result.suggestion, result.confidence = self._generate_suggestion(result)
        return result

    @staticmethod
    def _extract_spot_fields(spot_row: pd.Series | None) -> dict[str, float]:
        pe = 0.0
        if spot_row is not None and "pe" in spot_row.index and pd.notna(spot_row["pe"]):
            pe = float(spot_row["pe"])
        pb = 0.0
        if spot_row is not None and "pb" in spot_row.index and pd.notna(spot_row["pb"]):
            pb = float(spot_row["pb"])
        total_cap = 0.0
        if (
            spot_row is not None
            and "total_market_cap" in spot_row.index
            and pd.notna(spot_row["total_market_cap"])
        ):
            total_cap = float(spot_row["total_market_cap"]) / 1e8
        float_cap = 0.0
        if (
            spot_row is not None
            and "float_market_cap" in spot_row.index
            and pd.notna(spot_row["float_market_cap"])
        ):
            float_cap = float(spot_row["float_market_cap"]) / 1e8
        return {"pe": pe, "pb": pb, "total_cap": total_cap, "float_cap": float_cap}

    @staticmethod
    def _score_trend(ti: TechnicalIndicators, score: SignalScore) -> None:
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

    @staticmethod
    def _score_rsi(ti: TechnicalIndicators, score: SignalScore) -> None:
        rsi_val = ti.rsi()
        if pd.isna(rsi_val.iloc[-1]):
            return
        rsi_now = float(rsi_val.iloc[-1])
        # RSI > 50 表示多头动量
        if rsi_now > 60:
            score.rsi_score = 2
            score.signals.append("RSI强势(>60)")
        elif rsi_now > 50:
            score.rsi_score = 1
            score.signals.append("RSI偏多(>50)")
        elif rsi_now < 40:
            score.rsi_score = -2
            score.signals.append("RSI弱势(<40)")
        elif rsi_now < 50:
            score.rsi_score = -1
            score.signals.append("RSI偏空(<50)")

    @staticmethod
    def _score_macd(ti: TechnicalIndicators, score: SignalScore) -> None:
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

    @staticmethod
    def _score_kdj(ti: TechnicalIndicators, score: SignalScore) -> None:
        if ti.kdj_golden_cross():
            score.kdj_score = 1
            score.signals.append("KDJ金叉")
        if ti.kdj_overbought():
            score.kdj_score -= 1
            score.signals.append("KDJ超买")

    @staticmethod
    def _score_volume(ti: TechnicalIndicators, score: SignalScore) -> None:
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

    @staticmethod
    def _score_bollinger(ti: TechnicalIndicators, score: SignalScore) -> None:
        pos = ti.bollinger_position()
        if pos == "below":
            score.boll_score = 1
            score.signals.append("触及布林下轨")
        elif pos == "above":
            score.boll_score = -1
            score.signals.append("触及布林上轨")

    @staticmethod
    def _score_momentum(ti: TechnicalIndicators, score: SignalScore) -> None:
        # ROC 10日变化率
        roc10 = ti.roc_signal(10)
        if roc10 > 5:
            score.momentum_score += 2
            score.signals.append("ROC强势(+5%+)")
        elif roc10 > 2:
            score.momentum_score += 1
            score.signals.append("ROC上升(+2%+)")
        elif roc10 < -5:
            score.momentum_score -= 2
            score.signals.append("ROC弱势(-5%-)")
        elif roc10 < -2:
            score.momentum_score -= 1
            score.signals.append("ROC下降(-2%-)")

        # 动量加速度（短周期ROC - 长周期ROC）
        accel = ti.momentum_acceleration(5, 20)
        if accel > 3:
            score.momentum_score += 2
            score.signals.append("动量加速")
        elif accel > 0:
            score.momentum_score += 1
            score.signals.append("动量偏强")
        elif accel < -3:
            score.momentum_score -= 2
            score.signals.append("动量减速")
        elif accel < 0:
            score.momentum_score -= 1
            score.signals.append("动量偏弱")

        # 近期新高（5日/10日/20日）
        if ti.new_high(5):
            score.momentum_score += 2
            score.signals.append("5日新高")
        elif ti.new_high(10):
            score.momentum_score += 1
            score.signals.append("10日新高")
        elif ti.new_high(20):
            score.momentum_score += 1
            score.signals.append("20日新高")
        elif ti.new_low(20):
            score.momentum_score -= 2
            score.signals.append("20日新低")

        # ADX 趋势强度（动量需要趋势确认）
        adx_val = ti.adx(14)
        if adx_val > 25:
            score.momentum_score += 1
            score.signals.append("ADX强趋势")

    @staticmethod
    def _score_sector_momentum(
        code: str, score: SignalScore, ctx: dict | None,
    ) -> None:
        """板块动量 + 全市场涨幅排名。"""
        if ctx is None:
            return
        top_pct = ctx.get("top_pct", 5)

        # 板块动量
        sector_map = ctx.get("sector_map", {})
        sector_returns = ctx.get("sector_returns", {})
        top_sectors = ctx.get("top_sectors", set())
        sector = sector_map.get(code)
        if sector:
            ret = sector_returns.get(sector)
            if ret is not None:
                score.signals.append(f"板块:{sector}({ret:+.1f}%)")
            if sector in top_sectors:
                score.momentum_score += 3
                score.signals.append(f"强势板块(Top{top_pct}%)")

        # 全市场涨幅排名
        top_stocks = ctx.get("top_stocks", set())
        if code in top_stocks:
            score.momentum_score += 3
            score.signals.append(f"全市场涨幅Top{top_pct}%")

    @staticmethod
    def _generate_suggestion(score: SignalScore) -> tuple[str, str]:
        total = score.total_score
        if total >= 8:
            return "强烈买入", "高"
        elif total >= 5:
            return "买入", "中高"
        elif total >= 2:
            return "建议买入", "中"
        elif total >= 0:
            return "观望", "低"
        elif total >= -3:
            return "谨慎", "中"
        elif total >= -6:
            return "建议卖出", "中高"
        else:
            return "强烈卖出", "高"
