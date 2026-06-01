
"""Risk management: position sizing, portfolio constraints, drawdown control."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskLimits:
    """Portfolio-level risk constraints."""
    max_position_pct: float = 0.10
    max_sector_pct: float = 0.30
    max_total_positions: int = 20
    target_volatility: float = 0.15
    max_drawdown_limit: float = 0.15
    min_position_size: float = 0.02


@dataclass(frozen=True)
class Position:
    """A single portfolio position."""
    code: str
    name: str
    weight: float
    entry_price: float
    current_price: float = 0.0
    stop_loss_pct: float = 0.07
    take_profit_pct: float = 0.20
    sector: str = ""

    @property
    def unrealized_pnl(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price

    @property
    def is_stopped_out(self) -> bool:
        return self.unrealized_pnl <= -self.stop_loss_pct

    @property
    def is_take_profit(self) -> bool:
        return self.unrealized_pnl >= self.take_profit_pct


@dataclass
class PortfolioState:
    """Current portfolio state for risk checks."""
    positions: dict = field(default_factory=dict)
    cash: float = 1.0
    peak_value: float = 1.0
    current_value: float = 1.0

    @property
    def total_exposure(self) -> float:
        return sum(p.weight for p in self.positions.values())

    @property
    def current_drawdown(self) -> float:
        if self.peak_value <= 0:
            return 0.0
        return (self.peak_value - self.current_value) / self.peak_value

    def sector_exposure(self) -> dict:
        result = {}
        for p in self.positions.values():
            result[p.sector] = result.get(p.sector, 0.0) + p.weight
        return result


def kelly_criterion(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Calculate Kelly fraction for position sizing."""
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0
    b = avg_win / avg_loss
    kelly = (b * win_rate - (1 - win_rate)) / b
    return max(0.0, min(kelly, 0.5))


def volatility_position_size(target_vol: float, asset_vol: float, max_pct: float = 0.10) -> float:
    """Volatility-targeted position sizing."""
    if asset_vol <= 0:
        return 0.0
    return min(target_vol / asset_vol, max_pct)


def check_risk_limits(portfolio: PortfolioState, limits: RiskLimits) -> list:
    """Check portfolio against risk limits. Returns list of violations."""
    violations = []
    if portfolio.current_drawdown > limits.max_drawdown_limit:
        violations.append(("max_drawdown", portfolio.current_drawdown, limits.max_drawdown_limit))
    for code, pos in portfolio.positions.items():
        if pos.weight > limits.max_position_pct:
            violations.append(("max_position", code, pos.weight, limits.max_position_pct))
    for sector, exp in portfolio.sector_exposure().items():
        if exp > limits.max_sector_pct:
            violations.append(("max_sector", sector, exp, limits.max_sector_pct))
    return violations
