from __future__ import annotations
import pytest
from aimoon.risk import RiskLimits, Position, PortfolioState, kelly_criterion, volatility_position_size, check_risk_limits


class TestKellyCriterion:
    def test_positive_edge(self):
        k = kelly_criterion(0.6, 0.05, 0.03)
        assert 0.0 < k <= 0.5

    def test_no_edge_returns_zero(self):
        assert kelly_criterion(0.5, 0.03, 0.05) == 0.0

    def test_invalid_inputs(self):
        assert kelly_criterion(0.0, 0.05, 0.03) == 0.0
        assert kelly_criterion(1.0, 0.05, 0.03) == 0.0
        assert kelly_criterion(0.6, 0.05, 0.0) == 0.0

    def test_capped_at_half_kelly(self):
        k = kelly_criterion(0.9, 0.1, 0.01)
        assert k <= 0.5


class TestVolatilityPositionSize:
    def test_basic_sizing(self):
        size = volatility_position_size(0.15, 0.30)
        assert size == 0.1

    def test_max_cap(self):
        size = volatility_position_size(0.15, 0.10, max_pct=0.08)
        assert size == 0.08

    def test_zero_vol(self):
        assert volatility_position_size(0.15, 0.0) == 0.0


class TestPosition:
    def test_unrealized_pnl(self):
        pos = Position(code='000001', name='Test', weight=0.1, entry_price=10.0, current_price=12.0)
        assert pos.unrealized_pnl == 0.2

    def test_stop_loss_not_triggered(self):
        pos = Position(code='000001', name='Test', weight=0.1, entry_price=10.0, current_price=9.6)
        assert pos.is_stopped_out is False

    def test_stop_loss_triggered(self):
        pos = Position(code='000001', name='Test', weight=0.1, entry_price=10.0, current_price=9.4)
        assert pos.is_stopped_out is True

    def test_take_profit(self):
        pos = Position(code='000001', name='Test', weight=0.1, entry_price=10.0, current_price=13.1)
        assert pos.is_take_profit is True


class TestPortfolioState:
    def test_total_exposure(self):
        ps = PortfolioState()
        ps.positions['A'] = Position(code='A', name='A', weight=0.1, entry_price=10.0)
        ps.positions['B'] = Position(code='B', name='B', weight=0.2, entry_price=20.0)
        assert abs(ps.total_exposure - 0.3) < 1e-10

    def test_current_drawdown(self):
        ps = PortfolioState(peak_value=100.0, current_value=85.0)
        assert ps.current_drawdown == 0.15

    def test_sector_exposure(self):
        ps = PortfolioState()
        ps.positions['A'] = Position(code='A', name='A', weight=0.1, entry_price=10.0, sector='Tech')
        ps.positions['B'] = Position(code='B', name='B', weight=0.2, entry_price=20.0, sector='Tech')
        ps.positions['C'] = Position(code='C', name='C', weight=0.15, entry_price=15.0, sector='Finance')
        exp = ps.sector_exposure()
        assert abs(exp['Tech'] - 0.3) < 1e-10
        assert abs(exp['Finance'] - 0.15) < 1e-10


class TestCheckRiskLimits:
    def test_no_violations(self):
        ps = PortfolioState(cash=0.5)
        ps.positions['A'] = Position(code='A', name='A', weight=0.05, entry_price=10.0)
        limits = RiskLimits()
        violations = check_risk_limits(ps, limits)
        assert len(violations) == 0

    def test_max_position_violation(self):
        ps = PortfolioState()
        ps.positions['A'] = Position(code='A', name='A', weight=0.15, entry_price=10.0)
        limits = RiskLimits(max_position_pct=0.10)
        violations = check_risk_limits(ps, limits)
        assert len(violations) == 1

    def test_max_drawdown_violation(self):
        ps = PortfolioState(peak_value=100.0, current_value=80.0)
        limits = RiskLimits(max_drawdown_limit=0.15)
        violations = check_risk_limits(ps, limits)
        assert len(violations) >= 1
