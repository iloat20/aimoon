"""Portfolio-level scoring: score_portfolio + hybrid_score.

职责边界:
- 本模块: 对组合（自选股/持仓池）进行评分和排序
- scoring/__init__.py: 收集技术指标信号（collect_signals）
- scoring/hybrid_scorer.py: 多源信号加权融合（compute_hybrid_score）
"""

from __future__ import annotations

import pandas as pd

from aimoon.indicators.technical import TechInd
from aimoon.models import ScoredStock, Signal
from aimoon.scoring.hybrid_scorer import HybridScoreConfig, compute_hybrid_score


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
        use_reversal: Whether to include reversal scorers.
        ml_scores: Optional ``{code: ml_score}`` from ML ensemble.
        regime: Market regime for adaptive weights.

    Returns:
        List of ScoredStock, sorted by total_score descending.
    """
    from aimoon.scoring import collect_signals
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
                total_score=total,
            )
        )

    results.sort(key=lambda s: s.total_score, reverse=True)
    return results
