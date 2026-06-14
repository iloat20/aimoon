"""Scoring registry with explicit technical + momentum split.

职责边界:
- scoring/__init__.py: 收集技术指标信号（collect_signals），信号层入口
- scoring/hybrid_scorer.py: 多源信号加权融合（hybrid_score）
- factors/scorer.py: Alpha Zoo 因子层面的截面转换，不涉及多信号融合
"""

from __future__ import annotations

import pandas as pd

from collections.abc import Callable

from aimoon.indicators.technical import TechInd
from aimoon.models import ScoredStock, Signal
from aimoon.scoring.bollinger import score_bollinger
from aimoon.scoring.fundamentals import score_fundamentals
from aimoon.scoring.hybrid_scorer import (
    HybridScoreConfig,
    analyze_score_breakdown,
    compute_hybrid_score,
)
from aimoon.scoring.kdj import score_kdj
from aimoon.scoring.macd import score_macd
from aimoon.scoring.momentum import score_momentum
from aimoon.scoring.momentum_ext import score_momentum_ext
from aimoon.scoring.reversal import score_reversal
from aimoon.scoring.mean_reversion import score_mean_reversion
from aimoon.scoring.rsi import score_rsi
from aimoon.scoring.trend import score_trend
from aimoon.scoring.trend_ext import score_trend_ext
from aimoon.scoring.volume import score_volume

Scorer = Callable[..., Signal | list[Signal] | None]

ALL_SCORERS: list[Scorer] = [
    score_reversal,
    score_mean_reversion,
    score_momentum,
    score_momentum_ext,
    score_trend,
    score_trend_ext,
    score_volume,
    score_rsi,
    score_macd,
    score_kdj,
    score_bollinger,
    score_fundamentals,
]


def collect_signals(
    ti: TechInd, code: str = "", use_reversal: bool = True
) -> list[Signal]:
    """Run scoring functions and collect non-empty signals.
    
    Returns signals grouped by scorer, each scorer's signals are averaged
    to produce one composite signal per scorer. This prevents scorers
    that generate many signals from dominating the final score.
    """
    from collections import defaultdict
    scorer_signals: dict[str, list[Signal]] = defaultdict(list)
    for scorer in ALL_SCORERS:
        result = scorer(ti, code=code)
        if result is None:
            continue
        sigs = result if isinstance(result, list) else [result]
        # Use scorer function name as group key
        group = scorer.__name__
        scorer_signals[group].extend(sigs)
    
    # Average signals per scorer group, then flatten
    all_signals: list[Signal] = []
    for group_name, sigs in scorer_signals.items():
        if not sigs:
            continue
        avg_score = int(round(sum(s.score for s in sigs) / len(sigs)))
        # Use the group name as signal name, combine labels
        labels = "; ".join(s.label for s in sigs[:3])
        if len(sigs) > 3:
            labels += f"... +{len(sigs)-3}more"
        # All signals keep their original category
        # Just use the first signal's category as representative
        cat = sigs[0].category
        all_signals.append(Signal(
            name=f"group_{group_name}",
            label=f"[{len(sigs)}sig] {labels}",
            score=avg_score,
            category=cat,
        ))
    return all_signals


def hybrid_score(signals: list[Signal], config: HybridScoreConfig | None = None) -> int:
    """使用混合方法计算评分（唯一评分入口）

    四组独立评分后线性加权：
    - ML 分数：直接使用百分位（0-100），最准确
    - Alpha 因子：基本面/板块信号，tanh 缩放
    - Reversal 信号：技术指标（趋势/RSI/MACD/KDJ/布林/成交量），tanh 缩放
    - Momentum 信号：动量指标（ROC/动量加速/量价关系），tanh 缩放

    Args:
        signals: 信号列表（每个信号需有 category 字段）
        config: 评分配置（可选，支持 regime 自适应权重）

    Returns:
        int: 分数 (0-100)
    """
    score, _ = compute_hybrid_score(signals, config)
    return score


def hybrid_score_with_details(
    signals: list[Signal],
    config: HybridScoreConfig | None = None,
) -> tuple[int, dict[str, float]]:
    """使用混合方法计算评分，并返回详细信息。

    返回的详细信息包含四组分数：ml, alpha, reversal, momentum。

    Args:
        signals: 信号列表
        config: 评分配置（可选）

    Returns:
        tuple: (分数, 详细信息)
    """
    return compute_hybrid_score(signals, config)


def score_portfolio(
    codes: list[str],
    klines: dict[str, pd.DataFrame],
    use_reversal: bool = False,
    ml_scores: dict[str, int] | None = None,
    regime: str | None = None,
) -> list[ScoredStock]:
    """Score an arbitrary portfolio (watchlist / holdings pool).

    Uses the same scoring pipeline as ``screen_universe`` but operates on
    a pre-defined set of stock codes rather than a full market universe.
    Each stock is scored with technical signals + optional ML signals,
    then ranked by hybrid_score descending.

    Args:
        codes: Stock codes to score.
        klines: ``{code: DataFrame}`` with OHLCV data for each stock.
        ctx: Market context (sector_map, top_sectors, etc.).
        use_reversal: Whether to include reversal scorers.
        ml_scores: Optional ``{code: ml_score}`` from ML ensemble.
        regime: Market regime for adaptive weights.

    Returns:
        List of ScoredStock, sorted by total_score descending.
    """
    from aimoon.indicators.technical import TechInd
    from aimoon.scoring.hybrid_scorer import get_regime_config

    config = get_regime_config(regime) if regime else None
    results: list[ScoredStock] = []

    for code in codes:
        kdf = klines.get(code)
        if kdf is None or len(kdf) < 60:
            continue

        ti = TechInd(kdf)
        signals = collect_signals(ti, code=code, use_reversal=use_reversal)

        if ml_scores and code in ml_scores:
            from aimoon.scoring._ml_signal import create_ml_signal

            ml_signal = create_ml_signal(ml_scores[code])
            if ml_signal is not None:
                signals.append(ml_signal)

        if not signals:
            continue

        total = hybrid_score(signals, config)
        price = float(kdf["close"].iloc[-1])
        pct = float(kdf["pct_change"].iloc[-1]) if "pct_change" in kdf.columns else 0.0

        results.append(
            ScoredStock(
                code=code,
                name=code,
                price=price,
                pct_change=pct,
                signals=tuple(signals),
                ml_score=ml_scores.get(code) if ml_scores else None,
                hybrid_score=total,
            )
        )

    results.sort(key=lambda s: s.total_score, reverse=True)
    return results


def get_score_analysis(
    signals: list[Signal], config: HybridScoreConfig | None = None
) -> dict:
    """获取详细的评分分析

    Args:
        signals: 信号列表
        config: 评分配置（可选）

    Returns:
        dict: 详细的评分分析
    """
    return analyze_score_breakdown(signals, config)
