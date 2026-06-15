"""Enhanced backtest engine package.

Provides event-driven backtesting with stop-loss, take-profit,
position sizing, and risk management.

This package is a refactored version of the original single-file
enhanced_backtest.py module. It preserves full backward compatibility
by re-exporting all public symbols.
"""

from __future__ import annotations

from aimoon.enhanced_backtest.engine import EnhancedBacktestEngine
from aimoon.enhanced_backtest.entry_rules import (
    phase4_open_replacements,
)
from aimoon.enhanced_backtest.exit_rules import (
    phase0_execute_pending,
    phase1_stop_loss_take_profit,
    phase2_momentum_check,
)

# Re-export from submodules for backward compatibility
from aimoon.enhanced_backtest.helpers import (
    CHANDELIER_ATR_MULTIPLIER,
    DD_THRESHOLDS,
    HARD_LOSS_CAP,
    MIN_KLINE_LENGTH,
    PROFIT_PROTECTION_FLOOR,
    PROFIT_PROTECTION_PEAK_THRESHOLD,
    RECENT_RET_THRESHOLD,
    REGIME_TAKE_PROFIT,
    ROC5_DROP_THRESHOLD,
    ROC5_MODERATE_DROP,
    ROC5_RISE_THRESHOLD,
    STOP_LOSS_ATR_MULTIPLIER,
    TAKE_PROFIT_ATR_MULTIPLIER,
    TIME_DECAY_IDLE_DAYS,
    TIME_DECAY_IDLE_DAYS_THRESHOLD,
    TIME_DECAY_IDLE_PNL,
    TIME_DECAY_LOSS_DAYS,
    TIME_DECAY_TIGHTEN_RATIO,
    TRAILING_STOP_TIERS,
    compute_atr_entry_stop_loss,
    compute_atr_take_profit,
    parallel_compute_factors,
    regime_take_profit,
)
from aimoon.enhanced_backtest.metrics import (
    compute_metrics,
    empty_result,
)
from aimoon.enhanced_backtest.ml_integration import (
    compute_alpha_signals,
    get_alpha_signals_for_date,
    get_fallback_ml_scores,
    get_ml_scores_for_date,
    init_ml_model,
    score_stock,
)
from aimoon.enhanced_backtest.models import (
    EnhancedPortfolioResult,
    EnhancedPosition,
    EnhancedTrade,
    PhaseState,
)
from aimoon.enhanced_backtest.portfolio_runner import run_portfolio
from aimoon.enhanced_backtest.rumi_signals import (
    check_rumi_exit,
    generate_rumi_signals,
)

__all__ = [
    "EnhancedBacktestEngine",
    "EnhancedPosition",
    "EnhancedTrade",
    "EnhancedPortfolioResult",
    "PhaseState",
    "compute_atr_entry_stop_loss",
    "compute_atr_take_profit",
    "regime_take_profit",
    "parallel_compute_factors",
    "MIN_KLINE_LENGTH",
    "TRAILING_STOP_TIERS",
    "HARD_LOSS_CAP",
    "PROFIT_PROTECTION_PEAK_THRESHOLD",
    "PROFIT_PROTECTION_FLOOR",
    "CHANDELIER_ATR_MULTIPLIER",
    "REGIME_TAKE_PROFIT",
    "DD_THRESHOLDS",
    "STOP_LOSS_ATR_MULTIPLIER",
    "TAKE_PROFIT_ATR_MULTIPLIER",
    "RECENT_RET_THRESHOLD",
    "ROC5_DROP_THRESHOLD",
    "ROC5_MODERATE_DROP",
    "ROC5_RISE_THRESHOLD",
    "TIME_DECAY_IDLE_PNL",
    "TIME_DECAY_IDLE_DAYS_THRESHOLD",
    "TIME_DECAY_IDLE_DAYS",
    "TIME_DECAY_LOSS_DAYS",
    "TIME_DECAY_TIGHTEN_RATIO",
    "phase0_execute_pending",
    "phase1_stop_loss_take_profit",
    "phase2_momentum_check",
    "phase4_open_replacements",
    "compute_alpha_signals",
    "compute_metrics",
    "check_rumi_exit",
    "empty_result",
    "init_ml_model",
    "score_stock",
    "get_ml_scores_for_date",
    "get_fallback_ml_scores",
    "get_alpha_signals_for_date",
    "generate_rumi_signals",
    "run_portfolio",
]
