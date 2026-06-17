"""Data models for QF-Lib backtest configuration and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QFBacktestConfig:
    """Serializable configuration for a QF-Lib backtest run."""

    # Core params
    start_date: str = ""
    end_date: str = ""
    initial_cash: float = 1_000_000.0
    hold_days: int = 12
    max_positions: int = 4

    # Costs
    commission_pct: float = 0.0003
    slippage_pct: float = 0.001
    stamp_tax_pct: float = 0.0005

    # Entry/exit
    entry_threshold: int = 50
    stop_loss_pct: float = 0.035
    take_profit_pct: float = 0.14

    # Risk
    max_sector_pct: float = 0.30
    use_kelly: bool = False
    use_reversal: bool = False

    # ML
    use_ml: bool = True

    # Regime (pre-computed)
    regime: str = "sideways"

    # Benchmark
    benchmark_code: str = "000300"

    # Output
    backtest_name: str = "aimoon QF-Lib Backtest"


@dataclass
class QFTradeRecord:
    """Simplified trade record for JSON serialization."""

    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    exit_reason: str
    hold_days: int


@dataclass
class QFBacktestResult:
    """Backtest results for JSON serialization."""

    total_return_pct: float = 0.0
    annual_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0
    profit_factor: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    benchmark_return_pct: float = 0.0
    calmar_ratio: float = 0.0
    profit_loss_ratio: float = 0.0
    max_consecutive_loss: int = 0
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    # IC tracking
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ic_positive_pct: float = 0.0
    ic_values: list[float] = field(default_factory=list)
    ic_dates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_return_pct": round(self.total_return_pct, 2),
            "annual_return_pct": round(self.annual_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "sortino_ratio": round(self.sortino_ratio, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "win_rate": round(self.win_rate, 2),
            "trade_count": self.trade_count,
            "profit_factor": round(self.profit_factor, 2),
            "avg_win_pct": round(self.avg_win_pct, 2),
            "avg_loss_pct": round(self.avg_loss_pct, 2),
            "benchmark_return_pct": round(self.benchmark_return_pct, 2),
            "calmar_ratio": round(self.calmar_ratio, 2),
            "profit_loss_ratio": round(self.profit_loss_ratio, 2),
            "max_consecutive_loss": self.max_consecutive_loss,
            "trades": self.trades,
            "equity_curve": [round(v, 2) for v in self.equity_curve],
            "ic_mean": round(self.ic_mean, 4),
            "ic_std": round(self.ic_std, 4),
            "ic_positive_pct": round(self.ic_positive_pct, 2),
        }
