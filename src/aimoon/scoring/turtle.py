"""Super Turtle Trading Strategy.

Classic Turtle Trading Rules (Richard Dennis, 1983) with enhancements:
1. Donchian Channel breakout for entry signals
2. ATR-based position sizing (fixed fractional)
3. Pyramiding (add to winning positions)
4. Chandelier Exit / Donchian lower band for exits

References:
- "The Original Turtle Trading Rules" – Curtis Faith
- "Way of the Turtle" – Curtis Faith
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import pandas as pd

logger = logging.getLogger(__name__)


# ── Default parameters ──
_DEFAULT_ENTRY_PERIOD: int = 20  # Donchian upper band lookback
_DEFAULT_EXIT_PERIOD: int = 10  # Donchian lower band lookback (fast exit)
_DEFAULT_ATR_PERIOD: int = 20  # ATR period for position sizing
_DEFAULT_RISK_PCT: float = 0.02  # Risk 2% of equity per unit
_DEFAULT_MAX_UNITS: int = 4  # Max pyramid units per position
_DEFAULT_EQUITY: float = 1_000_000.0  # Default equity for position sizing calc


@dataclass(frozen=True)
class TurtlePlan:
    """Complete Turtle trading plan for one stock on one date.

    Contains all the actionable price levels a trader needs.
    """

    code: str
    name: str
    date: pd.Timestamp
    signal_type: Literal["buy", "add", "close", "hold"]
    current_price: float
    atr: float
    # ── Entry prices ──
    entry_price: float = 0.0  # 买入触发价（突破上轨）
    entry_stop_loss: float = 0.0  # 买入止损价（-1 ATR）
    # ── Pyramid prices ──
    add_prices: tuple[float, ...] = ()  # 各次加仓触发价
    add_stop_losses: tuple[float, ...] = ()  # 各加仓批次的止损价
    # ── Exit prices ──
    exit_price: float = 0.0  # 清仓触发价（跌破下轨）
    chandelier_stop: float = 0.0  # Chandelier 跟踪止损价
    # ── Take profit ──
    tp1_price: float = 0.0  # 第一目标价（+2 ATR）
    tp2_price: float = 0.0  # 第二目标价（+4 ATR）
    # ── Position sizing ──
    shares_per_unit: int = 0  # 每单位股数（按万元整数）
    risk_per_unit: float = 0.0  # 每单位风险金额
    max_units: int = _DEFAULT_MAX_UNITS
    # ── Metadata ──
    reason: str = ""
    donchian_upper: float = 0.0
    donchian_lower: float = 0.0

    def format_plan(self) -> str:
        """Format the trading plan as a concise string for display."""
        parts: list[str] = []

        if self.signal_type == "buy":
            parts.append(f"买入={self.entry_price:.2f}")
            parts.append(f"止损={self.entry_stop_loss:.2f}")
            if self.add_prices:
                parts.append(f"加仓={','.join(f'{p:.2f}' for p in self.add_prices)}")
            if self.tp1_price > 0:
                parts.append(f"目标1={self.tp1_price:.2f}")
            if self.tp2_price > 0:
                parts.append(f"目标2={self.tp2_price:.2f}")
            parts.append(f"清仓={self.exit_price:.2f}")
            if self.shares_per_unit > 0:
                parts.append(f"每单位{self.shares_per_unit}股")
        elif self.signal_type == "add":
            parts.append(f"加仓={self.entry_price:.2f}")
            parts.append(f"止损={self.entry_stop_loss:.2f}")
            if self.tp1_price > 0:
                parts.append(f"目标={self.tp1_price:.2f}")
            parts.append(f"清仓={self.exit_price:.2f}")
        elif self.signal_type == "close":
            parts.append(f"清仓={self.exit_price:.2f}")
            if self.chandelier_stop > 0:
                parts.append(f"跟踪止损={self.chandelier_stop:.2f}")
        else:
            parts.append("观望")
            if self.donchian_upper > 0:
                parts.append(f"突破{self.donchian_upper:.2f}买入")
            if self.donchian_lower > 0:
                parts.append(f"跌破{self.donchian_lower:.2f}卖出")

        return " | ".join(parts)


@dataclass(frozen=True)
class TurtleSignal:
    """A single Turtle trading signal for one stock on one date (legacy)."""

    code: str
    name: str
    date: pd.Timestamp
    signal_type: Literal["buy", "sell", "add", "close", "hold"]
    price: float
    atr: float
    donchian_upper: float
    donchian_lower: float
    units: int = 0
    reason: str = ""


def compute_donchian(
    high: pd.Series,
    low: pd.Series,
    period: int = _DEFAULT_ENTRY_PERIOD,
) -> tuple[pd.Series, pd.Series]:
    """Compute Donchian Channel upper and lower bands.

    Upper band = highest high of last N periods (excluding current bar).
    Lower band = lowest low of last N periods (excluding current bar).
    """
    upper = high.rolling(window=period).max().shift(1)
    lower = low.rolling(window=period).min().shift(1)
    return upper, lower


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = _DEFAULT_ATR_PERIOD,
) -> pd.Series:
    """Compute ATR (Average True Range)."""
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period).mean()


def compute_chandelier_exit(
    high: pd.Series,
    close: pd.Series,
    low: pd.Series,
    period: int = _DEFAULT_ENTRY_PERIOD,
    atr_multiplier: float = 3.0,
) -> pd.Series:
    """Compute Chandelier Exit (trailing stop based on ATR).

    Chandelier Exit = Highest High(period) - multiplier * ATR(period)
    """
    atr = compute_atr(high, low, close, period)
    highest = high.rolling(window=period).max()
    return highest - atr_multiplier * atr


def generate_turtle_plan(
    kline: pd.DataFrame,
    code: str = "",
    name: str = "",
    equity: float = _DEFAULT_EQUITY,
    entry_period: int = _DEFAULT_ENTRY_PERIOD,
    exit_period: int = _DEFAULT_EXIT_PERIOD,
    atr_period: int = _DEFAULT_ATR_PERIOD,
) -> TurtlePlan | None:
    """Generate a complete Turtle trading plan for the latest bar.

    Calculates all actionable price levels:
    - Entry price (breakout above Donchian upper band)
    - Stop loss per unit (entry - 1 ATR)
    - Pyramid add prices (entry + 0.5 * ATR * unit_number)
    - Take profit targets (entry + 2/4 ATR)
    - Exit price (breakout below Donchian lower band)
    - Chandelier trailing stop
    - Position sizing (shares per unit based on equity * 2% / ATR)

    Args:
        kline: OHLCV DataFrame with columns: open, high, low, close, volume.
        code: Stock code.
        name: Stock name.
        equity: Account equity for position sizing.
        entry_period: Donchian upper band lookback.
        exit_period: Donchian lower band lookback.
        atr_period: ATR period.

    Returns:
        TurtlePlan with all price levels, or None if insufficient data.
    """
    min_bars = max(entry_period, exit_period, atr_period) + 5
    if len(kline) < min_bars:
        return None

    close = kline["close"]
    high = kline["high"]
    low = kline["low"]

    donchian_upper, _ = compute_donchian(high, low, entry_period)
    _, donchian_lower = compute_donchian(high, low, exit_period)
    atr_series = compute_atr(high, low, close, atr_period)
    chandelier = compute_chandelier_exit(high, close, low, entry_period, 3.0)

    # Use the latest bar values
    i = len(kline) - 1
    price = float(close.iloc[i])
    atr_val = float(atr_series.iloc[i]) if not pd.isna(atr_series.iloc[i]) else 0.0
    upper = float(donchian_upper.iloc[i]) if not pd.isna(donchian_upper.iloc[i]) else 0.0
    lower = float(donchian_lower.iloc[i]) if not pd.isna(donchian_lower.iloc[i]) else 0.0
    chandelier_val = float(chandelier.iloc[i]) if not pd.isna(chandelier.iloc[i]) else 0.0

    if atr_val <= 0 or upper <= 0 or lower <= 0:
        return None

    date = kline.index[i]

    # Determine signal type
    if price > upper:
        signal_type = "buy"
    elif price < lower:
        signal_type = "close"
    else:
        # Check if we are in an "add" zone (between entry and upper)
        signal_type = "hold"

    # ── Entry price: breakout above upper band ──
    entry_price = round(upper, 2)

    # ── Stop loss: entry - 1 ATR ──
    entry_stop_loss = round(entry_price - atr_val, 2)

    # ── Pyramid add prices: entry + 0.5 * ATR * n ──
    add_prices_list: list[float] = []
    add_stop_losses_list: list[float] = []
    for n in range(1, _DEFAULT_MAX_UNITS + 1):
        add_price = round(entry_price + 0.5 * atr_val * n, 2)
        add_prices_list.append(add_price)
        # Each pyramid unit has its own stop loss
        add_sl = round(add_price - atr_val, 2)
        add_stop_losses_list.append(add_sl)

    # ── Take profit targets ──
    tp1 = round(entry_price + 2.0 * atr_val, 2)
    tp2 = round(entry_price + 4.0 * atr_val, 2)

    # ── Exit price: below lower band ──
    exit_price = round(lower, 2)

    # ── Position sizing: equity * 2% / ATR, rounded to 100 shares ──
    risk_amount = equity * _DEFAULT_RISK_PCT
    shares_raw = risk_amount / atr_val if atr_val > 0 else 0
    shares_per_unit = max(100, int(shares_raw // 100) * 100)  # Round down to 100s

    # ── Reason string ──
    if signal_type == "buy":
        reason = f"突破{entry_period}日上轨{upper:.2f}"
    elif signal_type == "close":
        reason = f"跌破{exit_period}日下轨{lower:.2f}"
    else:
        reason = f"区间震荡(上轨{upper:.2f}/下轨{lower:.2f})"

    return TurtlePlan(
        code=code,
        name=name,
        date=date,
        signal_type=signal_type,
        current_price=price,
        atr=atr_val,
        entry_price=entry_price,
        entry_stop_loss=entry_stop_loss,
        add_prices=tuple(add_prices_list),
        add_stop_losses=tuple(add_stop_losses_list),
        exit_price=exit_price,
        chandelier_stop=round(chandelier_val, 2) if chandelier_val > 0 else 0.0,
        tp1_price=tp1,
        tp2_price=tp2,
        shares_per_unit=shares_per_unit,
        risk_per_unit=round(risk_amount, 2),
        reason=reason,
        donchian_upper=upper,
        donchian_lower=lower,
    )


def generate_turtle_signals(
    kline: pd.DataFrame,
    code: str = "",
    name: str = "",
    entry_period: int = _DEFAULT_ENTRY_PERIOD,
    exit_period: int = _DEFAULT_EXIT_PERIOD,
    atr_period: int = _DEFAULT_ATR_PERIOD,
) -> list[TurtleSignal]:
    """Generate Turtle trading signals for a single stock (legacy interface).

    Scans the full kline history and produces buy/add/sell signals.
    """
    if len(kline) < max(entry_period, exit_period, atr_period) + 5:
        return []

    close = kline["close"]
    high = kline["high"]
    low = kline["low"]

    donchian_upper, _ = compute_donchian(high, low, entry_period)
    _, donchian_lower = compute_donchian(high, low, exit_period)
    atr = compute_atr(high, low, close, atr_period)

    signals: list[TurtleSignal] = []

    for i in range(len(kline)):
        date = kline.index[i]
        price = float(close.iloc[i])
        atr_val = float(atr.iloc[i]) if not pd.isna(atr.iloc[i]) else 0.0
        upper = float(donchian_upper.iloc[i]) if not pd.isna(donchian_upper.iloc[i]) else 0.0
        lower = float(donchian_lower.iloc[i]) if not pd.isna(donchian_lower.iloc[i]) else 0.0

        if atr_val <= 0 or upper <= 0 or lower <= 0:
            continue

        if price > upper:
            signals.append(
                TurtleSignal(
                    code=code,
                    name=name,
                    date=date,
                    signal_type="buy",
                    price=price,
                    atr=atr_val,
                    donchian_upper=upper,
                    donchian_lower=lower,
                    units=1,
                    reason=f"突破{entry_period}日上轨({upper:.2f})",
                )
            )
        elif price < lower:
            signals.append(
                TurtleSignal(
                    code=code,
                    name=name,
                    date=date,
                    signal_type="close",
                    price=price,
                    atr=atr_val,
                    donchian_upper=upper,
                    donchian_lower=lower,
                    units=0,
                    reason=f"跌破{exit_period}日下轨({lower:.2f})",
                )
            )

    return signals


def get_latest_turtle_signal(
    kline: pd.DataFrame,
    code: str = "",
    name: str = "",
) -> TurtleSignal | None:
    """Get the most recent Turtle signal for a stock (convenience wrapper)."""
    signals = generate_turtle_signals(kline, code, name)
    return signals[-1] if signals else None
