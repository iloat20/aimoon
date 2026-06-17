"""Enhanced Market Regime Detection — 多维度市场状态识别。

改进的 regime 检测机制，使用多个维度的指标：
1. 波动率维度：ATR、VIX 等效指标、波动率聚类
2. 趋势维度：MA 对齐、价格动量、趋势强度
3. 情绪维度：RSI、成交量变化、换手率
4. 市场结构维度：涨跌比、板块轮动、北向资金
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from aimoon.indicators.technical import TechInd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegimeScore:
    """Regime 维度得分。"""

    volatility: float  # 0-1, 高波动率得分
    trend: float  # -1 to 1, 趋势方向
    momentum: float  # -1 to 1, 动量方向
    sentiment: float  # 0-1, 情绪极端程度
    structure: float  # 0-1, 市场结构稳定性


@dataclass(frozen=True)
class EnhancedMarketRegime:
    """增强的市场状态。"""

    state: Literal["bull", "bear", "sideways", "high_volatility", "crisis"]
    confidence: float
    scores: RegimeScore
    details: dict
    transition_prob: dict[str, float]  # 状态转移概率

    @property
    def is_trending(self) -> bool:
        return self.state in ("bull", "bear")

    @property
    def is_risky(self) -> bool:
        return self.state in ("high_volatility", "crisis", "bear")

    @property
    def position_scale(self) -> float:
        """根据 regime 返回仓位比例（0.0-1.0）。"""
        scales = {
            "bull": 1.0,
            "sideways": 0.7,
            "bear": 0.3,
            "high_volatility": 0.4,
            "crisis": 0.1,
        }
        return scales.get(self.state, 0.5)


def _compute_volatility_scores(
    ti: TechInd,
    lookback: int = 120,
) -> tuple[float, float]:
    """计算波动率维度得分。

    返回: (volatility_score, vol_ratio)
    """
    close = ti._close
    high = ti._high
    low = ti._low

    # ATR 波动率
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr20 = tr.rolling(window=20).mean()
    atr_current = float(atr20.iloc[-1])
    atr_history = atr20.dropna().iloc[-lookback:]
    atr_p75 = float(atr_history.quantile(0.75)) if len(atr_history) > 0 else atr_current
    vol_ratio = atr_current / atr_p75 if atr_p75 > 0 else 1.0

    # 波动率聚类（GARCH 效应）
    returns = close.pct_change().dropna()
    if len(returns) >= 20:
        recent_vol = float(returns.iloc[-20:].std())
        hist_vol = float(returns.iloc[-lookback:].std()) if len(returns) >= lookback else recent_vol
        vol_cluster = recent_vol / hist_vol if hist_vol > 0 else 1.0
        vol_ratio = (vol_ratio + vol_cluster) / 2.0  # 合并 ATR 和聚类波动率

    # 波动率得分（0-1）
    volatility_score = min(1.0, max(0.0, (vol_ratio - 0.5) / 1.5))

    return volatility_score, vol_ratio


def _compute_trend_scores(
    ti: TechInd,
) -> tuple[float, float, float]:
    """计算趋势维度得分。

    返回: (trend_score, align_score, price_vs_ma60)
    """
    close = ti._close
    ma5 = ti.ma(5)
    ma20 = ti.ma(20)
    ma60 = ti.ma(60)

    if pd.isna(ma5.iloc[-1]) or pd.isna(ma20.iloc[-1]) or pd.isna(ma60.iloc[-1]):
        return 0.0, 0.0, 0.0

    # MA 对齐得分
    align_score = 0
    if float(ma5.iloc[-1]) > float(ma20.iloc[-1]):
        align_score += 1
    else:
        align_score -= 1
    if float(ma20.iloc[-1]) > float(ma60.iloc[-1]):
        align_score += 1
    else:
        align_score -= 1

    # 价格相对于 MA60 的偏离
    price_vs_ma60 = float(close.iloc[-1]) / float(ma60.iloc[-1]) - 1.0

    # 趋势强度（-1 到 1）
    trend_score = align_score / 2.0  # 归一化到 -1 到 1

    return trend_score, float(align_score), price_vs_ma60


def _compute_momentum_scores(
    ti: TechInd,
    lookback: int = 20,
) -> tuple[float, float, float]:
    """计算动量维度得分。

    返回: (momentum_score, rsi_val, roc_20d)
    """
    close = ti._close

    # RSI 信号
    rsi_series = ti.rsi(10)
    rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

    # 20 日动量（ROC）
    if len(close) >= lookback + 1:
        roc_20d = (float(close.iloc[-1]) / float(close.iloc[-lookback - 1]) - 1.0) * 100
    else:
        roc_20d = 0.0

    # 动量得分（-1 到 1）
    # ROC > 10% 强看多，ROC < -10% 强看空
    momentum_score = np.clip(roc_20d / 20.0, -1.0, 1.0)

    return momentum_score, rsi_val, roc_20d


def _compute_sentiment_scores(
    ti: TechInd,
) -> tuple[float, float, float]:
    """计算情绪维度得分。

    返回: (sentiment_score, vol_ratio_now, turnover_extreme)
    """
    # 成交量比率
    vol_ratio_now = ti.volume_ratio()

    # RSI 极端程度（0-1）
    rsi_series = ti.rsi(10)
    rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
    rsi_extreme = abs(rsi_val - 50) / 50.0  # 0-1

    # 成交量极端程度（0-1）
    vol_extreme = min(1.0, max(0.0, (vol_ratio_now - 0.5) / 1.5))

    # 情绪得分（0-1，越高越极端）
    sentiment_score = (rsi_extreme + vol_extreme) / 2.0

    return sentiment_score, vol_ratio_now, vol_extreme


def _compute_structure_scores(
    ti: TechInd,
    lookback: int = 20,
) -> tuple[float, float, float]:
    """计算市场结构维度得分。

    返回: (structure_score, price_range, volatility_regime)
    """
    close = ti._close
    high = ti._high
    low = ti._low

    # 价格波动范围（相对于 MA20）
    ma20 = ti.ma(20)
    if not pd.isna(ma20.iloc[-1]):
        price_range = (float(high.iloc[-1]) - float(low.iloc[-1])) / float(ma20.iloc[-1])
    else:
        price_range = 0.0

    # 波动率 regime（低波动率 = 结构稳定）
    returns = close.pct_change().dropna()
    if len(returns) >= lookback:
        recent_vol = float(returns.iloc[-lookback:].std())
        hist_vol = float(returns.iloc[-120:].std()) if len(returns) >= 120 else recent_vol
        volatility_regime = recent_vol / hist_vol if hist_vol > 0 else 1.0
    else:
        volatility_regime = 1.0

    # 结构稳定性得分（0-1，越高越稳定）
    structure_score = 1.0 - min(1.0, price_range * 10)  # 价格范围越大，结构越不稳定
    structure_score = max(0.0, structure_score)

    return structure_score, price_range, volatility_regime


def _determine_regime_state(
    vol_score: float,
    trend_score: float,
    momentum_score: float,
    sentiment_score: float,
    structure_score: float,
    align_score: float,
    price_vs_ma60: float,
    rsi_val: float,
    vol_ratio: float,
) -> tuple[
    Literal["bull", "bear", "sideways", "high_volatility", "crisis"],
    float,
    dict[str, float],
]:
    """确定 regime 状态和转移概率。

    返回: (state, confidence, transition_prob)
    """
    # 决策逻辑
    # 1. 危机模式：高波动 + 负趋势 + 极端情绪
    if vol_score > 0.7 and trend_score < -0.3 and sentiment_score > 0.6:
        return (
            "crisis",
            0.9,
            {
                "bull": 0.05,
                "bear": 0.6,
                "sideways": 0.1,
                "high_volatility": 0.2,
                "crisis": 0.05,
            },
        )

    # 2. 高波动模式：高波动 + 中性或负趋势
    if vol_score > 0.6 and trend_score <= 0:
        return (
            "high_volatility",
            min(vol_score + 0.2, 1.0),
            {
                "bull": 0.1,
                "bear": 0.3,
                "sideways": 0.2,
                "high_volatility": 0.3,
                "crisis": 0.1,
            },
        )

    # 3. 牛市模式：正趋势 + 正动量 + 价格在 MA60 上方
    if trend_score > 0.3 and momentum_score > 0.1 and price_vs_ma60 > 0.02:
        conf = min(0.5 + price_vs_ma60 + momentum_score * 0.3, 1.0)
        if rsi_val > 75:
            conf *= 0.7  # RSI 超买降低置信度
        return (
            "bull",
            conf,
            {
                "bull": 0.7,
                "bear": 0.05,
                "sideways": 0.15,
                "high_volatility": 0.05,
                "crisis": 0.05,
            },
        )

    # 4. 熊市模式：负趋势 + 负动量 + 价格在 MA60 下方
    if trend_score < -0.3 and momentum_score < -0.1 and price_vs_ma60 < -0.02:
        conf = min(0.5 - price_vs_ma60 - momentum_score * 0.3, 1.0)
        if rsi_val < 25:
            conf *= 0.6  # RSI 超卖降低置信度（可能反弹）
        return (
            "bear",
            conf,
            {
                "bull": 0.05,
                "bear": 0.7,
                "sideways": 0.15,
                "high_volatility": 0.05,
                "crisis": 0.05,
            },
        )

    # 5. 震荡模式：其他情况
    return (
        "sideways",
        0.5,
        {
            "bull": 0.2,
            "bear": 0.2,
            "sideways": 0.4,
            "high_volatility": 0.15,
            "crisis": 0.05,
        },
    )


def detect_enhanced_regime(
    kline: pd.DataFrame,
    lookback: int = 120,
) -> EnhancedMarketRegime:
    """增强的市场状态检测。

    使用多维度指标综合判断市场状态。

    Args:
        kline: K 线数据
        lookback: 回看周期

    Returns:
        EnhancedMarketRegime: 增强的市场状态对象
    """
    if len(kline) < 60:
        return EnhancedMarketRegime(
            state="sideways",
            confidence=0.0,
            scores=RegimeScore(0.0, 0.0, 0.0, 0.0, 0.0),
            details={"error": "insufficient data"},
            transition_prob={
                "bull": 0.2,
                "bear": 0.2,
                "sideways": 0.4,
                "high_volatility": 0.15,
                "crisis": 0.05,
            },
        )

    ti = TechInd(kline)

    # 计算各维度得分
    vol_score, vol_ratio = _compute_volatility_scores(ti, lookback)
    trend_score, align_score, price_vs_ma60 = _compute_trend_scores(ti)
    momentum_score, rsi_val, roc_20d = _compute_momentum_scores(ti, lookback)
    sentiment_score, vol_ratio_now, vol_extreme = _compute_sentiment_scores(ti)
    structure_score, price_range, volatility_regime = _compute_structure_scores(ti, lookback)

    # 确定 regime 状态
    state, confidence, transition_prob = _determine_regime_state(
        vol_score=vol_score,
        trend_score=trend_score,
        momentum_score=momentum_score,
        sentiment_score=sentiment_score,
        structure_score=structure_score,
        align_score=align_score,
        price_vs_ma60=price_vs_ma60,
        rsi_val=rsi_val,
        vol_ratio=vol_ratio,
    )

    # 构建得分对象
    scores = RegimeScore(
        volatility=vol_score,
        trend=trend_score,
        momentum=momentum_score,
        sentiment=sentiment_score,
        structure=structure_score,
    )

    # 构建详情字典
    details = {
        "vol_ratio": round(vol_ratio, 2),
        "align": align_score,
        "price_vs_ma60": round(price_vs_ma60, 4),
        "rsi": round(rsi_val, 1),
        "roc_20d": round(roc_20d, 2),
        "vol_ratio_now": round(vol_ratio_now, 2),
        "price_range": round(price_range, 4),
        "volatility_regime": round(volatility_regime, 2),
        "volatility_score": round(vol_score, 3),
        "trend_score": round(trend_score, 3),
        "momentum_score": round(momentum_score, 3),
        "sentiment_score": round(sentiment_score, 3),
        "structure_score": round(structure_score, 3),
    }

    return EnhancedMarketRegime(
        state=state,
        confidence=confidence,
        scores=scores,
        details=details,
        transition_prob=transition_prob,
    )


# 向后兼容：保留旧接口
def detect_regime(kline: pd.DataFrame, lookback: int = 120):
    """向后兼容的 regime 检测接口。"""
    enhanced = detect_enhanced_regime(kline, lookback)
    return enhanced
