"""Signal name prefix → scorer/category mapping.

Centralized definition to prevent drift between copies in combiner.py,
adaptive_weight.py, __init__.py, and hybrid_scorer.py.

Two lookup functions:
- ``lookup_scorer(signal_name)`` → scorer name (e.g. "score_momentum")
- ``lookup_category(signal_name)`` → scoring category (e.g. "ml", "alpha", "momentum")
"""

from __future__ import annotations

# ─── Scorer mapping (prefix → scorer name) ───
# Longest prefix first for correct matching (e.g. "roc10_" before "roc1_").
_SIGNAL_TO_SCORER: dict[str, str] = {
    # ── score_momentum ──
    "adx_strong": "score_momentum",
    "roc20_": "score_momentum",
    "roc10_": "score_momentum",
    "roc5_": "score_momentum",
    "decel_": "score_momentum",
    "accel_": "score_momentum",
    "high_": "score_momentum",
    "low_": "score_momentum",
    # ── score_momentum_ext ──
    "roc120_": "score_momentum_ext",
    "roc60_": "score_momentum_ext",
    "roc40_": "score_momentum_ext",
    "roc3_": "score_momentum_ext",
    "rps_ext_": "score_momentum_ext",
    "vol_adj_": "score_momentum_ext",
    "recovery_": "score_momentum_ext",
    "persist_": "score_momentum_ext",
    "hl_net_": "score_momentum_ext",
    "ud_vol_": "score_momentum_ext",
    "vwap_": "score_momentum_ext",
    "crash_filter": "score_momentum_ext",
    "obv_": "score_momentum_ext",
    "skew_": "score_momentum_ext",
    # ── score_trend ──
    "ma_golden": "score_trend",
    "ma_death": "score_trend",
    # ── score_trend_ext ──
    "above_ma20_60": "score_trend_ext",
    "below_ma20_60": "score_trend_ext",
    "above_ma20": "score_trend_ext",
    "adx_bull_": "score_trend_ext",
    "adx_bear_": "score_trend_ext",
    "macd_red_": "score_trend_ext",
    "macd_green_": "score_trend_ext",
    "ema_slope_": "score_trend_ext",
    "ma_align_": "score_trend_ext",
    # ── score_rps ──
    "rps_": "score_rps",
    # ── score_rsi ──
    "rsi_": "score_rsi",
    # ── score_macd ──
    "macd_": "score_macd",
    # ── score_kdj ──
    "kdj_": "score_kdj",
    # ── score_volume ──
    "volume_": "score_volume",
    # ── score_bollinger ──
    "boll_": "score_bollinger",
    # ── score_sector ──
    "sector_": "score_sector",
    # ── score_reversal ──
    "reversal_": "score_reversal",
    # ── score_fundamentals ──
    "pe_": "score_fundamentals",
    "pb_": "score_fundamentals",
    # ── score_alpha (Alpha Zoo / ML) ──
    "ml_alpha_": "score_alpha",
    "alpha_": "score_alpha",
}

# Pre-sorted prefix list (longest first) — avoids re-sorting on every call.
_SORTED_SCORER_PREFIXES = sorted(_SIGNAL_TO_SCORER, key=len, reverse=True)

# ─── Category mapping (scorer name → scoring category) ───
# Categories: "ml", "alpha", "momentum" — used by hybrid_scorer and __init__.py.
# Signal.category takes precedence; this table is the fallback for callers
# that only have a signal name string (e.g. combiner._find_weight).
_SCORER_TO_CATEGORY: dict[str, str] = {
    "score_trend": "reversal",
    "score_trend_ext": "reversal",
    "score_rps": "reversal",
    "score_rsi": "reversal",
    "score_macd": "reversal",
    "score_kdj": "reversal",
    "score_volume": "reversal",
    "score_bollinger": "reversal",
    "score_sector": "alpha",  # 板块轮动归类为 alpha
    "score_fundamentals": "alpha",  # 基本面归类为 alpha
    "score_reversal": "reversal",  # 反转信号独立分组
    "score_alpha": "alpha",
}

# Exact signal name → category (for signals that don't match any scorer prefix).
_EXACT_CATEGORY: dict[str, str] = {
    "ml_rank": "ml",
    "ic_weight_adj": "alpha",
    "ml_alpha": "alpha",
}


def lookup_scorer(signal_name: str) -> str | None:
    """Return the scorer name for *signal_name*, or ``None`` if unknown.

    Matches by longest-prefix-first so that ``roc10_`` is matched before
    ``roc1_``.
    """
    for prefix in _SORTED_SCORER_PREFIXES:
        if signal_name.startswith(prefix):
            return _SIGNAL_TO_SCORER[prefix]
    return None


def lookup_category(signal_name: str) -> str:
    """Return the scoring category ("ml", "alpha", "momentum") for *signal_name*.

    Checks exact matches first, then falls back to scorer-prefix lookup.
    Unknown signals default to "momentum".
    """
    # 1. Exact match (fastest path)
    if signal_name in _EXACT_CATEGORY:
        return _EXACT_CATEGORY[signal_name]

    # 2. Scorer-prefix lookup
    scorer = lookup_scorer(signal_name)
    if scorer is not None:
        return _SCORER_TO_CATEGORY.get(scorer, "momentum")

    # 3. Default
    return "momentum"
