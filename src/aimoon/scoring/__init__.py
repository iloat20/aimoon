"""评分函数注册表"""
from __future__ import annotations
from typing import Callable, Union
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal
from aimoon.scoring.momentum import score_momentum
from aimoon.scoring.momentum_ext import score_momentum_ext
from aimoon.scoring.trend import score_trend
from aimoon.scoring.trend_ext import score_trend_ext
from aimoon.scoring.macd import score_macd
from aimoon.scoring.kdj import score_kdj
from aimoon.scoring.volume import score_volume
from aimoon.scoring.bollinger import score_bollinger
from aimoon.scoring.sector import score_sector
from aimoon.scoring.fundamentals import score_fundamentals
from aimoon.scoring.reversal import score_reversal

Scorer = Callable[..., Union[Signal, list[Signal], None]]

SCORERS: list[Scorer] = [
    score_momentum, score_momentum_ext,  # 动量（降权）
    score_trend, score_trend_ext,         # 趋势（降权）
    score_macd, score_kdj,
    score_volume, score_bollinger, score_sector,
    score_fundamentals,                   # 基本面估值
]

# Reversal scorer: opt-in via --reversal flag (strong in mean-reversion regimes)
REVERSAL_SCORER: Scorer = score_reversal


def collect_signals(ti: TechInd, code: str = "", ctx: dict | None = None,
                    use_reversal: bool = False) -> list[Signal]:
    """运行所有评分函数，收集非空信号。"""
    signals: list[Signal] = []
    scorers = SCORERS + ([REVERSAL_SCORER] if use_reversal else [])
    for scorer in scorers:
        result = scorer(ti, code=code, ctx=ctx)
        if result is None:
            continue
        signals.extend(result if isinstance(result, list) else [result])
    return signals


# ── Category-level score capping ──
# Prevents any single signal category from dominating the total score.
# Factor evaluation shows A-shares are strongly mean-reverting: most signals
# are contrarian predictors. Uncapped momentum scores (up to +40) overwhelm
# the few contrarian signals (+2), so the system systematically picks
# overbought stocks that subsequently underperform.

# ── 100 分制加权评分 ──
# Alpha Zoo: 60 分 | 动量: 20 分 | 趋势/量价: 20 分

_CATEGORY_CAPS: dict[str, int] = {
    "alpha": 30,
    "momentum": 6, "rps": 4,
    "trend": 5, "macd": 3, "kdj": 2, "volume": 4,
    "valuation": 3, "sector": 2, "reversal": 4,
}

_CATEGORY_GROUP: dict[str, str] = {
    "alpha": "alpha",
    "momentum": "momentum", "rps": "momentum",
    "trend": "trend_vol", "macd": "trend_vol", "kdj": "trend_vol",
    "volume": "trend_vol", "valuation": "trend_vol",
    "sector": "trend_vol", "reversal": "trend_vol", "other": "trend_vol",
}

_GROUP_WEIGHTS: dict[str, int] = {
    "alpha": 60,
    "momentum": 20,
    "trend_vol": 20,
}

_SIGNAL_CATEGORY_PREFIXES: list[tuple[str, str]] = [
    ("momentum_exhaustion", "momentum"), ("momentum_overextended", "momentum"),
    ("crash_filter", "momentum"), ("rps_ext_", "momentum"),
    ("vol_adj_", "momentum"), ("persist_", "momentum"), ("skew_", "momentum"),
    ("hl_net_", "momentum"), ("recovery_", "momentum"),
    ("roc", "momentum"), ("accel_", "momentum"), ("decel_", "momentum"),
    ("high_", "momentum"), ("low_", "momentum"), ("adx_strong", "momentum"),
    ("ma_golden", "trend"), ("ma_death", "trend"), ("trend_", "trend"),
    ("ma_align_", "trend"), ("above_ma", "trend"), ("below_ma", "trend"),
    ("adx_bull_", "trend"), ("adx_bear_", "trend"),
    ("macd_red_", "trend"), ("macd_green_", "trend"), ("ema_slope_", "trend"),
    ("rsi_", "rsi"), ("macd_", "macd"), ("kdj_", "kdj"),
    ("volume_", "volume"), ("obv_", "volume"), ("ud_vol_", "volume"),
    ("vwap_", "volume"),
    ("pe_", "valuation"), ("pb_", "valuation"),
    ("sector_", "sector"),
    ("rps_", "rps"),
    ("reversal_", "reversal"),
    ("alpha_", "alpha"),  # Alpha Zoo 截面因子
]


def _classify_signal(name: str) -> str:
    for prefix, cat in _SIGNAL_CATEGORY_PREFIXES:
        if name.startswith(prefix):
            return cat
    return "other"


def category_capped_score(signals: list[Signal]) -> int:
    """100 分制加权评分。Alpha Zoo 60 分 / 动量 20 分 / 趋势量价 20 分。"""
    cat_totals: dict[str, int] = {}
    for s in signals:
        cat = _classify_signal(s.name)
        cat_totals[cat] = cat_totals.get(cat, 0) + s.score

    # 按类别 cap 截断，再按组归一化到目标权重
    group_raw: dict[str, float] = {}
    group_caps: dict[str, float] = {}
    for cat, raw in cat_totals.items():
        group = _CATEGORY_GROUP.get(cat, "trend_vol")
        cap = _CATEGORY_CAPS.get(cat, 6)
        clamped = max(0, min(cap, raw))  # 负分归零
        group_raw[group] = group_raw.get(group, 0) + clamped
        group_caps[group] = group_caps.get(group, 0) + cap

    total = 0
    for group, weight in _GROUP_WEIGHTS.items():
        raw = group_raw.get(group, 0)
        cap = group_caps.get(group, 1)
        total += int(raw / cap * weight) if cap > 0 else 0
    return total
