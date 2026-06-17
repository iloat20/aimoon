"""Performance metrics computation for QF-Lib backtest results."""

from __future__ import annotations

import math

import numpy as np

from aimoon.qf_backtest.models import QFBacktestResult, QFTradeRecord


def compute_metrics(
    trades: list[QFTradeRecord],
    equity_curve: list[float],
    initial_cash: float = 1_000_000.0,
    benchmark_curve: list[float] | None = None,
) -> QFBacktestResult:
    """Compute standard performance metrics from trade and equity data."""
    result = QFBacktestResult()
    result.equity_curve = equity_curve

    if not equity_curve:
        return result

    # Returns series
    equity = np.array(equity_curve, dtype=float)
    returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else np.array([0.0])

    # Total return
    result.total_return_pct = float((equity[-1] - initial_cash) / initial_cash * 100)

    # Annual return (assume ~252 trading days)
    n_days = len(equity)
    years = n_days / 252.0
    if years > 0:
        result.annual_return_pct = ((1 + result.total_return_pct / 100) ** (1 / years) - 1) * 100

    # Volatility
    if len(returns) > 1:
        ann_vol = float(np.std(returns, ddof=1) * math.sqrt(252)) * 100
        result.sharpe_ratio = (result.annual_return_pct - 2.0) / ann_vol if ann_vol > 1e-10 else 0.0

        # Sortino (downside deviation)
        downside = returns[returns < 0]
        if len(downside) > 0:
            down_dev = float(np.std(downside, ddof=1) * math.sqrt(252)) * 100
            result.sortino_ratio = (
                (result.annual_return_pct - 2.0) / down_dev if down_dev > 1e-10 else 0.0
            )
        else:
            result.sortino_ratio = result.sharpe_ratio

    # Max drawdown
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    result.max_drawdown_pct = max_dd * 100

    # Drawdown-based: Calmar ratio
    if max_dd > 1e-10:
        result.calmar_ratio = result.annual_return_pct / (max_dd * 100)
    else:
        result.calmar_ratio = result.sharpe_ratio if result.sharpe_ratio > 0 else 0.0

    # Trade statistics
    result.trade_count = len(trades)
    if trades:
        win_returns = [t.return_pct for t in trades if t.return_pct > 0]
        loss_returns = [t.return_pct for t in trades if t.return_pct <= 0]
        result.win_rate = len(win_returns) / len(trades) * 100 if trades else 0.0
        result.avg_win_pct = float(np.mean(win_returns)) if win_returns else 0.0
        result.avg_loss_pct = float(np.mean(loss_returns)) if loss_returns else 0.0

        abs_win = sum(t.return_pct for t in trades if t.return_pct > 0)
        abs_loss = abs(sum(t.return_pct for t in trades if t.return_pct <= 0))
        result.profit_factor = abs_win / abs_loss if abs_loss > 1e-10 else 0.0

        result.profit_loss_ratio = (
            abs(result.avg_win_pct / result.avg_loss_pct) if result.avg_loss_pct != 0 else 0.0
        )

        # Max consecutive losses
        max_consec = 0
        cur_consec = 0
        for t in trades:
            if t.return_pct <= 0:
                cur_consec += 1
                max_consec = max(max_consec, cur_consec)
            else:
                cur_consec = 0
        result.max_consecutive_loss = max_consec

    # Benchmark return
    if benchmark_curve and len(benchmark_curve) > 0:
        result.benchmark_return_pct = (
            (benchmark_curve[-1] - benchmark_curve[0]) / benchmark_curve[0] * 100
        )

    # Trades as dicts
    result.trades = [
        {
            "code": t.code,
            "name": t.name,
            "entry_date": t.entry_date,
            "exit_date": t.exit_date,
            "entry_price": round(t.entry_price, 2),
            "exit_price": round(t.exit_price, 2),
            "return_pct": t.return_pct,
            "exit_reason": t.exit_reason,
            "hold_days": t.hold_days,
        }
        for t in trades
    ]

    return result
