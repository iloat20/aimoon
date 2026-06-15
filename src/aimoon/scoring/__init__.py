"""Scoring registry with explicit technical + momentum split.

职责边界:
- scoring/__init__.py: 收集技术指标信号（collect_signals），信号层入口
- scoring/portfolio.py: 组合评分（score_portfolio）和融合评分（hybrid_score）
- scoring/hybrid_scorer.py: 多源信号加权融合（compute_hybrid_score）
- factors/scorer.py: Alpha Zoo 因子层面的截面转换，不涉及多信号融合
"""

from __future__ import annotations

from collections.abc import Callable

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal
from aimoon.scoring.bollinger import score_bollinger
from aimoon.scoring.fundamentals import score_fundamentals
from aimoon.scoring.kdj import score_kdj
from aimoon.scoring.macd import score_macd
from aimoon.scoring.mean_reversion import score_mean_reversion
from aimoon.scoring.momentum import score_momentum
from aimoon.scoring.momentum_ext import score_momentum_ext
from aimoon.scoring.portfolio import hybrid_score, score_portfolio
from aimoon.scoring.reversal import score_reversal
from aimoon.scoring.rsi import score_rsi
from aimoon.scoring.trend import score_trend
from aimoon.scoring.trend_ext import score_trend_ext
from aimoon.scoring.volume import score_volume

__all__ = [
    "collect_signals",
    "hybrid_score",
    "score_portfolio",
]

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
        group = scorer.__name__
        scorer_signals[group].extend(sigs)

    all_signals: list[Signal] = []
    for group_name, sigs in scorer_signals.items():
        if not sigs:
            continue
        avg_score = int(round(sum(s.score for s in sigs) / len(sigs)))
        labels = "; ".join(s.label for s in sigs[:3])
        if len(sigs) > 3:
            labels += f"... +{len(sigs)-3}more"
        cat = sigs[0].category
        all_signals.append(Signal(
            name=f"group_{group_name}",
            label=f"[{len(sigs)}sig] {labels}",
            score=avg_score,
            category=cat,
        ))
    return all_signals
