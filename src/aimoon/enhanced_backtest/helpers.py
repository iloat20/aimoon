"""Helper functions for the enhanced backtest engine.

Extracted from enhanced_backtest.py for modularity.
"""

from __future__ import annotations

import logging

import pandas as pd

from aimoon.backtest import risk_controls
from aimoon.indicators.technical import TechInd

logger = logging.getLogger(__name__)

# Re-export helpers from risk_controls for convenience
_get_atr_value = risk_controls.get_atr_value

# ── Risk-control constants (imported from backtest.risk_controls) ──
TRAILING_STOP_TIERS = risk_controls.TRAILING_STOP_TIERS
HARD_LOSS_CAP = risk_controls.HARD_LOSS_CAP
PROFIT_PROTECTION_PEAK_THRESHOLD = risk_controls.PROFIT_PROTECTION_PEAK_THRESHOLD
PROFIT_PROTECTION_FLOOR = risk_controls.PROFIT_PROTECTION_FLOOR
TIME_DECAY_IDLE_DAYS = risk_controls.TIME_DECAY_IDLE_DAYS
TIME_DECAY_LOSS_DAYS = risk_controls.TIME_DECAY_LOSS_DAYS
TIME_DECAY_TIGHTEN_RATIO = risk_controls.TIME_DECAY_TIGHTEN_RATIO
CHANDELIER_ATR_MULTIPLIER = risk_controls.CHANDELIER_ATR_MULTIPLIER
STOP_LOSS_ATR_MULTIPLIER = risk_controls.STOP_LOSS_ATR_MULTIPLIER
TAKE_PROFIT_ATR_MULTIPLIER = risk_controls.TAKE_PROFIT_ATR_MULTIPLIER
DD_THRESHOLDS = risk_controls.DD_THRESHOLDS
REGIME_TAKE_PROFIT = risk_controls.REGIME_TAKE_PROFIT


def regime_take_profit(regime: str, fallback: float = 0.15) -> float:
    """Return regime-adaptive take-profit threshold."""
    return REGIME_TAKE_PROFIT.get(regime, fallback)


def _compute_atr_threshold(
    kline: pd.DataFrame,
    atr_multiplier: float,
    fallback: float,
    compute_fn: str,
) -> float:
    """Compute ATR-based threshold (stop-loss or take-profit) at entry time.
    Uses ATR(14) percentage x multiplier, clamped to min/max limits.
    """
    try:
        if len(kline) < 20:
            return fallback
        ti = TechInd(kline)
        atr = ti.atr(14)
        close = float(kline["close"].iloc[-1]) if "close" in kline.columns else 1.0
        if close <= 0 or atr is None:
            return fallback
        atr_val = float(atr.iloc[-1]) if hasattr(atr, "iloc") else float(atr)
        atr_pct = atr_val / close * 100.0
        return getattr(risk_controls, compute_fn)(atr_pct, atr_multiplier)
    except (KeyError, ValueError, IndexError, AttributeError) as e:
        logger.debug("ATR threshold computation failed: %s", e)
        return fallback


def compute_atr_entry_stop_loss(
    kline: pd.DataFrame, atr_multiplier: float = 1.0, fallback: float = 0.04
) -> float:
    """Compute ATR-based stop-loss at entry time."""
    return _compute_atr_threshold(kline, atr_multiplier, fallback, "compute_atr_stop_loss")


def compute_atr_take_profit(
    kline: pd.DataFrame, atr_multiplier: float = 4.0, fallback: float = 0.15
) -> float:
    """Compute ATR-based take-profit at entry time."""
    return _compute_atr_threshold(kline, atr_multiplier, fallback, "compute_atr_take_profit")
def parallel_compute_factors(
    registry,
    panel: dict[str, pd.DataFrame],
    alpha_ids: list[str],
    out: dict[str, pd.DataFrame],
    max_workers: int = 8,
) -> None:
    """Compute factors in parallel and store results in *out* dict.

    For small factor lists (< 20) runs sequentially to avoid thread overhead.
    """
    if len(alpha_ids) < 20:
        for fid in alpha_ids:
            try:
                out[fid] = registry.compute(fid, panel)
            except (KeyError, ValueError, AttributeError):
                continue
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _compute_one(fid: str) -> tuple[str, pd.DataFrame] | None:
        try:
            return fid, registry.compute(fid, panel)
        except (ValueError, TypeError, KeyError, RuntimeError):
            return None

    # Use ThreadPoolExecutor (factor compute releases GIL via numpy/scipy)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_compute_one, fid): fid for fid in alpha_ids}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                out[result[0]] = result[1]


# ── Default scoring thresholds (used as defaults in the engine) ──
MIN_KLINE_LENGTH: int = 60
RECENT_RET_THRESHOLD: float = -0.05
ROC5_DROP_THRESHOLD: float = -0.05
ROC5_MODERATE_DROP: float = -0.03
ROC5_RISE_THRESHOLD: float = 0.05
TIME_DECAY_IDLE_PNL: float = 0.01
TIME_DECAY_IDLE_DAYS_THRESHOLD: int = 15


def precompute_tech_signals(
    klines: dict[str, pd.DataFrame],
    bar_date: pd.Timestamp,
    use_reversal: bool = False,
) -> dict[str, list]:
    """Precompute technical signals for all stocks at a specific date.

    This avoids repeated TechInd construction when scoring the same stocks
    multiple times (Phase 2 + Phase 4).

    Returns:
        dict mapping stock code -> list of Signal objects
    """
    from aimoon.scoring import collect_signals

    signals_cache: dict[str, list] = {}
    for code, df in klines.items():
        if bar_date not in df.index:
            continue
        idx = df.index.get_loc(bar_date)
        if idx < MIN_KLINE_LENGTH:
            continue
        window = df.iloc[:idx]  # Use data up to (not including) current bar
        try:
            ti = TechInd(window)
            signals = collect_signals(ti, code=code, use_reversal=use_reversal)
            if signals:
                signals_cache[code] = signals
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    return signals_cache
