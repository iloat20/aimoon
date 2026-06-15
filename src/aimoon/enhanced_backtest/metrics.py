"""Backtest performance metrics computation.

Extracted from EnhancedBacktestEngine for modularity.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from aimoon.enhanced_backtest.models import EnhancedPortfolioResult

logger = logging.getLogger(__name__)


def empty_result() -> EnhancedPortfolioResult:
    """Return a zeroed result for empty backtest runs."""
    return EnhancedPortfolioResult(
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, (), (100.0,), (0.0,),
        profit_loss_ratio=0.0, max_consecutive_loss=0, information_ratio=0.0,
    )


def compute_metrics(
    trades: list[Any],
    equity: list[float],
    dd_curve: list[float],
    benchmark_equity: list[float] | None = None,
    ic_series: list[float] | None = None,
    ic_dates: list[str] | None = None,
) -> EnhancedPortfolioResult:
    """Compute portfolio performance metrics from a backtest run.

    Handles empty trade lists gracefully.

    Returns:
        EnhancedPortfolioResult with all computed metrics.
    """
    if not trades:
        return empty_result()

    total_ret = (equity[-1] / equity[0] - 1) * 100
    n_periods = len(equity) - 1
    total_days = n_periods * 1
    annual_ret = ((equity[-1] / equity[0]) ** (252 / max(total_days, 1)) - 1) * 100

    returns = [(equity[i] / equity[i - 1] - 1) for i in range(1, len(equity))]
    if returns:
        mean_ret = float(np.mean(returns)) * 252 / 1
        std_ret = float(np.std(returns)) * np.sqrt(252 / 1)
        sharpe = mean_ret / std_ret if std_ret > 0 else 0.0
        downside = [r for r in returns if r < 0]
        downside_std = float(np.std(downside)) * np.sqrt(252 / 1) if downside else 0.0
        sortino = mean_ret / downside_std if downside_std > 0 else 0.0
    else:
        sharpe = sortino = 0.0

    max_dd = float(max(dd_curve)) if dd_curve else 0.0

    win_rate = sum(1 for t in trades if t.return_pct > 0) / len(trades)
    wins = [t.return_pct for t in trades if t.return_pct > 0]
    losses = [t.return_pct for t in trades if t.return_pct <= 0]
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    gross_profit = float(sum(wins))
    gross_loss = float(abs(sum(losses)))
    profit_factor = (
        (gross_profit / gross_loss)
        if gross_loss > 0
        else (float("inf") if gross_profit > 0 else 0.0)
    )
    avg_hold = float(np.mean([t.hold_days for t in trades])) if trades else 0.0

    bench_ret = 0.0
    if benchmark_equity and len(benchmark_equity) > 1:
        bench_ret = (benchmark_equity[-1] / benchmark_equity[0] - 1) * 100

    calmar = annual_ret / max_dd if max_dd > 0 else 0.0

    avg_w = float(avg_win)
    avg_l = abs(float(avg_loss))
    profit_loss_ratio = round(avg_w / avg_l, 4) if avg_l > 1e-10 else 0.0

    max_consec = 0
    cur_consec = 0
    for t in trades:
        if t.return_pct < 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    information_ratio = 0.0
    if benchmark_equity and len(benchmark_equity) > 1 and len(returns) > 1:
        bench_returns = [
            (benchmark_equity[i] / benchmark_equity[i - 1] - 1)
            for i in range(1, len(benchmark_equity))
        ]
        n = min(len(returns), len(bench_returns))
        if n > 1:
            active = np.array(returns[-n:]) - np.array(bench_returns[-n:])
            active_std = float(np.std(active))
            information_ratio = round(
                float(np.mean(active) / (active_std + 1e-10) * np.sqrt(252 / 1)), 4
            )

    return EnhancedPortfolioResult(
        total_return=round(total_ret, 2),
        annual_return=round(annual_ret, 2),
        sharpe_ratio=round(sharpe, 2),
        sortino_ratio=round(sortino, 2),
        max_drawdown=round(max_dd * 100, 2),
        win_rate=round(win_rate, 4),
        trade_count=len(trades),
        avg_hold_days=round(avg_hold, 1),
        profit_factor=round(profit_factor, 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        benchmark_return=round(bench_ret, 2),
        excess_return=round(total_ret - bench_ret, 2),
        calmar_ratio=round(calmar, 2),
        trades=tuple(trades),
        equity_curve=tuple(equity),
        drawdown_curve=tuple(dd_curve),
        profit_loss_ratio=profit_loss_ratio,
        max_consecutive_loss=max_consec,
        information_ratio=information_ratio,
        ic_series=tuple(ic_series or []),
        ic_dates=tuple(ic_dates or []),
    )
