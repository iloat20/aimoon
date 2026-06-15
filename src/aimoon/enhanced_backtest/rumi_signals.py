"""Rumi signal generation and KRange exit check.

Extracted from EnhancedBacktestEngine for modularity.
"""

from __future__ import annotations

import logging

import pandas as pd

from aimoon.enhanced_backtest.models import EnhancedPosition
from aimoon.rumi_strategy import (
    KRangeExit,
    RumiPosition,
    RumiSignal,
    check_krange_exit,
    compute_rumi_score,
)

logger = logging.getLogger(__name__)

_RUMI_LOOKBACK: int = 10
_RUMI_MIN_SCORE: float = 100.0
_RUMI_MOMENTUM_WEIGHT: float = 0.4
_RUMI_RELATIVE_STRENGTH_WEIGHT: float = 0.3
_RUMI_VOLATILITY_WEIGHT: float = 0.3

_KRANGE_EXIT_THRESHOLD: float = 0.3


def generate_rumi_signals(
    klines: dict[str, pd.DataFrame],
    names: dict[str, str],
    bar_date: pd.Timestamp,
) -> dict[str, RumiSignal]:
    """Generate Rumi signals for all stocks at bar_date."""
    rumi_signals: dict[str, RumiSignal] = {}
    for code, kline in klines.items():
        if bar_date not in kline.index:
            continue
        loc = kline.index.get_loc(bar_date)
        if loc < _RUMI_LOOKBACK:
            continue
        window = kline.iloc[: loc + 1]
        rumi_score, momentum_score, relative_strength, volatility = compute_rumi_score(
            window, lookback=_RUMI_LOOKBACK,
            momentum_weight=_RUMI_MOMENTUM_WEIGHT,
            relative_strength_weight=_RUMI_RELATIVE_STRENGTH_WEIGHT,
            volatility_weight=_RUMI_VOLATILITY_WEIGHT,
        )
        if rumi_score >= _RUMI_MIN_SCORE:
            signal_type = "buy"
        elif rumi_score <= 20:
            signal_type = "sell"
        else:
            signal_type = "hold"
        rumi_signals[code] = RumiSignal(
            code=code, name=names.get(code, code),
            rumi_score=rumi_score, momentum_score=momentum_score,
            relative_strength=relative_strength, volatility=volatility,
            signal_type=signal_type,
        )
    return rumi_signals


def check_rumi_exit(
    code: str,
    position: EnhancedPosition,
    klines: dict[str, pd.DataFrame],
    bar_date: pd.Timestamp,
    rumi_score: float,
    regime: str,
) -> KRangeExit | None:
    """Check Rumi/KRange exit signal for a position."""
    if code not in klines or bar_date not in klines[code].index:
        return None
    rumi_position = RumiPosition(
        code=code, name=position.name,
        entry_price=position.entry_price, entry_date=position.entry_date,
        current_price=float(klines[code].loc[bar_date, "close"]),
        highest_price=position.highest_price,
        lowest_price=position.entry_price * 0.92,
        rumi_score=rumi_score,
        atr_at_entry=position.atr_at_entry,
        krange_upper=0.0, krange_lower=0.0,
        trailing_stop=position.stop_loss,
        pnl=(float(klines[code].loc[bar_date, "close"]) - position.entry_price) / position.entry_price,
        hold_days=(pd.Timestamp(bar_date) - position.entry_date).days,
    )
    return check_krange_exit(
        position=rumi_position, kline=klines[code], current_date=bar_date,
        rumi_score=rumi_score, regime=regime, exit_threshold=_KRANGE_EXIT_THRESHOLD,
    )
