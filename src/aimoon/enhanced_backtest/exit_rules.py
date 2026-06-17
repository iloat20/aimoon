"""Exit rule phases extracted from EnhancedBacktestEngine.

Phase 1: stop-loss / take-profit / max hold (every bar)
Phase 2: momentum check (every 3 bars)
Phase 3: daily mark-to-market
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from aimoon.enhanced_backtest import risk_controls
from aimoon.enhanced_backtest.helpers import (
    TIME_DECAY_IDLE_DAYS_THRESHOLD as _TIME_DECAY_IDLE_DAYS_THRESHOLD,
)
from aimoon.enhanced_backtest.helpers import (
    TIME_DECAY_IDLE_PNL as _TIME_DECAY_IDLE_PNL,
)
from aimoon.enhanced_backtest.helpers import (
    _get_atr_value,
)
from aimoon.enhanced_backtest.helpers import (
    compute_atr_entry_stop_loss as _compute_atr_entry_stop_loss,
)
from aimoon.enhanced_backtest.helpers import (
    regime_take_profit as _regime_take_profit,
)
from aimoon.enhanced_backtest.models import EnhancedPosition, EnhancedTrade
from aimoon.enhanced_backtest.risk_controls import (
    PARTIAL_PROFIT_SECONDARY_PNL,
    PARTIAL_PROFIT_TAKE_PNL,
)

logger = logging.getLogger(__name__)


def phase0_execute_pending(
    bar_date: pd.Timestamp,
    positions: dict[str, EnhancedPosition],
    pending_entries: dict[str, dict],
    klines: dict[str, pd.DataFrame],
    effective_positions: int,
    cash: list[float],
    engine: Any,
    pending_expiry: dict[str, int] | None = None,
    max_pending_age: int = 5,
) -> None:
    """Execute pending entry orders from the current bar (T open).

    Timeline:
      bar_date = T (current trading day)
      - Signal was generated at T-1 close → pending_entry was created
      - At T open, we execute: can_buy_at_open checks if T open is limit-up
      - entry_price = T open price

    No look-ahead: T open is knowable at T open.
    """
    from aimoon.data.limit_utils import can_buy_at_open

    for code, pending in list(pending_entries.items()):
        if code not in klines:
            pending_entries.pop(code, None)
            if pending_expiry is not None:
                pending_expiry.pop(code, None)
            continue
        if pending_expiry is not None:
            age = pending_expiry.get(code, 0)
            if age >= max_pending_age:
                logger.debug("Pending entry expired after %d bars: %s", age, code)
                pending_entries.pop(code, None)
                pending_expiry.pop(code, None)
                continue
        df = klines[code]
        if bar_date not in df.index:
            if pending_expiry is not None:
                pending_expiry[code] = pending_expiry.get(code, 0) + 1
            continue

        if not can_buy_at_open(df, bar_date):
            pending_entries.pop(code, None)
            if pending_expiry is not None:
                pending_expiry.pop(code, None)
            continue
        entry_price = (
            float(df.loc[bar_date, "open"])
            if "open" in df.columns
            else float(df.loc[bar_date, "close"])
        )

        entry_loc = df.index.get_loc(bar_date)
        entry_window = df.iloc[:entry_loc]
        dynamic_sl = _compute_atr_entry_stop_loss(
            entry_window, engine.stop_loss_atr_multiplier, engine.stop_loss_pct
        )
        weight = pending.get("weight", 1.0 / effective_positions)
        buy_cost = weight * cash[0]
        cash[0] -= buy_cost
        engine._pos_cost_basis[code] = buy_cost

        positions[code] = EnhancedPosition(
            name=pending.get("name", code),
            entry_price=entry_price,
            entry_date=bar_date,
            weight=weight,
            sector=pending.get("sector", ""),
            stop_loss=dynamic_sl,
            entry_score=pending.get("score", 0),
            peak_pnl=0.0,
            highest_price=entry_price,
            atr_at_entry=_get_atr_value(entry_window),
        )
        pending_entries.pop(code, None)
        if pending_expiry is not None and code in pending_expiry:
            del pending_expiry[code]


def phase1_stop_loss_take_profit(
    bar_date: pd.Timestamp,
    positions: dict[str, EnhancedPosition],
    klines: dict[str, pd.DataFrame],
    trades: list[EnhancedTrade],
    cash: list[float],
    current_regime: str,
    max_hold_bars: int,
    engine: Any,
    sector_ctx: dict[str, Any] | None = None,
    alpha_signals: dict[str, Any] | None = None,
    weak_streak: dict[str, int] | None = None,
    recent_exits: dict[str, int] | None = None,
    stop_loss_count: dict[str, int] | None = None,
    bar_count: int = 0,
    partial_taken_set: set[str] | None = None,
    prev_date: pd.Timestamp | None = None,
) -> list[tuple[str, float, str, int]]:
    """Stop-loss / take-profit / max-hold-period check.

    Timeline:
      bar_date = T (current trading day)
      - At T open, we see T's open price → can_sell_at_open checks if T open is limit-down
      - current_price = T open price → stop-loss check at T open
      - Entry happened at a prior date → holding period spans past bars

    No look-ahead: all prices used (T open, prior close) are knowable at T open.
    """
    from aimoon.data.limit_utils import can_sell_at_open

    weak_streak = weak_streak or {}
    recent_exits = recent_exits or {}
    stop_loss_count = stop_loss_count or {}
    to_close: list[tuple[str, float, str, int]] = []

    for code, pos in list(positions.items()):
        if code not in klines:
            continue
        df = klines[code]
        effective_sl = pos.stop_loss if pos.stop_loss > 0 else engine.stop_loss_pct
        if bar_date not in df.index:
            last_price = float(df["close"].iloc[-1])
            entry_date = (
                pos.entry_date
                if isinstance(pos.entry_date, pd.Timestamp)
                else pd.Timestamp(pos.entry_date)
            )
            elapsed = (pd.Timestamp(bar_date) - entry_date).days
            to_close.append((code, last_price, "data_gap", elapsed))
            continue

        if not can_sell_at_open(df, bar_date):
            continue

        current_price = (
            float(df.loc[bar_date, "open"]) if "open" in df.columns else _prev_close(df, bar_date)
        )

        pnl = (current_price - pos.entry_price) / pos.entry_price
        entry_date = (
            pos.entry_date
            if isinstance(pos.entry_date, pd.Timestamp)
            else pd.Timestamp(pos.entry_date)
        )

        try:
            entry_loc = df.index.get_loc(entry_date)
            current_loc = df.index.get_loc(bar_date)
            elapsed_days = current_loc - entry_loc
        except (KeyError, TypeError):
            elapsed_days = (pd.Timestamp(bar_date) - entry_date).days

        if elapsed_days < 1:
            continue

        pos = pos.with_update(peak_pnl=max(pos.peak_pnl, pnl) if pos.peak_pnl != 0.0 else pnl)
        pos = pos.with_update(
            highest_price=(
                max(pos.highest_price, current_price) if pos.highest_price != 0.0 else current_price
            )
        )
        positions[code] = pos

        for pnl_threshold, lock_ratio in risk_controls.TRAILING_STOP_TIERS:
            if pnl >= pnl_threshold:
                if lock_ratio == 0.0:
                    effective_sl = 0.0
                else:
                    pos_peak = pos.peak_pnl if pos.peak_pnl > 0 else pnl
                    effective_sl = max(effective_sl, pos_peak * lock_ratio)

        atr_val = pos.atr_at_entry if pos.atr_at_entry > 0 else 0
        if atr_val > 0:
            # Adaptive ATR: widen stops in high-vol regime
            atr_mult = risk_controls.CHANDELIER_ATR_MULTIPLIER
            if hasattr(engine, "use_adaptive_atr") and engine.use_adaptive_atr:
                from aimoon.risk import adaptive_atr_multiplier

                # Use regime-based ATR multiplier
                if current_regime in ("high_volatility", "crisis"):
                    atr_mult = adaptive_atr_multiplier(atr_mult, 0.5, 0.3)
                elif current_regime == "bull":
                    atr_mult = adaptive_atr_multiplier(atr_mult, 0.2, 0.3)
            highest = pos.highest_price if pos.highest_price > 0 else current_price
            chandelier_stop = (highest - atr_mult * atr_val) / pos.entry_price - 1
            effective_sl = max(effective_sl, chandelier_stop)

        if pnl <= -risk_controls.HARD_LOSS_CAP:
            to_close.append((code, current_price, "stop_loss", elapsed_days))
            continue
        elif (
            pnl > 0
            and pos.peak_pnl >= risk_controls.PROFIT_PROTECTION_PEAK_THRESHOLD
            and pnl <= risk_controls.PROFIT_PROTECTION_FLOOR
        ):
            to_close.append((code, current_price, "profit_protection", elapsed_days))
            continue
        elif pnl <= -effective_sl:
            to_close.append((code, current_price, "stop_loss", elapsed_days))
            continue
        elif pos.atr_at_entry > 0:
            entry_atr_pct = pos.atr_at_entry / pos.entry_price * 100.0
            atr_tp = risk_controls.compute_atr_take_profit(
                entry_atr_pct, engine.take_profit_atr_multiplier
            )
            atr_tp_regime = _regime_take_profit(current_regime, atr_tp)
            fixed_tp = _regime_take_profit(current_regime, engine.take_profit_pct)
            tp_threshold = min(atr_tp_regime, fixed_tp)
            if pnl >= tp_threshold:
                to_close.append((code, current_price, "take_profit", elapsed_days))
                continue
        elif pnl >= _regime_take_profit(current_regime, engine.take_profit_pct):
            to_close.append((code, current_price, "take_profit", elapsed_days))
            continue
        elif pnl >= PARTIAL_PROFIT_TAKE_PNL and not getattr(pos, "_partial_taken", False):
            to_close.append((code, current_price, "partial_profit_50", elapsed_days))
            continue
        elif pnl >= PARTIAL_PROFIT_SECONDARY_PNL and getattr(pos, "_partial_taken", False):
            to_close.append((code, current_price, "partial_profit_100", elapsed_days))
            continue
        # ── 时间止损：持仓超过5个交易日且收益为负 → 强制平仓（避免长期扛单） ──
        if elapsed_days >= 5 and pnl < 0:
            to_close.append((code, current_price, "time_stop_negative", elapsed_days))
            continue
        elif elapsed_days > _TIME_DECAY_IDLE_DAYS_THRESHOLD and 0 < pnl < _TIME_DECAY_IDLE_PNL:
            to_close.append((code, current_price, "time_decay", elapsed_days))
        elif elapsed_days > 10 and pnl < 0 and pnl <= -effective_sl * 0.8:
            to_close.append((code, current_price, "stop_loss", elapsed_days))
        elif elapsed_days > 10 and pnl < 0:
            # Time-stop tightening: if holding 10+ days still losing and score declining
            entry_score = pos.entry_score if pos.entry_score > 0 else 50
            window = df.iloc[: df.index.get_loc(bar_date) + 1]
            ml_sigs = engine._get_ml_scores_for_date(prev_date) if prev_date else None
            current_score = engine._score_stock(
                code, pos.name, window, alpha_signals=alpha_signals, ml_scores=ml_sigs
            )
            if current_score is not None and current_score < entry_score * 0.7:
                to_close.append((code, current_price, "time_stop_score_decline", elapsed_days))
        elif elapsed_days >= max_hold_bars:
            entry_score = pos.entry_score if pos.entry_score > 0 else 50
            window = df.iloc[: df.index.get_loc(bar_date) + 1]
            ml_sigs = engine._get_ml_scores_for_date(prev_date) if prev_date else None
            current_score = engine._score_stock(
                code,
                pos.name,
                window,
                ctx=sector_ctx,
                alpha_signals=alpha_signals,
                ml_scores=ml_sigs,
            )
            if (
                current_score is not None
                and current_score >= entry_score * 0.8
                and pnl > 0
                and elapsed_days < max_hold_bars * 2.0
            ):
                continue
            else:
                to_close.append((code, current_price, "hold_period", elapsed_days))

    for code, exit_price, reason, hdays in to_close:
        if code not in positions:
            continue
        pos = positions.pop(code)
        weak_streak.pop(code, None)
        cost_rate = engine._buy_cost() + engine._sell_cost()
        gross_ret = (exit_price - pos.entry_price) / pos.entry_price
        net_ret = gross_ret - cost_rate
        trades.append(
            EnhancedTrade(
                code=code,
                name=pos.name,
                entry_date=str(pos.entry_date),
                exit_date=str(bar_date),
                entry_price=pos.entry_price,
                exit_price=exit_price,
                return_pct=net_ret * 100,
                cost_pct=cost_rate * 100,
                exit_reason=reason,
                hold_days=hdays,
            )
        )
        cost_basis = engine._pos_cost_basis.pop(code, 0.0)
        sell_proceeds = cost_basis * exit_price / pos.entry_price
        sell_cost = cost_basis * cost_rate
        cash[0] += sell_proceeds - sell_cost
        recent_exits[code] = bar_count
        if reason == "stop_loss":
            stop_loss_count[code] = stop_loss_count.get(code, 0) + 1

    return to_close


def phase2_momentum_check(
    bar_date: pd.Timestamp,
    prev_date: pd.Timestamp | None,
    positions: dict[str, EnhancedPosition],
    klines: dict[str, pd.DataFrame],
    trades: list[EnhancedTrade],
    engine: Any,
    alpha_signals: dict[str, Any] | None = None,
    sector_ctx: dict[str, Any] | None = None,
    weak_streak: dict[str, int] | None = None,
    recent_exits: dict[str, int] | None = None,
    bar_count: int = 0,
    partial_taken_set: set[str] | None = None,
    cash: list[float] | None = None,
) -> None:
    """Momentum check every 3 bars. Exits weak-signal positions."""
    weak_streak = weak_streak or {}
    recent_exits = recent_exits or {}
    cash = cash or [100.0]
    alpha_query_date = prev_date if prev_date is not None else bar_date
    alpha_sigs = (
        engine._get_alpha_signals_for_date(alpha_signals, alpha_query_date)
        if alpha_signals
        else None
    )
    weak_codes: list[str] = []
    for code, pos in list(positions.items()):
        if code not in klines:
            continue
        df = klines[code]
        if bar_date not in df.index:
            continue
        idx = df.index.get_loc(bar_date)
        if idx < 60:
            continue
        window = df.iloc[:idx]
        ml_sigs = engine._get_ml_scores_for_date(alpha_query_date)
        score = engine._score_stock(
            code,
            pos.name,
            window,
            ctx=sector_ctx,
            alpha_signals=alpha_sigs,
            ml_scores=ml_sigs,
            _ti=engine._bar_ti_cache.get(code),
        )
        if score is not None and score < engine.exit_threshold:
            weak_streak[code] = weak_streak.get(code, 0) + 1
            if weak_streak[code] >= 2:
                weak_codes.append(code)
        else:
            weak_streak.pop(code, None)

    for code in weak_codes:
        if code not in positions:
            continue
        if code not in klines or bar_date not in klines[code].index:
            continue
        weak_streak.pop(code, None)
        pos = positions.pop(code)
        exit_price = (
            float(klines[code].loc[bar_date, "open"])
            if "open" in klines[code].columns
            else float(klines[code].loc[bar_date, "close"])
        )
        cost_rate = engine._buy_cost() + engine._sell_cost()
        gross_ret = (exit_price - pos.entry_price) / pos.entry_price
        net_ret = gross_ret - cost_rate
        entry_date = (
            pos.entry_date
            if isinstance(pos.entry_date, pd.Timestamp)
            else pd.Timestamp(pos.entry_date)
        )
        elapsed = (pd.Timestamp(bar_date) - entry_date).days
        trades.append(
            EnhancedTrade(
                code=code,
                name=pos.name,
                entry_date=str(pos.entry_date),
                exit_date=str(bar_date),
                entry_price=pos.entry_price,
                exit_price=exit_price,
                return_pct=net_ret * 100,
                cost_pct=cost_rate * 100,
                exit_reason="momentum_exit",
                hold_days=elapsed,
            )
        )
        cost_basis = engine._pos_cost_basis.pop(code, 0.0)
        sell_proceeds = cost_basis * exit_price / pos.entry_price
        sell_cost = cost_basis * cost_rate
        cash[0] += sell_proceeds - sell_cost
        recent_exits[code] = bar_count


def _prev_close(df: pd.DataFrame, bar_date: pd.Timestamp) -> float:
    prev_idx = df.index.get_loc(bar_date) - 1
    if prev_idx >= 0:
        return float(df.iloc[prev_idx]["close"])
    return float(df.loc[bar_date, "close"])
