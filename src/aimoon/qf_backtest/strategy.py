"""QF-Lib strategy implementing aimoon's entry/exit logic.

Uses QF-Lib's built-in portfolio/position management and adds
aimoon's custom stop-loss, take-profit, trailing stop, and
ML-driven entry logic.

Enhancements over the base version:
- Trend filter (MA20/MA60 alignment)
- ATR dynamic stop-loss
- Drawdown-based position scaling
- Volatility & market timing filters
- Sector exposure limits
- RPS / reversal bonuses
- 3-layer time stops
- T+1 limit-up/down checks
- IC tracking
"""

from __future__ import annotations

import logging
import warnings
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from aimoon.qf_backtest.imports import QF_AVAILABLE
from aimoon.qf_backtest.models import QFTradeRecord

if QF_AVAILABLE:
    from qf_lib.backtesting.broker.broker import Broker
    from qf_lib.backtesting.order.execution_style import MarketOrder
    from qf_lib.backtesting.order.time_in_force import TimeInForce
    from qf_lib.backtesting.portfolio.portfolio import Portfolio
    from qf_lib.backtesting.strategies.abstract_strategy import AbstractStrategy
    from qf_lib.backtesting.trading_session.trading_session import TradingSession

logger = logging.getLogger(__name__)

# ── Risk constants (mirrors enhanced_backtest.risk_controls) ──────────
TRAILING_STOP_TIERS: list[tuple[float, float]] = [
    (0.05, 0.0),   # +5% -> breakeven
    (0.10, 0.6),   # +10% -> lock 60% of peak
    (0.15, 0.5),   # +15% -> lock 50%
    (0.20, 0.4),   # +20% -> lock 40%
]
HARD_LOSS_CAP: float = 0.05
PROFIT_PROTECTION_PEAK: float = 0.12
PROFIT_PROTECTION_FLOOR: float = 0.05
REGIME_TP: dict[str, float] = {
    "bull": 0.20,
    "sideways": 0.15,
    "bear": 0.08,
    "high_volatility": 0.18,
    "crisis": 0.05,
}
PARTIAL_PROFIT_TAKE: float = 0.15
PARTIAL_PROFIT_SECONDARY: float = 0.25
MAX_HOLD_BARS: int = 22

# ATR stop-loss params (mirrors risk_controls.py)
_STOP_LOSS_ATR_MULT: float = 1.0
_MIN_STOP_LOSS_PCT: float = 0.03
_MAX_STOP_LOSS_PCT: float = 0.07
_TAKE_PROFIT_ATR_MULT: float = 4.0
_MIN_TAKE_PROFIT_PCT: float = 0.12
_MAX_TAKE_PROFIT_PCT: float = 0.30
_CHANDELIER_ATR_MULT: float = 2.0

# Drawdown thresholds (mirrors risk_controls.DD_THRESHOLDS)
DD_THRESHOLDS: list[tuple[float, float]] = [
    (0.05, 0.75),
    (0.07, 0.50),
    (0.10, 0.25),
]

# Time-stop constants
_TIME_STOP_NEGATIVE_DAYS: int = 5
_TIME_STOP_SCORE_DECLINE_RATIO: float = 0.7
_TIME_DECAY_IDLE_DAYS: int = 15
_TIME_DECAY_IDLE_PNL: float = 0.01
_DEAD_MONEY_DAYS: int = 25
_DEAD_MONEY_MIN_PNL: float = 0.015

# RPS / reversal thresholds
_RECENT_RET_THRESHOLD: float = -0.05
_ROC5_DROP_THRESHOLD: float = -0.05
_ROC5_MODERATE_DROP: float = -0.03
_ROC5_RISE_THRESHOLD: float = 0.05

# Position sizing
_RANK_WEIGHTS: list[float] = [0.40, 0.30, 0.15, 0.15]


def _get_atr_value(kline: pd.DataFrame) -> float:
    """Compute ATR(14) from kline."""
    try:
        if len(kline) < 20:
            return 0.0
        from aimoon.indicators.technical import TechInd

        ti = TechInd(kline)
        atr = ti.atr(14)
        return float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
    except Exception:
        return 0.0


def _compute_atr_stop_loss(atr_pct: float) -> float:
    raw = atr_pct * _STOP_LOSS_ATR_MULT / 100.0
    return max(_MIN_STOP_LOSS_PCT, min(_MAX_STOP_LOSS_PCT, raw))


def _compute_atr_take_profit(atr_pct: float) -> float:
    raw = atr_pct * _TAKE_PROFIT_ATR_MULT / 100.0
    return max(_MIN_TAKE_PROFIT_PCT, min(_MAX_TAKE_PROFIT_PCT, raw))


class AimoonStrategy(AbstractStrategy if QF_AVAILABLE else object):  # type: ignore[misc]
    """QF-Lib strategy with full parity to Enhanced engine risk controls."""

    def __init__(
        self,
        ts: TradingSession,
        tickers: list[Any],
        ml_scores_by_date: dict[str, dict[str, int]],
        klines: dict[str, pd.DataFrame],
        stop_loss_pct: float = 0.035,
        take_profit_pct: float = 0.14,
        entry_threshold: int = 50,
        max_positions: int = 4,
        regime: str = "sideways",
        commission_pct: float = 0.0003,
        slippage_pct: float = 0.001,
        stamp_tax_pct: float = 0.0005,
        # ── New params for full-parity features ──
        sector_map: dict[str, str] | None = None,
        benchmark_kline: pd.DataFrame | None = None,
        vol_regime_cache: dict[pd.Timestamp, float] | None = None,
        market_timing_cache: dict[pd.Timestamp, bool] | None = None,
        names: dict[str, str] | None = None,
        stop_loss_cooldown: int = 5,
        max_sector_pct: float = 0.30,
    ) -> None:
        if not QF_AVAILABLE:
            raise ImportError("qf-lib required")
        super().__init__(ts)
        self._ts: TradingSession = ts
        self._broker: Broker = ts.broker
        self._portfolio: Portfolio = ts.portfolio
        self._tickers: list[Any] = tickers
        self._ml_scores_by_date = ml_scores_by_date
        self._klines = klines
        self._stop_loss_pct = stop_loss_pct
        self._take_profit_pct = take_profit_pct
        self._entry_threshold = entry_threshold
        self._max_positions = max_positions
        self._regime = regime
        self._commission_pct = commission_pct
        self._slippage_pct = slippage_pct
        self._stamp_tax_pct = stamp_tax_pct

        self._exit_threshold = int(entry_threshold * 0.6)
        self._stop_loss_cooldown = stop_loss_cooldown
        self._max_sector_pct = max_sector_pct

        # ── Enhanced data ──
        self._sector_map: dict[str, str] = sector_map or {}
        self._benchmark_kline: pd.DataFrame | None = benchmark_kline
        self._vol_regime_cache: dict[pd.Timestamp, float] = vol_regime_cache or {}
        self._market_timing_cache: dict[pd.Timestamp, bool] = market_timing_cache or {}
        self._names: dict[str, str] = names or {}

        # Per-position tracking (keyed by ticker.as_string)
        self._peak_pnl: dict[str, float] = {}
        self._highest_price: dict[str, float] = {}
        self._weak_streak: dict[str, int] = {}
        self._recent_exits: dict[str, datetime] = {}
        self._stop_loss_count: dict[str, int] = {}
        self._partial_taken: set[str] = set()
        self._bar_count: int = 0
        self._pos_atr: dict[str, float] = {}         # ATR at entry
        self._pos_entry_score: dict[str, int] = {}    # Score at entry

        # Trade log
        self.trades: list[QFTradeRecord] = []
        self.equity_curve: list[float] = []

        # IC tracking
        self._prev_ml_scores: dict[str, float] = {}
        self._ic_values: list[float] = []
        self._ic_dates: list[str] = []

    # ==================================================================
    # Main entry point — called per bar
    # ==================================================================
    def calculate_and_place_orders(self) -> None:
        self._bar_count += 1
        now: datetime = self._ts.timer.now()

        # 1. Exit checks for existing positions
        self._check_exits(now)

        # 2. Enter new positions
        self._check_entries(now)

        # 3. Record equity
        try:
            self.equity_curve.append(float(self._portfolio.net_liquidation))
        except (ValueError, TypeError, AttributeError):
            self.equity_curve.append(0.0)

        # 4. IC tracking (every 5 bars)
        if self._bar_count % 5 == 0 and self._prev_ml_scores:
            self._track_ic(now)

        # Cache ML scores for next IC check
        date_str = str(now)[:10]
        if date_str in self._ml_scores_by_date:
            self._prev_ml_scores = self._ml_scores_by_date[date_str]

    # ==================================================================
    # Exit logic (Enhanced parity)
    # ==================================================================
    def _check_exits(self, now: datetime) -> None:
        date_str = str(now)[:10]
        to_close: list[tuple[Any, str]] = []
        kline_date = pd.Timestamp(now.date())

        for ticker in self._tickers:
            code = ticker.as_string
            pos = self._get_position(ticker)
            if pos is None:
                continue

            entry_price = (
                pos._avg_price_per_unit
                if hasattr(pos, "_avg_price_per_unit") and pos._avg_price_per_unit > 0
                else pos.current_price
            )
            entry_date = pos.start_time if pos.start_time is not None else now
            if entry_price <= 0:
                continue

            kline = self._klines.get(code)
            if kline is None:
                to_close.append((ticker, "data_gap"))
                continue

            if not isinstance(kline.index, pd.DatetimeIndex):
                try:
                    kline.index = pd.to_datetime(kline.index)
                except Exception:
                    to_close.append((ticker, "data_gap"))
                    continue

            if kline_date not in kline.index:
                elapsed = (now - entry_date).days if isinstance(entry_date, datetime) else 0
                if elapsed >= MAX_HOLD_BARS:
                    to_close.append((ticker, "max_hold"))
                continue

            # ── T+1: cannot sell on entry day ──
            elapsed = (now - entry_date).days if isinstance(entry_date, datetime) else 0
            if elapsed < 1:
                continue

            # ── T+1: cannot sell if limit-down at open ──
            try:
                from aimoon.data.limit_utils import can_sell_at_open

                if not can_sell_at_open(kline, kline_date):
                    continue
            except Exception:
                pass

            current_price = float(kline.loc[kline_date, "open"])
            pnl = (current_price - entry_price) / entry_price

            # Track peak
            if code not in self._peak_pnl or pnl > self._peak_pnl[code]:
                self._peak_pnl[code] = pnl
            if code not in self._highest_price or current_price > self._highest_price[code]:
                self._highest_price[code] = current_price

            # ── ATR dynamic stop-loss ──
            atr_at_entry = self._pos_atr.get(code, 0.0)
            effective_sl = self._stop_loss_pct
            if atr_at_entry > 0 and entry_price > 0:
                atr_pct = atr_at_entry / entry_price * 100.0
                atr_sl = _compute_atr_stop_loss(atr_pct)
                effective_sl = max(effective_sl, atr_sl)

            # ── Trailing stop ──
            trailing = self._trailing_stop(code, pnl)
            effective_sl = max(trailing, effective_sl)

            # ── Chandelier exit (ATR-based) ──
            if atr_at_entry > 0:
                highest = self._highest_price.get(code, current_price)
                chandelier_stop = (highest - _CHANDELIER_ATR_MULT * atr_at_entry) / entry_price - 1
                effective_sl = max(effective_sl, chandelier_stop)

            # ── Time-deay tightening: 10+ days still losing → tighten SL ──
            if elapsed > 10 and pnl < 0:
                effective_sl = max(effective_sl, effective_sl * 0.80)

            # ── Hard loss cap ──
            if pnl <= -HARD_LOSS_CAP:
                to_close.append((ticker, "stop_loss"))
                continue

            # ── Profit protection ──
            peak = self._peak_pnl.get(code, pnl)
            if pnl > 0 and peak >= PROFIT_PROTECTION_PEAK and pnl <= PROFIT_PROTECTION_FLOOR:
                to_close.append((ticker, "profit_protection"))
                continue

            # ── Stop loss ──
            if pnl <= -effective_sl:
                to_close.append((ticker, "stop_loss"))
                continue

            # ── Take profit (ATR-based + regime) ──
            tp = self._regime_tp(self._take_profit_pct)
            if atr_at_entry > 0 and entry_price > 0:
                atr_pct = atr_at_entry / entry_price * 100.0
                atr_tp = _compute_atr_take_profit(atr_pct)
                tp = min(tp, atr_tp)
            if pnl >= tp:
                to_close.append((ticker, "take_profit"))
                continue

            # ── Partial profit ──
            if pnl >= PARTIAL_PROFIT_TAKE and code not in self._partial_taken:
                to_close.append((ticker, "partial_profit_50"))
                self._partial_taken.add(code)
                continue
            if pnl >= PARTIAL_PROFIT_SECONDARY and code in self._partial_taken:
                to_close.append((ticker, "partial_profit_100"))
                continue

            # ── Time stop: 5 days still negative → force exit ──
            if elapsed >= _TIME_STOP_NEGATIVE_DAYS and pnl < 0:
                to_close.append((ticker, "time_stop_negative"))
                continue

            # ── Time stop: score decline > 30% → exit ──
            if elapsed > 10 and pnl < 0:
                entry_score = self._pos_entry_score.get(code, 50)
                scores = self._ml_scores_by_date.get(date_str, {})
                current_score = scores.get(code, 0)
                if current_score < entry_score * _TIME_STOP_SCORE_DECLINE_RATIO:
                    to_close.append((ticker, "time_stop_score_decline"))
                    continue

            # ── Time decay: idle money ──
            if elapsed > _TIME_DECAY_IDLE_DAYS and 0 < pnl < _TIME_DECAY_IDLE_PNL:
                to_close.append((ticker, "time_decay"))
                continue

            # ── Dead money: 25+ days, pnl < 1.5% ──
            if elapsed >= _DEAD_MONEY_DAYS and 0 < pnl < _DEAD_MONEY_MIN_PNL:
                to_close.append((ticker, "dead_money"))
                continue

            # ── Max hold ──
            if elapsed >= MAX_HOLD_BARS:
                to_close.append((ticker, "hold_period"))
                continue

            # ── Momentum check ──
            scores = self._ml_scores_by_date.get(date_str, {})
            score = scores.get(code, 0)
            if score < self._exit_threshold:
                self._weak_streak[code] = self._weak_streak.get(code, 0) + 1
                if self._weak_streak[code] >= 2:
                    to_close.append((ticker, "momentum_exit"))
            else:
                self._weak_streak.pop(code, None)

        self._execute_closes(to_close, now, date_str)

    # ==================================================================
    # Entry logic (Enhanced parity)
    # ==================================================================
    def _check_entries(self, now: datetime) -> None:
        date_str = str(now)[:10]
        scores = self._ml_scores_by_date.get(date_str, {})
        if not scores:
            return

        # ── Drawdown-based position scaling ──
        effective_positions = self._max_positions
        current_dd = self._current_drawdown()
        for dd_thresh, dd_scale in DD_THRESHOLDS:
            if current_dd > dd_thresh:
                effective_positions = max(1, int(self._max_positions * dd_scale))
                break

        open_count = len(self._open_positions)
        slots = effective_positions - open_count
        if slots <= 0:
            return

        # ── Volatility filter ──
        kline_date = pd.Timestamp(now.date())
        vol_scale = self._vol_regime_cache.get(kline_date, 1.0)
        if vol_scale < 0.5:
            return

        # ── Market timing filter ──
        if not self._market_timing_cache.get(kline_date, True):
            return

        ticker_codes = {t.as_string for t in self._tickers}
        held_codes = {p.ticker().as_string for p in self._open_positions if hasattr(p, "ticker")}

        # ── Build candidates with multi-dimensional scoring ──
        candidates: list[tuple[str, float]] = []
        for code, ml_score in scores.items():
            if code not in ticker_codes or code in held_codes:
                continue

            # Recent exit cooldown
            if code in self._recent_exits:
                last_exit = self._recent_exits[code]
                if isinstance(last_exit, datetime) and (now - last_exit).days < 5:
                    continue

            # Stop-loss cooldown
            sl_count = self._stop_loss_count.get(code, 0)
            if sl_count >= 2:
                continue
            if sl_count > 0 and code in self._recent_exits:
                last_exit = self._recent_exits[code]
                if isinstance(last_exit, datetime) and (now - last_exit).days < self._stop_loss_cooldown:
                    continue

            # ML score minimum gate
            if ml_score < 30:
                continue

            # ── Trend filter: MA20 > MA60 and price > MA60 ──
            kline = self._klines.get(code)
            if kline is not None and len(kline) >= 60:
                try:
                    if not isinstance(kline.index, pd.DatetimeIndex):
                        kline.index = pd.to_datetime(kline.index)
                    if kline_date in kline.index:
                        idx = kline.index.get_loc(kline_date)
                        if idx >= 60:
                            window = kline.iloc[:idx + 1]
                            close_s = window["close"].dropna()
                            if len(close_s) >= 60:
                                ma20 = close_s.rolling(20).mean().iloc[-1]
                                ma60 = close_s.rolling(60).mean().iloc[-1]
                                last_close = close_s.iloc[-1]
                                if not pd.isna(ma60) and not pd.isna(ma20):
                                    if last_close < ma60 or ma20 < ma60:
                                        continue
                except Exception:
                    pass

            # ── Recent return filter ──
            if kline is not None and len(kline) >= 10:
                try:
                    if kline_date in kline.index:
                        idx = kline.index.get_loc(kline_date)
                        if idx >= 6:
                            close_s = kline["close"].dropna()
                            recent_ret = (close_s.iloc[idx] - close_s.iloc[idx - 5]) / close_s.iloc[idx - 5]
                            if recent_ret < _RECENT_RET_THRESHOLD:
                                continue
                except Exception:
                    pass

            # ── Composite score: ML + RPS bonus + reversal bonus ──
            composite = float(ml_score)
            composite += self._rps_bonus(code, kline_date)
            composite += self._reversal_bonus(code, kline_date)

            candidates.append((code, composite))

        if not candidates:
            return

        candidates.sort(key=lambda x: x[1], reverse=True)
        ticker_map = {t.as_string: t for t in self._tickers}

        # ── Sector exposure tracking ──
        sector_exposure: dict[str, float] = {}
        for pos in self._open_positions:
            if hasattr(pos, "ticker"):
                pos_code = pos.ticker().as_string
                sec = self._sector_map.get(pos_code, "")
                if sec:
                    sector_exposure[sec] = sector_exposure.get(sec, 0.0) + (
                        getattr(pos, "_weight", 1.0 / max_positions) if hasattr(pos, "_weight") else 1.0 / effective_positions
                    )

        for idx in range(min(slots, len(candidates))):
            code, _score = candidates[idx]
            ticker = ticker_map.get(code)
            if ticker is None:
                continue

            # ── Sector limit ──
            sec = self._sector_map.get(code, "")
            if sec:
                cur = sector_exposure.get(sec, 0.0)
                weight = _RANK_WEIGHTS[idx] if idx < 4 else 0.25
                if cur + weight > self._max_sector_pct:
                    continue
                sector_exposure[sec] = cur + weight

            weight = _RANK_WEIGHTS[idx] if idx < 4 else 0.25
            self._place_entry(ticker, weight, code, now)

    def _place_entry(self, ticker: Any, weight: float, code: str, now: datetime) -> None:
        # ── T+1: cannot buy if limit-up at open ──
        kline = self._klines.get(code)
        if kline is not None:
            kline_date = pd.Timestamp(now.date())
            try:
                if not isinstance(kline.index, pd.DatetimeIndex):
                    kline.index = pd.to_datetime(kline.index)
                from aimoon.data.limit_utils import can_buy_at_open

                if kline_date in kline.index and not can_buy_at_open(kline, kline_date):
                    return
            except Exception:
                pass

        # ── Record ATR at entry ──
        if kline is not None:
            kline_date = pd.Timestamp(now.date())
            try:
                if not isinstance(kline.index, pd.DatetimeIndex):
                    kline.index = pd.to_datetime(kline.index)
                if kline_date in kline.index:
                    idx = kline.index.get_loc(kline_date)
                    if idx >= 20:
                        entry_window = kline.iloc[:idx]
                        self._pos_atr[code] = _get_atr_value(entry_window)
            except Exception:
                self._pos_atr[code] = 0.0

        # ── Record entry score ──
        date_str = str(now)[:10]
        entry_scores = self._ml_scores_by_date.get(date_str, {})
        self._pos_entry_score[code] = entry_scores.get(code, 50)

        total_value = max(float(self._portfolio.net_liquidation), 1.0)
        target_value = total_value * weight
        try:
            orders = self._ts.order_factory.target_value_orders(
                {ticker: target_value},
                MarketOrder(),
                TimeInForce.DAY,
            )
            self._broker.place_orders(orders)
        except Exception as e:
            logger.debug("Entry failed for %s: %s", code, e)

    # ==================================================================
    # Helper methods
    # ==================================================================
    @property
    def _open_positions(self) -> list[Any]:
        return list(self._portfolio.open_positions_dict.values())

    def _get_position(self, ticker: Any) -> Any | None:
        for pos in self._open_positions:
            if hasattr(pos, "ticker") and pos.ticker() == ticker:
                return pos
        return None

    def _trailing_stop(self, code: str, pnl: float) -> float:
        for pnl_threshold, lock_ratio in reversed(TRAILING_STOP_TIERS):
            peak = self._peak_pnl.get(code, pnl)
            if peak >= pnl_threshold:
                if lock_ratio == 0.0:
                    return 0.0
                return peak * lock_ratio
        return 0.0

    def _regime_tp(self, default_tp: float) -> float:
        return REGIME_TP.get(self._regime, default_tp)

    def _current_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = max(self.equity_curve)
        current = self.equity_curve[-1]
        return (peak - current) / peak if peak > 0 else 0.0

    def _rps_bonus(self, code: str, bar_date: pd.Timestamp) -> float:
        """Compute RPS (Relative Price Strength) bonus."""
        kline = self._klines.get(code)
        if kline is None or len(kline) < 21:
            return 0.0
        try:
            if not isinstance(kline.index, pd.DatetimeIndex):
                kline.index = pd.to_datetime(kline.index)
            if bar_date not in kline.index:
                return 0.0
            idx = kline.index.get_loc(bar_date)
            if idx < 20:
                return 0.0
            close = pd.to_numeric(kline["close"].iloc[:idx + 1], errors="coerce")
            rocs: dict[int, float] = {}
            for period in [5, 10, 20]:
                if len(close) > period and close.iloc[-period - 1] > 0:
                    rocs[period] = float(
                        (close.iloc[-1] - close.iloc[-period - 1]) / close.iloc[-period - 1] * 100
                    )
            if not rocs:
                return 0.0
            # Simplified RPS: average of percentile-ranked ROCs
            avg_roc = float(np.mean(list(rocs.values())))
            if avg_roc > 5.0:
                return 2.0
            if avg_roc > 2.0:
                return 1.0
            if avg_roc < -5.0:
                return -2.0
            if avg_roc < -2.0:
                return -1.0
            return 0.0
        except Exception:
            return 0.0

    def _reversal_bonus(self, code: str, bar_date: pd.Timestamp) -> float:
        """Compute 5-day reversal bonus."""
        kline = self._klines.get(code)
        if kline is None or len(kline) < 6:
            return 0.0
        try:
            if not isinstance(kline.index, pd.DatetimeIndex):
                kline.index = pd.to_datetime(kline.index)
            if bar_date not in kline.index:
                return 0.0
            idx = kline.index.get_loc(bar_date)
            if idx < 5:
                return 0.0
            close_5d = pd.to_numeric(kline["close"].iloc[idx - 5: idx + 1], errors="coerce")
            if len(close_5d) >= 2 and close_5d.iloc[0] > 0:
                roc5 = (close_5d.iloc[-1] - close_5d.iloc[0]) / close_5d.iloc[0]
                if roc5 < _ROC5_DROP_THRESHOLD:
                    return 5.0
                if roc5 < _ROC5_MODERATE_DROP:
                    return 3.0
                if roc5 > _ROC5_RISE_THRESHOLD:
                    return -5.0
            return 0.0
        except Exception:
            return 0.0

    # ==================================================================
    # IC tracking
    # ==================================================================
    def _track_ic(self, now: datetime) -> None:
        """Measure Information Coefficient (Spearman rank correlation)."""
        if not self._prev_ml_scores:
            return
        date_str = str(now)[:10]
        try:
            from scipy.stats import spearmanr

            kline_date = pd.Timestamp(now.date())
            fwd_returns: dict[str, float] = {}
            for code, kline in self._klines.items():
                if code not in self._prev_ml_scores:
                    continue
                if not isinstance(kline.index, pd.DatetimeIndex):
                    kline.index = pd.to_datetime(kline.index)
                if kline_date not in kline.index:
                    continue
                idx = kline.index.get_loc(kline_date)
                if idx < 5:
                    continue
                fwd = float(kline["close"].iloc[idx]) / float(kline["close"].iloc[idx - 5]) - 1.0
                fwd_returns[code] = fwd

            common = set(self._prev_ml_scores) & set(fwd_returns)
            if len(common) >= 10:
                preds = [self._prev_ml_scores[c] for c in common]
                rets = [fwd_returns[c] for c in common]
                if np.std(preds) > 1e-10 and np.std(rets) > 1e-10:
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore", message=".*constant.*")
                        ic_val, _ = spearmanr(preds, rets)
                    if not np.isnan(ic_val):
                        self._ic_values.append(float(ic_val))
                        self._ic_dates.append(date_str)
        except Exception:
            pass

    # ==================================================================
    # Order execution helpers
    # ==================================================================
    def _execute_closes(
        self,
        to_close: list[tuple[Any, str]],
        now: datetime,
        date_str: str,
    ) -> None:
        for ticker, reason in to_close:
            self._execute_close(ticker, reason, now, date_str)

    def _execute_close(self, ticker: Any, reason: str, now: datetime, date_str: str) -> None:
        code = ticker.as_string
        try:
            orders = self._ts.order_factory.target_percent_orders(
                {ticker: 0.0},
                MarketOrder(),
                TimeInForce.DAY,
            )
            self._broker.place_orders(orders)
        except Exception as e:
            logger.debug("Close failed for %s: %s", code, e)

        # Record trade
        kline = self._klines.get(code)
        kline_date = pd.Timestamp(now.date())
        exit_price = 0.0
        if kline is not None:
            if not isinstance(kline.index, pd.DatetimeIndex):
                try:
                    kline.index = pd.to_datetime(kline.index)
                except Exception:
                    pass
            if kline_date in kline.index:
                exit_price = float(kline.loc[kline_date, "open"])
        pos = self._get_position(ticker)
        if pos is not None:
            entry_price = (
                pos._avg_price_per_unit
                if hasattr(pos, "_avg_price_per_unit") and pos._avg_price_per_unit > 0
                else pos.current_price
            )
            entry_date = pos.start_time if pos.start_time is not None else now
        else:
            entry_price = 0.0
            entry_date = now

        if entry_price > 0 and exit_price > 0:
            gross_ret = (exit_price - entry_price) / entry_price
            cost = self._commission_pct + self._slippage_pct + self._stamp_tax_pct
            net_ret = (gross_ret - cost) * 100
            elapsed = (now - entry_date).days if isinstance(entry_date, datetime) else 0
            self.trades.append(
                QFTradeRecord(
                    code=code,
                    name=self._names.get(code, code),
                    entry_date=str(entry_date)[:10],
                    exit_date=date_str,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    return_pct=round(net_ret, 2),
                    exit_reason=reason,
                    hold_days=elapsed,
                )
            )

        # Cleanup per-position tracking
        self._peak_pnl.pop(code, None)
        self._highest_price.pop(code, None)
        self._weak_streak.pop(code, None)
        self._pos_atr.pop(code, None)
        self._pos_entry_score.pop(code, None)
        self._recent_exits[code] = now
        if reason == "stop_loss":
            self._stop_loss_count[code] = self._stop_loss_count.get(code, 0) + 1
