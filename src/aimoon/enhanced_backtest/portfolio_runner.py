"""Portfolio backtest main loop — extracted from EnhancedBacktestEngine.

Contains the orchestrator that drives the 4-phase per-bar loop.
"""

from __future__ import annotations

import logging
import warnings
from collections import deque
from typing import Any

import numpy as np
import pandas as pd

from aimoon.backtest import _detect_regime_safe, risk_controls
from aimoon.enhanced_backtest.models import EnhancedPosition, EnhancedTrade
from aimoon.rumi_strategy import RumiSignal

logger = logging.getLogger(__name__)

_RUMI_MIN_SCORE: float = 100.0


def run_portfolio(
    engine: Any,
    klines: dict[str, pd.DataFrame],
    names: dict[str, str] | None = None,
    sectors: Any = None,
    ctx: dict | None = None,
) -> Any:
    """Momentum-driven portfolio: rebalance when signals change, not on fixed schedule."""
    if not klines:
        return engine._empty_result()
    names = names or {c: c for c in klines}

    from aimoon.data.history import fix_kline_dates

    skipped_codes = []
    for code, kline in klines.items():
        fixed = fix_kline_dates(kline)
        if len(fixed) > 0 and isinstance(fixed.index[0], (int, np.integer)):
            fixed_ok = False
            try:
                if "date" in fixed.columns:
                    fixed["date"] = pd.to_datetime(fixed["date"])
                    fixed = fixed.set_index("date").sort_index()
                    fixed_ok = True
                elif "datetime" in fixed.columns:
                    fixed["datetime"] = pd.to_datetime(fixed["datetime"])
                    fixed = fixed.set_index("datetime").sort_index()
                    fixed_ok = True
                else:
                    for col in ["trade_date", "timestamp", "time"]:
                        if col in fixed.columns:
                            fixed[col] = pd.to_datetime(fixed[col])
                            fixed = fixed.set_index(col).sort_index()
                            fixed_ok = True
                            break
            except (ValueError, TypeError, KeyError):
                pass
            if not fixed_ok:
                logger.warning("Cannot fix integer index for %s, skipping", code)
                skipped_codes.append(code)
                continue
        klines[code] = fixed

    if skipped_codes:
        n_skipped = len(skipped_codes)
        logger.warning("Skipped %d stocks with invalid dates: %s", n_skipped, skipped_codes)

    all_dates = set()
    for code, df in klines.items():
        if len(df) > 0:
            if isinstance(df.index[0], (int, np.integer)):
                logger.error("Stock %s still has integer index after fix!", code)
            all_dates.update(df.index)

    date_types = set(type(d) for d in all_dates)
    if len(date_types) > 1:
        logger.error("Mixed date types in all_dates: %s", date_types)
        all_dates = {d for d in all_dates if isinstance(d, pd.Timestamp)}

    sorted_dates = sorted(all_dates)
    if len(sorted_dates) < 60 + engine.hold_days:
        return engine._empty_result()

    alpha_signals = (
        engine._compute_alpha_signals(klines)
        if engine.use_alpha and not engine._alpha_cached
        else engine._alpha_cache
    )
    engine._alpha_cached = True
    engine._alpha_cache = alpha_signals

    if engine.use_ml:
        cached = alpha_signals if isinstance(alpha_signals, dict) else None
        engine._init_ml_model(
            klines,
            panel=cached.get("panel") if cached else None,
            registry=cached.get("registry") if cached else None,
        )
    equity = [100.0]
    dd_curve = [0.0]
    trades: list[EnhancedTrade] = []
    positions: dict[str, EnhancedPosition] = {}
    weak_streak: dict[str, int] = {}
    benchmark_equity = [100.0]
    has_benchmark = engine.benchmark_code in klines
    benchmark_kline = klines.get(engine.benchmark_code) if has_benchmark else None
    prev_bench_price = None
    peak = 100.0
    sector_map = (ctx or {}).get("sector_map", {})
    recent_exits: dict[str, int] = {}
    stop_loss_count: dict[str, int] = {}
    pending_entries: dict[str, dict] = {}
    pending_expiry: dict[str, int] = {}
    bar_count = 0
    check_interval = engine.check_interval
    max_hold_bars = engine.hold_days * 2
    sector_ctx = {"sector_map": sector_map} if sector_map else None
    prev_date = None
    engine._bar_ti_cache = {}
    cash: list[float] = [100.0]
    _PENDING_MAX_AGE = 5
    engine._pos_cost_basis = {}
    vol_regime_cache: dict[pd.Timestamp, float] = {}
    ic_deque: deque[float] = deque(maxlen=20)
    ic_series: list[float] = []
    ic_dates: list[str] = []
    prev_ml_scores: dict[str, float] = {}

    # Compute volatility and volume regime filter
    if has_benchmark and benchmark_kline is not None:
        bench_close = benchmark_kline['close']
        bench_returns = bench_close.pct_change().dropna()
        bench_vol_20 = bench_returns.rolling(20).std() * (252 ** 0.5)
        bench_vol_20_arr = bench_vol_20.dropna()
        if len(bench_vol_20_arr) > 0:
            vol_p80 = bench_vol_20_arr.quantile(0.80)
            vol_p95 = bench_vol_20_arr.quantile(0.95)
            bench_volume = benchmark_kline['volume'] if 'volume' in benchmark_kline.columns else None
            vol_ma20 = bench_volume.rolling(20).mean() if bench_volume is not None else None

            for date in sorted_dates[60:]:
                ts = pd.Timestamp(date)
                if ts in bench_vol_20.index:
                    vol_val = float(bench_vol_20.loc[ts])
                    if vol_val > vol_p95:
                        vol_regime_cache[ts] = 0.0  # extreme vol: no new positions
                    elif vol_val > vol_p80:
                        vol_regime_cache[ts] = 0.5  # high vol: reduce positions
                    else:
                        vol_regime_cache[ts] = 1.0  # normal vol
                else:
                    vol_regime_cache[ts] = 1.0

                # Volume filter: if volume < 0.6x of 20-day average, reduce signal weight
                if vol_ma20 is not None and ts in vol_ma20.index and ts in bench_volume.index:
                    vol_ratio = float(bench_volume.loc[ts]) / float(vol_ma20.loc[ts]) if float(vol_ma20.loc[ts]) > 0 else 1.0
                    if vol_ratio < 0.6 and vol_regime_cache[ts] > 0.3:
                        vol_regime_cache[ts] = 0.3  # low volume: reduce signal weight

    import time as _time
    _loop_times = {"total": 0, "regime": 0, "rumi_gen": 0, "dd_risk": 0, "phase0": 0, "phase1": 0, "rumi_exit": 0, "phase2": 0, "equity": 0, "vol_filter": 0, "phase4": 0, "ic_track": 0, "ml_score": 0}
    _loop_start = _time.time()

    for bar_date in sorted_dates[60:]:
        bar_date = pd.Timestamp(bar_date)
        _iter_start = _time.time()

        if alpha_signals is not None:
            panel = alpha_signals.get("panel")
            if panel is not None and "close" in panel:
                close_max = panel["close"].index.max()
                if close_max is not None and close_max > pd.Timestamp(bar_date):
                    logger.debug(
                        "Panel extends beyond bar_date %s — consumers must slice",
                        bar_date.date(),
                    )

        if engine.backtest_start_date is not None and bar_date < pd.Timestamp(
            engine.backtest_start_date
        ):
            bar_count += 1
            continue

        effective_positions = engine.max_positions
        effective_threshold = engine.entry_threshold
        current_regime = "sideways"
        if benchmark_kline is not None:
            regime = _detect_regime_safe(benchmark_kline, bar_date)
            if regime is not None:
                current_regime = regime.state
                if hasattr(regime, "position_scale"):
                    effective_positions = max(1, int(engine.max_positions * regime.position_scale))
                else:
                    if regime.state == "bear":
                        effective_positions = max(2, engine.max_positions // 2)
                        effective_threshold = engine.entry_threshold + 7
                    elif regime.state == "high_volatility":
                        effective_positions = max(2, engine.max_positions // 2)
                        effective_threshold = engine.entry_threshold + 8
                    elif regime.state == "bull":
                        effective_threshold = max(50, engine.entry_threshold - 5)

        rumi_signals: dict[str, RumiSignal]
        _t = _time.time()
        if _RUMI_MIN_SCORE < 100.0:
            rumi_signals = engine._generate_rumi_signals(klines, names, bar_date)
        else:
            rumi_signals = {}
        _loop_times["rumi_gen"] += _time.time() - _t

        current_dd = dd_curve[-1] if dd_curve else 0.0
        dd_scale = 1.0
        for dd_threshold, scale in risk_controls.DD_THRESHOLDS:
            if current_dd > dd_threshold:
                dd_scale = scale
                break
        if dd_scale < 1.0:
            effective_positions = max(1, int(effective_positions * dd_scale))

        with engine._perf.timer("phase0_execute"):
            engine._phase0_execute_pending(
                bar_date=bar_date,
                positions=positions,
                pending_entries=pending_entries,
                klines=klines,
                effective_positions=effective_positions,
                cash=cash,
                pending_expiry=pending_expiry,
                max_pending_age=_PENDING_MAX_AGE,
            )

        with engine._perf.timer("phase1_stop_loss"):
            engine._phase1_stop_loss_take_profit(
                bar_date=bar_date,
                positions=positions,
                klines=klines,
                trades=trades,
                cash=cash,
                current_regime=current_regime,
                max_hold_bars=max_hold_bars,
                sector_ctx=sector_ctx,
                alpha_signals=alpha_signals,
                weak_streak=weak_streak,
                recent_exits=recent_exits,
                stop_loss_count=stop_loss_count,
                bar_count=bar_count,
                prev_date=prev_date,
                            )

        rumi_exits = []
        for code, pos in list(positions.items()):
            if code in rumi_signals:
                rumi_sig = rumi_signals[code]
                exit_signal = engine._check_rumi_exit(
                    code=code,
                    position=pos,
                    klines=klines,
                    bar_date=bar_date,
                    rumi_score=rumi_sig.rumi_score,
                    regime=current_regime,
                )
                if exit_signal:
                    rumi_exits.append(
                        (
                            code,
                            exit_signal.exit_price,
                            "rumi_krange_exit",
                            (pd.Timestamp(bar_date) - pos.entry_date).days,
                        )
                    )

        for code, exit_price, reason, hdays in rumi_exits:
            if code in positions:
                pos = positions.pop(code)
                weak_streak.pop(code, None)
                cost_rate = engine._buy_cost() + engine._sell_cost()
                cost_basis = engine._pos_cost_basis.pop(code, 0.0)
                sell_proceeds = cost_basis * exit_price / pos.entry_price if cost_basis > 0 else 0.0
                sell_cost = sell_proceeds * cost_rate
                net_ret = (exit_price / pos.entry_price - 1) - cost_rate
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
                cash += sell_proceeds - sell_cost
                recent_exits[code] = bar_count

        if bar_count % check_interval == 0 and positions:
            with engine._perf.timer("phase2_momentum"):
                engine._phase2_momentum_check(
                    bar_date=bar_date,
                    prev_date=prev_date,
                    positions=positions,
                    klines=klines,
                    trades=trades,
                    alpha_signals=alpha_signals,
                    sector_ctx=sector_ctx,
                    weak_streak=weak_streak,
                    recent_exits=recent_exits,
                    bar_count=bar_count,
                    cash=cash,
                )

        new_equity = cash[0]
        for code, pos in positions.items():
            df = klines.get(code)
            if df is None or bar_date not in df.index:
                continue
            current_price = (
                float(df.loc[bar_date, "open"])
                if "open" in df.columns
                else (
                    float(df.iloc[df.index.get_loc(bar_date) - 1]["close"])
                    if df.index.get_loc(bar_date) - 1 >= 0
                    else float(df.loc[bar_date, "close"])
                )
            )
            cost_basis = engine._pos_cost_basis.get(code, 0.0)
            new_equity += cost_basis * current_price / pos.entry_price

        if has_benchmark and benchmark_kline is not None and bar_date in benchmark_kline.index:
            bench_price_now = float(benchmark_kline.loc[bar_date, "close"])
            if prev_bench_price is not None:
                bench_ret = (bench_price_now - prev_bench_price) / prev_bench_price
                benchmark_equity.append(benchmark_equity[-1] * (1 + bench_ret))
            prev_bench_price = bench_price_now

        if new_equity <= 0:
            logger.warning(
                "Portfolio wiped out at bar %d (equity=%.4f), stopping", bar_count, new_equity
            )
            equity.append(0.0)
            dd_curve.append(1.0)
            break
        if new_equity < 10.0:
            logger.warning(
                "Portfolio equity critically low at bar %d (equity=%.4f), stopping",
                bar_count,
                new_equity,
            )
            break

        equity.append(new_equity)
        current_val = equity[-1]
        peak = max(peak, current_val)
        dd = (peak - current_val) / peak if peak > 0 else 0.0
        dd_curve.append(dd)

        # Signal filter: volatility and volume regime check
        vol_filter_scale = 1.0
        if bar_date in vol_regime_cache:
            vol_filter_scale = vol_regime_cache[bar_date]
        if vol_filter_scale < 0.5:
            bar_count += 1
            prev_date = bar_date
            continue

        if len(positions) < effective_positions and bar_count % check_interval == 0:
            _t = _time.time()
            with engine._perf.timer("phase4_replacements"):
                engine._phase4_open_replacements(
                    bar_date=bar_date,
                    prev_date=prev_date,
                    positions=positions,
                    pending_entries=pending_entries,
                    klines=klines,
                    trades=trades,
                    names=names,
                    sector_map=sector_map,
                    alpha_signals=alpha_signals,
                    sector_ctx=sector_ctx,
                    recent_exits=recent_exits,
                    stop_loss_count=stop_loss_count,
                    effective_positions=effective_positions,
                    effective_threshold=effective_threshold,
                    current_regime=current_regime,
                    dd_scale=dd_scale,
                    bar_count=bar_count,
                    rumi_signals=rumi_signals,
                )

        _loop_times["phase4"] += _time.time() - _t

        if prev_ml_scores and prev_date is not None:
            from aimoon.ml.label_engine import generate_reversal_labels
            rev_labels = generate_reversal_labels(klines, prev_date, forward_days=5, lookback_days=20)
            common_codes = set(prev_ml_scores) & set(rev_labels.index)
            if len(common_codes) >= 10:
                from scipy.stats import spearmanr

                preds = [prev_ml_scores[c] for c in common_codes]
                rets = [rev_labels[c] for c in common_codes]
                if np.std(preds) == 0 or np.std(rets) == 0:
                    pass
                else:
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore", message=".*constant.*")
                        ic_val, _ = spearmanr(preds, rets)
                    if not np.isnan(ic_val):
                        ic_deque.append(float(ic_val))
                        ic_series.append(float(ic_val))
                        ic_dates.append(
                            str(bar_date.date()) if hasattr(bar_date, "date") else str(bar_date)
                        )

        _t = _time.time()
        ml_sigs = engine._get_ml_scores_for_date(bar_date)
        if ml_sigs:
            prev_ml_scores = ml_sigs
        _loop_times["ml_score"] += _time.time() - _t
        prev_date = bar_date
        bar_count += 1
        _loop_times["total"] += _time.time() - _iter_start

    logger.info("Backtest performance:\n%s", engine._perf.summary())
    return engine._compute_metrics(
        trades, equity, dd_curve, benchmark_equity, ic_series=ic_series, ic_dates=ic_dates
    )
