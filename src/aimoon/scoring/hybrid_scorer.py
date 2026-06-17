"""混合评分系统 — 三组信号独立评分后加权组合。

分组：
- reversal: 技术指标信号（趋势/RSI/MACD/KDJ/布林/成交量/RPS/反转），以均值直接映射
- momentum: 动量信号（ROC/动量加速/波动率调整/量价关系等），以均值直接映射
- alpha:   基本面/板块信号（PE/PB/板块轮动），以均值直接映射
- ml:      ML 模型信号（百分位 0-100），线性映射

设计原则：
1. 每组内部用 sum(scores) 而非 mean，让信号多的股票得分更高
2. 用 tanh 做软截断，保留区分度同时防止极端值
3. 最终加权组合，限制在 [0, 100]
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from aimoon.models import Signal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HybridScoreConfig:
    """混合评分配置"""

    # 各组权重（总和应为 1.0）
    ml_weight: float = 0.30
    alpha_weight: float = 0.25
    reversal_weight: float = 0.45
    momentum_weight: float = 0.00

    # 每组信号求和后的 tanh 缩放参数
    # tanh(sum / scale) * 50 + 50 → 映射到 [0, 100]
    reversal_scale: float = 8.0  # reversal 信号通常较多，用较大 scale
    alpha_scale: float = 4.0  # alpha 信号最少


# Regime-adaptive weight presets
_REGIME_WEIGHT_PRESETS: dict[str, dict[str, float]] = {
    "bull": {"ml": 0.25, "alpha": 0.20, "reversal": 0.55, "momentum": 0.0},
    "bear": {"ml": 0.35, "alpha": 0.25, "reversal": 0.40, "momentum": 0.0},
    "sideways": {"ml": 0.30, "alpha": 0.25, "reversal": 0.45, "momentum": 0.0},
    "high_volatility": {"ml": 0.35, "alpha": 0.20, "reversal": 0.45, "momentum": 0.0},
    "crisis": {"ml": 0.40, "alpha": 0.25, "reversal": 0.35, "momentum": 0.0},
}


def get_regime_config(regime: str | None = None) -> HybridScoreConfig:
    """Return a HybridScoreConfig with weights adapted to market regime."""
    if regime and regime in _REGIME_WEIGHT_PRESETS:
        w = _REGIME_WEIGHT_PRESETS[regime]
        return HybridScoreConfig(
            ml_weight=w["ml"],
            alpha_weight=w["alpha"],
            reversal_weight=w["reversal"],
            momentum_weight=w["momentum"],
        )
    return HybridScoreConfig()


def compute_hybrid_score(
    signals: list[Signal],
    config: HybridScoreConfig | None = None,
) -> tuple[int, dict[str, float]]:
    """计算混合评分。"""
    if config is None:
        config = HybridScoreConfig()

    ml_sigs, alpha_sigs, _mom_sigs, rev_sigs = _separate_signals(signals)

    ml_score = _compute_ml_score(ml_sigs)
    alpha_score = _compute_group_score(alpha_sigs, config.alpha_scale)
    rev_score = _compute_group_score(rev_sigs, config.reversal_scale)
    # momentum signals are collected but NOT scored (weight=0)
    mom_score = 50.0  # neutral

    weighted = (
        ml_score * config.ml_weight
        + alpha_score * config.alpha_weight
        + rev_score * config.reversal_weight
        + mom_score * config.momentum_weight
    )

    final = max(0, min(100, int(weighted)))

    details = {
        "ml_score": ml_score,
        "alpha_score": alpha_score,
        "reversal_score": rev_score,
        "momentum_score": mom_score,
        "momentum_weight": config.momentum_weight,
        "ml_weight": config.ml_weight,
        "alpha_weight": config.alpha_weight,
        "reversal_weight": config.reversal_weight,
        "weighted_score": weighted,
    }

    logger.debug(
        "Score: ML=%.1f(w=%.2f) A=%.1f(w=%.2f) R=%.1f(w=%.2f) M=%.1f(w=%.2f) -> %d",
        ml_score,
        config.ml_weight,
        alpha_score,
        config.alpha_weight,
        rev_score,
        config.reversal_weight,
        mom_score,
        config.momentum_weight,
        final,
    )

    return final, details


def _separate_signals(
    signals: list[Signal],
) -> tuple[list[Signal], list[Signal], list[Signal], list[Signal]]:
    """分离信号到四个组：ml, alpha, momentum, reversal。"""
    ml_sigs: list[Signal] = []
    alpha_sigs: list[Signal] = []
    mom_sigs: list[Signal] = []
    rev_sigs: list[Signal] = []

    for s in signals:
        cat = s.category
        if cat == "ml":
            ml_sigs.append(s)
        elif cat == "alpha":
            alpha_sigs.append(s)
        elif cat == "reversal":
            rev_sigs.append(s)
        else:
            mom_sigs.append(s)

    return ml_sigs, alpha_sigs, mom_sigs, rev_sigs


def _compute_ml_score(signals: list[Signal]) -> float:
    """ML 分数：score 是 alpha_score [-40,+40]，线性映射到 [0,100]。"""
    if not signals:
        return 50.0
    avg = sum(s.score for s in signals) / len(signals)
    return max(0.0, min(100.0, 50.0 + avg * (50.0 / 40.0)))


def _compute_group_score(signals: list[Signal], scale: float) -> float:
    """分组分数：sum(scores) 经 tanh 软截断映射到 [0,100]。

    tanh(sum/scale) 的值域是 (-1, 1)，映射到 (0, 100)。
    scale 控制区分度：scale 越小，区分度越高。
    """
    if not signals:
        return 50.0
    total = sum(s.score for s in signals)
    # tanh(x) 在 x=0 时为 0，x→±∞ 时趋近 ±1
    normalized = math.tanh(total / scale)
    return max(0.0, min(100.0, normalized * 50.0 + 50.0))


def get_suggestion(score: int) -> tuple[str, str]:
    """根据分数获取建议。"""
    if score >= 75:
        return "强烈买入", "高"
    elif score >= 60:
        return "买入", "中高"
    elif score >= 50:
        return "建议买入", "中"
    elif score >= 40:
        return "观望", "低"
    elif score >= 30:
        return "谨慎", "中"
    elif score >= 20:
        return "建议卖出", "中高"
    else:
        return "强烈卖出", "高"


def analyze_score_breakdown(
    signals: list[Signal],
    config: HybridScoreConfig | None = None,
) -> dict:
    """分析评分分解。"""
    if config is None:
        config = HybridScoreConfig()

    ml_sigs, alpha_sigs, mom_sigs, rev_sigs = _separate_signals(signals)

    ml_score = _compute_ml_score(ml_sigs)
    alpha_score = _compute_group_score(alpha_sigs, config.alpha_scale)
    rev_score = _compute_group_score(rev_sigs, config.reversal_scale)
    # momentum signals are collected but NOT scored (weight=0)
    mom_score = 50.0  # neutral

    weighted = (
        ml_score * config.ml_weight
        + alpha_score * config.alpha_weight
        + rev_score * config.reversal_weight
        + mom_score * config.momentum_weight
    )

    final = max(0, min(100, int(weighted)))
    suggestion, confidence = get_suggestion(final)

    return {
        "final_score": final,
        "suggestion": suggestion,
        "confidence": confidence,
        "breakdown": {
            "ml": {
                "score": ml_score,
                "weight": config.ml_weight,
                "weighted": ml_score * config.ml_weight,
                "signals": len(ml_sigs),
            },
            "alpha": {
                "score": alpha_score,
                "weight": config.alpha_weight,
                "weighted": alpha_score * config.alpha_weight,
                "signals": len(alpha_sigs),
            },
            "reversal": {
                "score": rev_score,
                "weight": config.reversal_weight,
                "weighted": rev_score * config.reversal_weight,
                "signals": len(rev_sigs),
            },
            "momentum": {
                "score": mom_score,
                "weight": config.momentum_weight,
                "weighted": mom_score * config.momentum_weight,
                "signals": len(mom_sigs),
            },
        },
        "total_signals": len(signals),
    }
