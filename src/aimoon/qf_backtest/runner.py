"""QF-Lib backtest runner -- entry point for subprocess or direct calls.

Builds a minimal TradingSession without relying on the full
BacktestTradingSessionBuilder (which triggers the weasyprint
dependency chain).  Instead, constructs the session manually
from individual QF-Lib components.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import warnings
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from aimoon.qf_backtest.imports import QF_AVAILABLE
from aimoon.qf_backtest.metrics import compute_metrics
from aimoon.qf_backtest.models import QFBacktestConfig, QFBacktestResult

# Suppress known qf-lib / xarray warnings that we cannot fix upstream
warnings.filterwarnings(
    "ignore",
    message=".*subclass QFDataArray should explicitly define __slots__.*",
    category=FutureWarning,
)

if QF_AVAILABLE:
    from qf_lib.backtesting.broker.backtest_broker import BacktestBroker
    from qf_lib.backtesting.contract.contract_to_ticker_conversion.simulated_contract_ticker_mapper import (  # noqa: E501
        SimulatedContractTickerMapper,
    )
    from qf_lib.backtesting.events.event_manager import EventManager
    from qf_lib.backtesting.events.notifiers import Notifiers
    from qf_lib.backtesting.events.time_event.regular_time_event.market_close_event import (
        MarketCloseEvent,
    )
    from qf_lib.backtesting.events.time_event.regular_time_event.market_open_event import (
        MarketOpenEvent,
    )
    from qf_lib.backtesting.execution_handler.commission_models.fixed_commission_model import (
        FixedCommissionModel,
    )
    from qf_lib.backtesting.execution_handler.simulated_execution_handler import (
        SimulatedExecutionHandler,
    )
    from qf_lib.backtesting.execution_handler.slippage.price_based_slippage import (
        PriceBasedSlippage,
    )
    from qf_lib.backtesting.monitoring.abstract_monitor import AbstractMonitor
    from qf_lib.backtesting.monitoring.backtest_result import BacktestResult
    from qf_lib.backtesting.order.order_factory import OrderFactory
    from qf_lib.backtesting.order.order_rounder import OrderRounder
    from qf_lib.backtesting.portfolio.portfolio import Portfolio
    from qf_lib.backtesting.position_sizer.simple_position_sizer import SimplePositionSizer
    from qf_lib.backtesting.signals.backtest_signals_register import BacktestSignalsRegister
    from qf_lib.backtesting.trading_session.trading_session import TradingSession
    from qf_lib.common.enums.frequency import Frequency
    from qf_lib.common.utils.dateutils.relative_delta import RelativeDelta
    from qf_lib.common.utils.dateutils.timer import SettableTimer
    from qf_lib.settings import Settings

logger = logging.getLogger(__name__)


def is_qf_lib_available() -> bool:
    return QF_AVAILABLE


def precompute_ml_scores_by_date(
    klines: dict[str, pd.DataFrame],
    sorted_dates: list[pd.Timestamp],
    cache_dir: str | None = None,
    stock_scores: dict[str, int] | None = None,
) -> dict[str, dict[str, int]]:
    """Pre-compute ML ensemble percentile scores for each date.

    Returns dict[date_str][code] = 0-100 percentile score.
    Falls back to basic price momentum signals if ML model is not available.
    """
    ml_scores_by_date: dict[str, dict[str, int]] = {}
    _fp_logger = logging.getLogger("aimoon.ml.feature_pipeline")
    _fp_prev = _fp_logger.level

    try:
        from aimoon.factors.panel import build_panel
        from aimoon.factors.registry import get_default_registry
        from aimoon.ml.ensemble import EnsemblePredictor
        from aimoon.ml.feature_pipeline import extract_features

        panel = build_panel(klines)
        predictor = EnsemblePredictor.from_cache(cache_dir)
        logger.info(
            "ML predictor loaded: xgb=%s, lgbm=%s, en=%s",
            predictor.has_xgb, predictor.has_lgbm, getattr(predictor, "has_en", False),
        )
        # Suppress noisy feature_pipeline warnings during precompute (expected for small panels)
        _fp_logger.setLevel(logging.ERROR)
        if predictor.has_xgb or predictor.has_lgbm:
            zoo_factor_ids = getattr(predictor, "_zoo_factor_ids", None)
            fn = getattr(predictor, "_feature_names", None)
            # Skip Zoo factors if model wasn't trained with them
            reg = get_default_registry() if zoo_factor_ids else None

            for date in sorted_dates:
                try:
                    features = extract_features(
                        panel,
                        registry=reg,
                        target_date=date,
                        zoo_factor_ids=zoo_factor_ids,
                    )
                    if features is None or features.empty:
                        continue
                    if fn:
                        features = features.reindex(columns=fn, fill_value=0.0)

                    preds: dict[str, np.ndarray] = {}
                    if predictor._xgb is not None:
                        import xgboost as xgb

                        preds["xgb"] = predictor._xgb.predict(xgb.DMatrix(features))
                    if predictor._lgbm is not None:
                        preds["lgbm"] = predictor._lgbm.predict(features)
                    if predictor._en is not None and predictor._en_scaler is not None:
                        fn_en = predictor._feature_names
                        fe = features.reindex(columns=fn_en, fill_value=0.0) if fn_en else features
                        en_scaled = predictor._en_scaler.transform(fe.values)
                        preds["en"] = predictor._en.predict(en_scaled)

                    if not preds:
                        continue

                    # Use IC-based weights from EnsemblePredictor
                    active_weights: dict[str, float] = {}
                    for name in preds:
                        if name == "xgb":
                            active_weights[name] = getattr(predictor, "_xgb_weight", 0.0)
                        elif name == "lgbm":
                            active_weights[name] = getattr(predictor, "_lgbm_weight", 0.0)
                        elif name == "en":
                            active_weights[name] = getattr(predictor, "_en_weight", 0.0)
                    total_weight = sum(active_weights.values())
                    combined = np.zeros(len(features))
                    for name, w in active_weights.items():
                        if w > 0 and total_weight > 0:
                            combined += (w / total_weight) * preds[name]
                    if total_weight <= 0 or len(combined) < 5:
                        continue

                    ranked = pd.Series(combined, index=features.index).rank(pct=True)
                    scores = (ranked * 100).round().astype(int).to_dict()
                    date_str = str(date)[:10]
                    ml_scores_by_date[date_str] = scores
                except Exception:
                    logger.debug("ML score failed for %s", date, exc_info=True)
                    continue

            logger.info("Pre-computed ML scores for %d dates", len(ml_scores_by_date))
            _fp_logger.setLevel(_fp_prev)
            if ml_scores_by_date:
                return ml_scores_by_date
    except Exception as exc:
        logger.warning("ML predictor unavailable: %s", exc)
        _fp_logger.setLevel(_fp_prev)

    # Fallback: score stocks by recent momentum (return over past 20d)
    logger.info("Using momentum fallback for per-date scores")
    codes = [c for c in klines if c and not klines[c].empty]
    for date in sorted_dates:
        try:
            date_scores: dict[str, int] = {}
            for code in codes:
                df = klines[code]
                hist = df[df.index <= date]
                if len(hist) < 5:
                    date_scores[code] = 50
                    continue
                if len(hist) >= 2:
                    ret_20d = hist["close"].iloc[-1] / hist["close"].iloc[-min(20, len(hist))] - 1
                else:
                    ret_20d = 0.0
                screener_boost = stock_scores.get(code, 50) if stock_scores else 50
                momentum_score = max(0, min(100, int((ret_20d + 0.1) / 0.2 * 50 + 50)))
                blended = int(momentum_score * 0.4 + screener_boost * 0.6)
                date_scores[code] = max(30, min(100, blended))
            if len(date_scores) > 2:
                ml_scores_by_date[str(date)[:10]] = date_scores
        except Exception:
            continue
    logger.info("Fallback scores for %d dates", len(ml_scores_by_date))

    return ml_scores_by_date


# ---------------------------------------------------------------------------
# Minimal mock monitor that does not use PDF / weasyprint
# ---------------------------------------------------------------------------
class NullMonitor(AbstractMonitor if QF_AVAILABLE else object):  # type: ignore[misc]
    """Monitor that records nothing -- bypasses weasyprint dependency."""

    def real_time_update(self, timestamp: datetime) -> None:
        pass

    def end_of_day_update(self, timestamp: datetime) -> None:
        pass

    def end_of_trading_update(self, timestamp: datetime | None = None) -> None:
        pass

    def record_transaction(self, transaction: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Standalone setup and execution
# ---------------------------------------------------------------------------
def run_qf_backtest(
    klines: dict[str, pd.DataFrame],
    ml_scores_by_date: dict[str, dict[str, int]],
    names: dict[str, str] | None = None,
    benchmark_code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    config: QFBacktestConfig | None = None,
) -> QFBacktestResult:
    """Run a full QF-Lib backtest using aimoon data.

    Builds a minimal TradingSession manually to avoid the weasyprint
    dependency chain triggered by BacktestTradingSessionBuilder.

    Parameters
    ----------
    klines:
        Dict of {code: DataFrame} with OHLCV columns and datetime index.
    ml_scores_by_date:
        Dict of {date_str: {code: score}} -- ML percentile scores per day.
    names:
        Optional dict mapping code -> human-readable name.
    benchmark_code:
        Optional benchmark code for comparison.
    start_date, end_date:
        Date range for the backtest (YYYY-MM-DD).  Auto-detected if None.
    config:
        Backtest configuration.  Uses defaults if None.

    Returns
    -------
    QFBacktestResult with performance metrics and trades.
    """
    if not QF_AVAILABLE:
        raise ImportError("qf-lib is not installed.\n" "Install with: uv pip install qf-lib")

    cfg = config or QFBacktestConfig()
    names = names or {}

    # Determine date range from scores
    all_dates: list[str] = sorted(ml_scores_by_date.keys())
    if start_date is None:
        start_date = all_dates[0] if all_dates else "2025-01-01"
    if end_date is None:
        end_date = all_dates[-1] if all_dates else "2025-12-31"

    bt_start = datetime.strptime(start_date, "%Y-%m-%d")
    bt_end = datetime.strptime(end_date, "%Y-%m-%d")

    # Set trigger times for market events (must be before scheduler is accessed)
    MarketOpenEvent.set_trigger_time({"hour": 9, "minute": 30, "second": 0, "microsecond": 0})
    MarketCloseEvent.set_trigger_time({"hour": 15, "minute": 0, "second": 0, "microsecond": 0})

    OrderRounder.switch_off_rounding_for_backtest()

    # Build components in the same order as BacktestTradingSessionBuilder
    # Settings(None) logs a warning about empty/missing settings file — suppress it
    _qf_settings_logger = logging.getLogger("qf.Settings")
    _prev_level = _qf_settings_logger.level
    _qf_settings_logger.setLevel(logging.CRITICAL)
    settings = Settings(None)
    _qf_settings_logger.setLevel(_prev_level)

    # 1) Timer
    timer = SettableTimer(bt_start)

    # 2) Data provider
    from aimoon.qf_backtest.data_provider import AimoonDataProvider

    data_provider = AimoonDataProvider(klines)
    data_provider.set_timer(timer)
    data_provider.frequency = Frequency.DAILY

    # 3) Notifiers + EventManager
    notifiers = Notifiers(timer)
    event_manager = EventManager(timer)
    event_manager.register_notifiers(
        [
            notifiers.all_event_notifier,
            notifiers.empty_queue_event_notifier,
            notifiers.end_trading_event_notifier,
            notifiers.scheduler,
        ]
    )

    # 4) Signals register
    signals_register = BacktestSignalsRegister()

    # 5) Portfolio
    portfolio = Portfolio(data_provider, initial_cash=int(cfg.initial_cash))

    # 6) BacktestResult + Monitor (null monitor avoids weasyprint)
    backtest_result = BacktestResult(
        portfolio, signals_register, cfg.backtest_name, bt_start, bt_end
    )
    monitor = NullMonitor()

    # 7) Slippage + Commission
    slippage_model = PriceBasedSlippage(
        data_provider=data_provider,
        slippage_rate=cfg.slippage_pct,
    )
    commission_model = FixedCommissionModel(commission=0.0)

    # 8) Execution handler
    execution_handler = SimulatedExecutionHandler(
        data_provider,
        notifiers.scheduler,
        monitor,
        commission_model,
        portfolio,
        slippage_model,
        scheduling_time_delay=RelativeDelta(minutes=1),
        frequency=Frequency.DAILY,
    )

    # 9) Broker
    contract_ticker_mapper = SimulatedContractTickerMapper()
    broker = BacktestBroker(contract_ticker_mapper, portfolio, execution_handler)

    # 11) Order factory
    order_factory = OrderFactory(broker, data_provider)

    # 12) Position sizer
    position_sizer = SimplePositionSizer(
        broker,
        data_provider,
        order_factory,
        signals_register,
    )

    # 13) Assemble session
    session = TradingSession()
    session.data_provider = data_provider
    session.broker = broker
    session.contract_ticker_mapper = contract_ticker_mapper
    session.order_factory = order_factory
    session.position_sizer = position_sizer
    session.orders_filters = []
    session.event_manager = event_manager
    session.notifiers = notifiers
    session.frequency = Frequency.DAILY
    session.settings = settings
    session.monitor = monitor
    session.portfolio = portfolio
    session.backtest_result = backtest_result
    session.timer = timer

    # ── Precompute benchmark signals (volatility regime + market timing) ──
    benchmark_kline = klines.get(benchmark_code) if benchmark_code else None
    vol_regime_cache: dict[pd.Timestamp, float] = {}
    market_timing_cache: dict[pd.Timestamp, bool] = {}

    if benchmark_kline is not None and not benchmark_kline.empty:
        try:
            bench_close = benchmark_kline["close"]
            bench_returns = bench_close.pct_change().dropna()
            bench_vol_20 = bench_returns.rolling(20).std() * (252**0.5)
            bench_vol_20_clean = bench_vol_20.dropna()
            if len(bench_vol_20_clean) > 0:
                vol_p80 = bench_vol_20_clean.quantile(0.80)
                vol_p95 = bench_vol_20_clean.quantile(0.95)
                for date in bench_vol_20.index:
                    ts = pd.Timestamp(date)
                    if ts in bench_vol_20.index and not pd.isna(bench_vol_20.loc[ts]):
                        vol_val = float(bench_vol_20.loc[ts])
                        if vol_val > vol_p95:
                            vol_regime_cache[ts] = 0.0
                        elif vol_val > vol_p80:
                            vol_regime_cache[ts] = 0.5
                        else:
                            vol_regime_cache[ts] = 1.0

            # Market timing: MA20 + MACD > 0
            if len(benchmark_kline) >= 50:
                close_m = benchmark_kline["close"]
                ma20_m = close_m.rolling(20).mean()
                ema12 = close_m.ewm(span=12, adjust=False).mean()
                ema26 = close_m.ewm(span=26, adjust=False).mean()
                macd_line = ema12 - ema26
                signal_line = macd_line.ewm(span=9, adjust=False).mean()
                macd_hist = macd_line - signal_line
                for date in benchmark_kline.index:
                    ts = pd.Timestamp(date)
                    if ts in ma20_m.index and ts in macd_hist.index:
                        if not pd.isna(ma20_m.loc[ts]) and not pd.isna(macd_hist.loc[ts]):
                            ok = (
                                float(close_m.loc[ts]) >= float(ma20_m.loc[ts])
                                and float(macd_hist.loc[ts]) > 0
                            )
                            market_timing_cache[ts] = ok
        except Exception:
            logger.debug("Benchmark signal precompute failed", exc_info=True)

    # Build ticker list
    from aimoon.qf_backtest.data_provider import SimpleTicker

    tickers = [SimpleTicker(code) for code in klines if code != benchmark_code]

    # Create and register strategy
    from aimoon.qf_backtest.strategy import AimoonStrategy

    strategy = AimoonStrategy(
        ts=session,
        tickers=tickers,
        ml_scores_by_date=ml_scores_by_date,
        klines=klines,
        stop_loss_pct=cfg.stop_loss_pct,
        take_profit_pct=cfg.take_profit_pct,
        entry_threshold=cfg.entry_threshold,
        max_positions=cfg.max_positions,
        regime=cfg.regime,
        commission_pct=cfg.commission_pct,
        slippage_pct=cfg.slippage_pct,
        stamp_tax_pct=cfg.stamp_tax_pct,
        # ── Enhanced params ──
        benchmark_kline=benchmark_kline,
        vol_regime_cache=vol_regime_cache,
        market_timing_cache=market_timing_cache,
        names=names,
    )

    # Register the strategy's order event with the scheduler
    from qf_lib.backtesting.events.time_event.regular_time_event.calculate_and_place_orders_event import (  # noqa: E501
        CalculateAndPlaceOrdersRegularEvent,
    )

    CalculateAndPlaceOrdersRegularEvent.set_daily_default_trigger_time()
    CalculateAndPlaceOrdersRegularEvent.exclude_weekends()
    strategy.subscribe(CalculateAndPlaceOrdersRegularEvent)

    # ── TimeFlowController: advances timer + publishes EndTradingEvent ──
    from qf_lib.backtesting.events.time_flow_controller import BacktestTimeFlowController

    time_flow = BacktestTimeFlowController(
        scheduler=notifiers.scheduler,
        event_manager=event_manager,
        settable_timer=timer,
        empty_queue_event_notifier=notifiers.empty_queue_event_notifier,
        backtest_end_date=bt_end,
    )
    _ = time_flow  # kept alive by event_manager subscription

    # Patch strategy to print bar info + debug
    orig_capo = strategy.calculate_and_place_orders

    def _debug_capo(self2):
        now2 = self2._ts.timer.now()
        if self2._bar_count <= 3 or self2._bar_count % 50 == 0:
            print(f"  QFBAR#{self2._bar_count} {str(now2)[:10]} pos={len(self2._open_positions)}", flush=True)
        try:
            orig_capo()
        except Exception as e:
            print(f"  QFBAR#{self2._bar_count} ERROR: {e}", flush=True)

    strategy.calculate_and_place_orders = _debug_capo.__get__(strategy, type(strategy))

    # Run
    logger.info("Starting QF-Lib backtest: %s -> %s", start_date, end_date)
    try:
        session.start_trading()
    except Exception as e:
        import traceback

        logger.error("QF-Lib backtest failed: %s\n%s", e, traceback.format_exc())
        return QFBacktestResult()

    # Collect results
    result = compute_metrics(
        trades=strategy.trades,
        equity_curve=strategy.equity_curve,
        initial_cash=cfg.initial_cash,
    )

    # ── IC tracking from strategy ──
    if strategy._ic_values:
        ic_arr = np.array(strategy._ic_values)
        result.ic_mean = float(np.mean(ic_arr))
        result.ic_std = float(np.std(ic_arr))
        result.ic_positive_pct = float(np.sum(ic_arr > 0) / len(ic_arr) * 100)
        result.ic_values = strategy._ic_values
        result.ic_dates = strategy._ic_dates

    logger.info(
        "QF-Lib backtest complete: return=%.2f%%, sharpe=%.2f, trades=%d, ic=%.4f",
        result.total_return_pct,
        result.sharpe_ratio,
        result.trade_count,
        result.ic_mean,
    )

    return result


# ---------------------------------------------------------------------------
# Standalone CLI entry point (for subprocess execution)
# ---------------------------------------------------------------------------
def main_cli() -> None:
    """CLI entry point for standalone QF-Lib backtest execution.

    Reads data from a directory (exported by aimoon's main CLI)
    and writes results to a JSON file.

    Usage:
        python -m aimoon.qf_backtest.runner --data-dir <path> --output <path.json>
    """
    import argparse

    parser = argparse.ArgumentParser(description="QF-Lib backtest runner")
    parser.add_argument("--data-dir", required=True, help="Directory with backtest data")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if not QF_AVAILABLE:
        logger.error("qf-lib is not installed -- cannot run backtest")
        sys.exit(1)

    # Load data
    data_dir = args.data_dir

    # Load config
    config_path = os.path.join(data_dir, "config.json")
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            config_data = json.load(f)
        cfg = QFBacktestConfig(**config_data)
    else:
        cfg = QFBacktestConfig()

    # Load klines
    klines_dir = os.path.join(data_dir, "klines")
    klines: dict[str, pd.DataFrame] = {}
    if os.path.isdir(klines_dir):
        for fname in os.listdir(klines_dir):
            if fname.endswith(".parquet"):
                code = fname.replace(".parquet", "")
                df = pd.read_parquet(os.path.join(klines_dir, fname))
                if not df.empty:
                    klines[code] = df

    # Load ML scores
    scores_path = os.path.join(data_dir, "ml_scores.json")
    if os.path.exists(scores_path):
        with open(scores_path, encoding="utf-8") as f:
            ml_scores_by_date = json.load(f)
    else:
        ml_scores_by_date = {}

    # Load names
    names_path = os.path.join(data_dir, "names.json")
    names: dict[str, str] = {}
    if os.path.exists(names_path):
        with open(names_path, encoding="utf-8") as f:
            names = json.load(f)

    # Run
    result = run_qf_backtest(
        klines=klines,
        ml_scores_by_date=ml_scores_by_date,
        names=names,
        benchmark_code=cfg.benchmark_code,
        start_date=cfg.start_date or None,
        end_date=cfg.end_date or None,
        config=cfg,
    )

    # Write output
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)

    logger.info("Results written to %s", args.output)


if __name__ == "__main__":
    main_cli()
