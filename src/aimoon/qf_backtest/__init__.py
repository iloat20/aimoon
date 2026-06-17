"""QF-Lib based backtesting module for aimoon.

This module provides an optional QF-Lib backtesting implementation
that wraps aimoon's ML ranking pipeline into QF-Lib's event-driven
backtesting architecture.

QF-Lib must be installed separately -- it is not a core dependency.

Install:  uv pip install qf-lib
"""

from __future__ import annotations

from aimoon.qf_backtest.runner import (
    is_qf_lib_available,
    precompute_ml_scores_by_date,
    run_qf_backtest,
)

__all__ = ["run_qf_backtest", "is_qf_lib_available", "precompute_ml_scores_by_date"]
