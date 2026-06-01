
"""Adaptive factor weighting based on market regime."""
from __future__ import annotations

import numpy as np
from aimoon.models import Signal, ScoredStock
from aimoon.regime import MarketRegime

# Signal name prefix -> scorer name mapping for accurate weight lookup.
# Must be defined before REGIME_WEIGHTS so the lookup helper can use it.
_SIGNAL_TO_SCORER: dict[str, str] = {
    # score_momentum
    "roc5_": "score_momentum", "roc10_": "score_momentum", "roc20_": "score_momentum",
    "accel_": "score_momentum", "decel_": "score_momentum",
    "high_": "score_momentum", "low_": "score_momentum", "adx_strong": "score_momentum",
    # score_momentum_ext
    "roc3_": "score_momentum_ext", "roc40_": "score_momentum_ext",
    "roc60_": "score_momentum_ext", "roc120_": "score_momentum_ext",
    "rps_ext_": "score_momentum_ext",
    "vol_adj_": "score_momentum_ext", "persist_": "score_momentum_ext",
    "skew_": "score_momentum_ext", "ud_vol_": "score_momentum_ext",
    "obv_": "score_momentum_ext", "vwap_": "score_momentum_ext",
    "recovery_": "score_momentum_ext", "hl_net_": "score_momentum_ext",
    "crash_filter": "score_momentum_ext",
    # score_trend
    "ma_golden": "score_trend", "ma_death": "score_trend",
    # score_trend_ext
    "ma_align_": "score_trend_ext", "above_ma20_60": "score_trend_ext",
    "above_ma20": "score_trend_ext", "below_ma20_60": "score_trend_ext",
    "adx_bull_": "score_trend_ext", "adx_bear_": "score_trend_ext",
    "macd_red_": "score_trend_ext", "macd_green_": "score_trend_ext",
    "ema_slope_": "score_trend_ext",
    # score_rps
    "rps_": "score_rps",
    # score_rsi
    "rsi_": "score_rsi",
    # score_macd
    "macd_": "score_macd",
    # score_kdj
    "kdj_": "score_kdj",
    # score_volume
    "volume_": "score_volume",
    # score_bollinger
    "boll_": "score_bollinger",
    # score_sector
    "sector_": "score_sector",
    # score_fundamentals
    "pe_": "score_fundamentals", "pb_": "score_fundamentals",
    # score_alpha (Alpha Zoo 截面因子)
    "alpha_": "score_alpha",
}

REGIME_WEIGHTS = {
    MarketRegime.BULL: {
        "score_momentum": 1.5, "score_momentum_ext": 1.5,
        "score_trend": 1.3, "score_trend_ext": 1.3,
        "score_rps": 1.4, "score_rsi": 0.8,
        "score_macd": 1.2, "score_kdj": 0.7,
        "score_volume": 1.0, "score_bollinger": 0.6,
        "score_sector": 1.2,
        "score_fundamentals": 1.0,
        "score_alpha": 1.2,
    },
    MarketRegime.BEAR: {
        "score_momentum": 0.6, "score_momentum_ext": 0.6,
        "score_trend": 0.7, "score_trend_ext": 0.7,
        "score_rps": 0.5, "score_rsi": 1.3,
        "score_macd": 1.0, "score_kdj": 1.4,
        "score_volume": 1.2, "score_bollinger": 1.3,
        "score_sector": 0.8,
        "score_fundamentals": 1.0,
        "score_alpha": 1.3,
    },
    MarketRegime.SIDEWAYS: {
        "score_momentum": 0.7, "score_momentum_ext": 0.7,
        "score_trend": 0.5, "score_trend_ext": 0.5,
        "score_rps": 0.6, "score_rsi": 1.4,
        "score_macd": 0.8, "score_kdj": 1.5,
        "score_volume": 1.3, "score_bollinger": 1.5,
        "score_sector": 1.0,
        "score_fundamentals": 1.0,
        "score_alpha": 0.8,
    },
    MarketRegime.HIGH_VOL: {
        "score_momentum": 0.5, "score_momentum_ext": 0.5,
        "score_trend": 0.4, "score_trend_ext": 0.4,
        "score_rps": 0.4, "score_rsi": 1.2,
        "score_macd": 0.6, "score_kdj": 1.3,
        "score_volume": 1.5, "score_bollinger": 1.4,
        "score_sector": 0.7,
        "score_fundamentals": 1.0,
        "score_alpha": 1.0,
    },
}

def get_regime_weights(regime):
    return REGIME_WEIGHTS.get(regime.state, {})

def _match_scorer_weight(signal_name, weights):
    # Try exact match first
    if signal_name in weights:
        return weights[signal_name]
    # Look up scorer via prefix mapping
    for prefix, scorer in _SIGNAL_TO_SCORER.items():
        if signal_name.startswith(prefix):
            return weights.get(scorer, 1.0)
    return 1.0

def apply_regime_weights(scored, regime):
    weights = get_regime_weights(regime)
    if not weights:
        return scored
    adjusted = []
    for sig in scored.signals:
        mult = _match_scorer_weight(sig.name, weights)
        if mult != 1.0:
            adjusted.append(Signal(sig.name, sig.label, int(round(sig.score * mult))))
        else:
            adjusted.append(sig)
    return ScoredStock(
        code=scored.code, name=scored.name, price=scored.price,
        pct_change=scored.pct_change, turnover=scored.turnover,
        pe=scored.pe, pb=scored.pb, market_cap_yi=scored.market_cap_yi,
        signals=tuple(adjusted), rps=scored.rps,
    )

def apply_regime_to_list(results, regime):
    return [apply_regime_weights(s, regime) for s in results]
