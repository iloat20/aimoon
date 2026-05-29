"""评分函数注册表"""
from __future__ import annotations
from typing import Callable, Union
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal
from aimoon.scoring.momentum import score_momentum
from aimoon.scoring.momentum_ext import score_momentum_ext
from aimoon.scoring.trend import score_trend
from aimoon.scoring.trend_ext import score_trend_ext
from aimoon.scoring.rsi import score_rsi
from aimoon.scoring.macd import score_macd
from aimoon.scoring.kdj import score_kdj
from aimoon.scoring.volume import score_volume
from aimoon.scoring.bollinger import score_bollinger
from aimoon.scoring.sector import score_sector

Scorer = Callable[..., Union[Signal, list[Signal], None]]

SCORERS: list[Scorer] = [
    score_momentum, score_momentum_ext,  # 动量为主
    score_trend, score_trend_ext,         # 趋势辅助
    score_rsi, score_macd, score_kdj,
    score_volume, score_bollinger, score_sector,
]


def collect_signals(ti: TechInd, code: str = "", ctx: dict | None = None) -> list[Signal]:
    """运行所有评分函数，收集非空信号。"""
    signals: list[Signal] = []
    for scorer in SCORERS:
        result = scorer(ti, code=code, ctx=ctx)
        if result is None:
            continue
        signals.extend(result if isinstance(result, list) else [result])
    return signals
