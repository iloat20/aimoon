"""Data models for the enhanced backtest engine.

Extracted from enhanced_backtest.py for modularity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class EnhancedPosition:
    """回测引擎中的持仓记录。"""

    name: str
    entry_price: float
    entry_date: pd.Timestamp
    weight: float
    sector: str
    stop_loss: float
    entry_score: int
    peak_pnl: float = 0.0
    highest_price: float = 0.0
    atr_at_entry: float = 0.0

    def with_update(self, **kwargs: Any) -> EnhancedPosition:
        """返回更新后的新实例（不可变模式）。"""
        from dataclasses import replace

        return replace(self, **kwargs)


@dataclass(frozen=True)
class EnhancedTrade:
    """Completed trade record."""

    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    cost_pct: float
    exit_reason: str
    hold_days: int


@dataclass(frozen=True)
class EnhancedPortfolioResult:
    """Backtest portfolio performance result."""

    total_return: float
    annual_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    avg_hold_days: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    benchmark_return: float
    excess_return: float
    calmar_ratio: float
    trades: tuple
    equity_curve: tuple
    drawdown_curve: tuple
    # Vibe-Trading 移植指标
    profit_loss_ratio: float = 0.0
    max_consecutive_loss: int = 0
    information_ratio: float = 0.0
    # IC 时序追踪
    ic_series: tuple[float, ...] = ()
    ic_dates: tuple[str, ...] = ()


@dataclass(frozen=True)
class PhaseState:
    """每个 bar 回测阶段的中间状态。"""

    positions: dict[str, dict]
    pending_entries: dict[str, dict]
    trades: list[EnhancedTrade]
    weak_streak: dict[str, int]
    recent_exits: dict[str, int]
    stop_loss_count: dict[str, int]
    closed_return: float
    bar_count: int
