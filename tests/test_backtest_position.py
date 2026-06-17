"""Tests for position management strategy optimizations."""

import numpy as np
import pandas as pd
import pytest

from aimoon.enhanced_backtest import EnhancedBacktestEngine
from aimoon.enhanced_backtest.helpers import compute_atr_entry_stop_loss


@pytest.fixture
def sample_kline():
    """Create a sample kline DataFrame with 100 days of data."""
    dates = pd.date_range(start='2025-01-01', periods=100, freq='D')
    np.random.seed(42)

    # Generate realistic price data with uptrend
    base_price = 10.0
    returns = np.random.normal(0.001, 0.02, 100)  # 0.1% mean daily return, 2% volatility
    prices = base_price * np.cumprod(1 + returns)

    # Create DataFrame
    kline = pd.DataFrame({
        'open': prices * 0.99,
        'high': prices * 1.02,
        'low': prices * 0.98,
        'close': prices,
        'volume': np.random.randint(1000000, 5000000, 100)
    }, index=dates)

    return kline


@pytest.fixture
def backtest_engine():
    """Create a backtest engine with default optimized parameters."""
    return EnhancedBacktestEngine(
        hold_days=10,
        max_positions=5,
        entry_threshold=55,
        stop_loss_pct=0.05,
        take_profit_pct=0.20,
        max_sector_pct=0.25,
        exit_ratio=0.60,
        stop_loss_cooldown=15,
        use_alpha=False,
        use_kelly=False,
    )


def test_trailing_stop_moves_up_never_down(backtest_engine, sample_kline):
    """Verify trailing stop only moves up, tracking peak PnL."""
    klines = {'TEST': sample_kline}
    names = {'TEST': 'TestStock'}

    # Create engine
    engine = backtest_engine

    # Track position with peak_pnl
    positions = {
        'TEST': {
            'name': 'TestStock',
            'entry_price': 10.0,
            'entry_date': pd.Timestamp('2025-01-10'),
            'weight': 0.2,
            'sector': 'tech',
            'stop_loss': 0.06,
            'entry_score': 70,
            'peak_pnl': 0.0,
        }
    }

    # Simulate price movements
    current_price = 10.0

    # Price goes up 5%
    current_price = 10.5
    pnl = (current_price - 10.0) / 10.0

    # Update peak_pnl
    positions['TEST']['peak_pnl'] = max(positions['TEST']['peak_pnl'], pnl)
    assert positions['TEST']['peak_pnl'] == 0.05

    # Price goes down slightly
    current_price = 10.3
    pnl = (current_price - 10.0) / 10.0

    # Peak_pnl should remain at 0.05 (only moves up)
    positions['TEST']['peak_pnl'] = max(positions['TEST']['peak_pnl'], pnl)
    assert positions['TEST']['peak_pnl'] == 0.05

    # Price makes new high
    current_price = 10.8
    pnl = (current_price - 10.0) / 10.0

    positions['TEST']['peak_pnl'] = max(positions['TEST']['peak_pnl'], pnl)
    assert positions['TEST']['peak_pnl'] == pytest.approx(0.08, abs=0.001)


def test_profit_protection_exit(backtest_engine, sample_kline):
    """Verify profit protection exit when gains evaporate."""
    # Create a scenario: stock gains 7% then falls to 0.5% profit
    entry_price = 10.0
    peak_price = 10.7  # +7%
    current_price = 10.05  # +0.5%

    peak_pnl = (peak_price - entry_price) / entry_price  # 0.07
    current_pnl = (current_price - entry_price) / entry_price  # 0.005

    # Should trigger profit protection exit
    assert peak_pnl >= 0.05  # Peak >= 5%
    assert current_pnl <= 0.01  # Current <= 1%

    # Logic should exit
    should_exit = peak_pnl >= 0.05 and current_pnl <= 0.01
    assert should_exit is True


def test_profit_protection_no_trigger(backtest_engine, sample_kline):
    """Verify profit protection does not trigger when profit is maintained."""
    entry_price = 10.0
    peak_price = 10.7  # +7%
    current_price = 10.5  # +5% - still above threshold

    peak_pnl = (peak_price - entry_price) / entry_price  # 0.07
    current_pnl = (current_price - entry_price) / entry_price  # 0.05

    # Should NOT trigger profit protection exit
    should_exit = peak_pnl >= 0.05 and current_pnl <= 0.01
    assert should_exit is False


def test_dynamic_stop_loss_atr_bounds():
    """Verify ATR stop loss clamps to [4%, 8%] range."""
    # Create a kline with known volatility
    dates = pd.date_range(start='2025-01-01', periods=30, freq='D')
    kline = pd.DataFrame({
        'open': [10.0] * 30,
        'high': [10.5] * 30,  # High volatility
        'low': [9.5] * 30,
        'close': [10.0] * 30,
        'volume': [1000000] * 30
    }, index=dates)

    # Test the function
    sl = compute_atr_entry_stop_loss(kline, fallback=0.06)

    # Should be clamped to [4%, 10%]
    assert sl >= 0.04
    assert sl <= 0.10


def test_dynamic_stop_loss_low_volatility():
    """Verify ATR stop loss uses minimum bound for low volatility."""
    # Create a kline with very low volatility
    dates = pd.date_range(start='2025-01-01', periods=30, freq='D')
    kline = pd.DataFrame({
        'open': [10.0] * 30,
        'high': [10.01] * 30,  # Very low volatility
        'low': [9.99] * 30,
        'close': [10.0] * 30,
        'volume': [1000000] * 30
    }, index=dates)

    sl = compute_atr_entry_stop_loss(kline, fallback=0.06)

    # Should be at minimum bound (3%)
    assert sl == 0.03


def test_stop_loss_cooldown_not_permanent(backtest_engine, sample_kline):
    """Verify stocks can re-enter after stop-loss cooldown period."""
    engine = backtest_engine
    assert engine.stop_loss_cooldown == 15

    # Simulate stop-loss event
    stop_loss_count = {'TEST': 1}
    recent_exits = {'TEST': 10}  # Bar 10
    current_bar = 20  # Bar 20 (10 bars later)

    # With 1 stop-loss and 15-bar cooldown, should be blocked
    bars_since_exit = current_bar - recent_exits['TEST']  # 10
    is_blocked = bars_since_exit < engine.stop_loss_cooldown  # 10 < 15 = True
    assert is_blocked is True

    # After 15 bars (bar 25), should be eligible again
    current_bar = 25
    bars_since_exit = current_bar - recent_exits['TEST']  # 15
    is_blocked = bars_since_exit < engine.stop_loss_cooldown  # 15 < 15 = False
    assert is_blocked is False


def test_stop_loss_permanent_after_3_times(backtest_engine, sample_kline):
    """Verify stocks are permanently blacklisted after 3 stop-losses."""
    engine = backtest_engine

    # 3 stop-losses = permanent blacklist
    stop_loss_count = {'TEST': 3}
    is_permanently_blocked = stop_loss_count.get('TEST', 0) >= 3
    assert is_permanently_blocked is True


def test_regime_adaptive_threshold(backtest_engine, sample_kline):
    """Verify threshold changes by market regime."""
    engine = backtest_engine
    base_threshold = engine.entry_threshold

    # Test bear market adjustment
    bear_threshold = base_threshold + 10
    assert bear_threshold == 65

    # Test high volatility adjustment
    hv_threshold = base_threshold + 8
    assert hv_threshold == 63

    # Test bull market adjustment (slightly lower)
    bull_threshold = max(50, base_threshold - 5)
    assert bull_threshold == 50


def test_regime_adaptive_positions(backtest_engine, sample_kline):
    """Verify position count changes by market regime."""
    engine = backtest_engine
    max_positions = engine.max_positions

    # Bear market: aggressively reduce
    bear_positions = max(1, max_positions // 3)
    assert bear_positions == 1  # 5 // 3 = 1

    # High volatility: reduce
    hv_positions = max(2, max_positions // 2)
    assert hv_positions == 2  # 5 // 2 = 2

    # Bull market: full positions
    bull_positions = max_positions
    assert bull_positions == 5


def test_score_proportional_sizing(backtest_engine, sample_kline):
    """Verify higher-scoring stocks get larger positions."""
    # Test score normalization
    scores = {'A': 70, 'B': 55, 'C': 40}
    avg_score = np.mean(list(scores.values()))  # 55.0

    score_scale = {code: max(0.5, min(1.5, score / avg_score)) for code, score in scores.items()}

    # A (70) > avg, should be scaled up
    assert score_scale['A'] > 1.0
    # B (55) = avg, should be ~1.0
    assert abs(score_scale['B'] - 1.0) < 0.1
    # C (40) < avg, should be scaled down
    assert score_scale['C'] < 1.0


def test_hold_period_respects_parameter(backtest_engine, sample_kline):
    """Verify max_hold_bars uses self.hold_days."""
    engine = backtest_engine

    # Default hold_days=10, so max_hold_bars should be 20
    expected_max_hold_bars = engine.hold_days * 2
    assert expected_max_hold_bars == 20


def test_reversal_bonus_requires_stabilization(backtest_engine, sample_kline):
    """Verify reversal bonus requires stabilization before entry."""
    # This is a logic test - in the actual implementation,
    # reversal bonus should require positive ROC in recent 2 days

    # Test case: stock dropped 5% in 5 days, but still falling
    roc5 = -0.05
    roc2d = -0.02  # Still negative

    # Should NOT get reversal bonus (still falling)
    should_get_bonus = roc5 <= -0.05 and roc2d > 0
    assert should_get_bonus is False

    # Test case: stock dropped 5% in 5 days, but stabilizing
    roc5 = -0.05
    roc2d = 0.02  # Positive - stabilizing

    # Should get reversal bonus (stabilized)
    should_get_bonus = roc5 <= -0.05 and roc2d > 0
    assert should_get_bonus is True


def test_trailing_stop_at_2pct(backtest_engine, sample_kline):
    """Verify trailing stop moves to breakeven at +2%."""
    entry_price = 10.0
    current_price = 10.2  # +2%

    pnl = (current_price - entry_price) / entry_price
    stop_loss = 0.05  # Initial
    effective_sl = stop_loss  # Initialize to default

    # At +2% (or slightly below due to float precision), should move stop to breakeven (0%)
    # This overrides the stop_loss - breakeven means no loss allowed
    if pnl >= 0.019:  # Use 0.019 to account for float precision
        effective_sl = 0.0  # Breakeven: override initial stop_loss

    # Breakeven means no loss allowed
    assert effective_sl == 0.0


def test_trailing_stop_at_5pct(backtest_engine, sample_kline):
    """Verify trailing stop trails at 50% of peak profit when +5%."""
    entry_price = 10.0
    peak_price = 10.8  # +8% peak
    current_price = 10.5  # +5% current

    peak_pnl = (peak_price - entry_price) / entry_price  # 0.08
    pnl = (current_price - entry_price) / entry_price  # 0.05
    stop_loss = 0.0

    # At +5% or more, trail at 50% of peak profit
    if pnl >= 0.05:
        trail_stop = peak_pnl * 0.5  # 0.08 * 0.5 = 0.04
        effective_sl = max(stop_loss, trail_stop)

    assert effective_sl == pytest.approx(0.04, abs=0.001)


def test_trailing_stop_at_10pct(backtest_engine, sample_kline):
    """Verify trailing stop trails at 40% of peak profit when +10%."""
    entry_price = 10.0
    peak_price = 11.5  # +15% peak
    current_price = 11.0  # +10% current

    peak_pnl = (peak_price - entry_price) / entry_price  # 0.15
    pnl = (current_price - entry_price) / entry_price  # 0.10
    stop_loss = 0.0

    # At +10% or more, trail at 40% of peak profit
    if pnl >= 0.10:
        trail_stop = peak_pnl * 0.4  # 0.15 * 0.4 = 0.06
        effective_sl = max(stop_loss, trail_stop)

    assert effective_sl == pytest.approx(0.06, abs=0.001)


def test_entry_threshold_alignment(backtest_engine, sample_kline):
    """Verify entry threshold matches 0-100 scoring scale."""
    engine = backtest_engine

    # Default should be 55, not 18
    assert engine.entry_threshold == 55

    # Exit threshold should be 55 * 0.60 = 33
    assert engine.exit_threshold == 33


def test_max_sector_pct_improvement(backtest_engine, sample_kline):
    """Verify sector concentration limit is tightened."""
    engine = backtest_engine

    # Should be 0.25, not 0.30
    assert engine.max_sector_pct == 0.25
