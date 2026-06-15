"""Enhanced backtest engine — event-driven simulation with risk management.

Extracted from the original single-file module for modularity.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from aimoon.backtest import risk_controls
from aimoon.backtest.position import compute_position_weights
from aimoon.enhanced_backtest.models import (
    EnhancedPosition,
    EnhancedTrade,
)
from aimoon.enhanced_backtest.portfolio_runner import run_portfolio as _run_backtest
from aimoon.indicators.technical import TechInd
from aimoon.risk import RiskLimits
from aimoon.rumi_strategy import (
    KRangeExit,
    RumiSignal,
)

logger = logging.getLogger(__name__)

class EnhancedBacktestEngine:
    """Event-driven backtest with stop-loss, take-profit, position sizing."""

    def __init__(
        self,
        hold_days: int = 12,
        max_positions: int = 4,
        commission: float = 0.0003,
        slippage: float = 0.002,
        stamp_tax: float = 0.001,
        entry_threshold: int | float = 60,
        stop_loss_pct: float = 0.04,
        take_profit_pct: float = 0.15,
        stop_loss_atr_multiplier: float = risk_controls.STOP_LOSS_ATR_MULTIPLIER,
        take_profit_atr_multiplier: float = risk_controls.TAKE_PROFIT_ATR_MULTIPLIER,
        risk_limits: RiskLimits | None = None,
        rebalance_freq: int = 3,
        benchmark_code: str | None = None,
        max_sector_pct: float = 0.25,
        use_reversal: bool = False,
        use_alpha: bool = False,
        use_ml: bool = True,
        use_kelly: bool = True,
        ic_weights: dict[str, float] | None = None,
        backtest_start_date: str | None = None,
        exit_ratio: float = 0.50,  # 从0.65降低，让动量退出更宽松
        stop_loss_cooldown: int = 10,
        check_interval: int = 2,
    ) -> None:
        self.hold_days = hold_days
        self.max_positions = max_positions
        self.commission = commission
        self.slippage = slippage
        self.stamp_tax = stamp_tax
        self.entry_threshold = entry_threshold
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.stop_loss_atr_multiplier = stop_loss_atr_multiplier
        self.take_profit_atr_multiplier = take_profit_atr_multiplier
        self.risk_limits = risk_limits or RiskLimits()
        self.rebalance_freq = rebalance_freq
        self.benchmark_code = benchmark_code
        self.check_interval = check_interval
        self.max_sector_pct = max_sector_pct
        self.use_reversal = use_reversal
        self.use_alpha = use_alpha
        self.use_ml = use_ml
        self.use_kelly = use_kelly
        self.ic_weights = ic_weights
        self.backtest_start_date = backtest_start_date
        self.exit_threshold = int(entry_threshold * exit_ratio)
        self.stop_loss_cooldown = stop_loss_cooldown
        self.cache_dir: str | None = None

        # T1 合规：hold_days 至少为 1（买入次日才能卖出）
        if self.hold_days < 1:
            logger.warning("hold_days=%d < 1, 违反 T1 制度，自动调整为 1", self.hold_days)
            self.hold_days = 1

        # 初始化智能滑点模型
        from aimoon.ml.slippage_model import SlippageModel

        self.slippage_model = SlippageModel()

        # ML 集成模型（回测开始时初始化）
        self._ml_predictor: Any = None
        self._ml_panel: dict | None = None
        self._ml_registry: Any = None
        self._ml_score_cache: dict[str, dict[str, int]] = {}
        self._ml_model_hash: str | None = None  # 模型文件 hash，用于失效检测
        self._alpha_cached: bool = False
        self._alpha_cache: dict | None = None

        from aimoon.performance import PerformanceMonitor
        self._perf = PerformanceMonitor()
            # ICIR 动态权重 + 因子衰减

    def _init_ml_model(
        self,
        klines: dict[str, pd.DataFrame],
        panel: dict[str, pd.DataFrame] | None = None,
        registry: Any = None,
        factor_cache: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        """加载 ML 集成模型并自适应权重。

        Delegates to ml_integration.init_ml_model.
        """
        from aimoon.enhanced_backtest.ml_integration import init_ml_model as _init_ml_model_fn
        _init_ml_model_fn(
            self, klines, panel=panel, registry=registry,
            factor_cache=factor_cache, cache_dir=self.cache_dir,
        )


    def _buy_cost(
        self,
        price: float = 0.0,
        shares: int = 0,
        daily_volume: float = 0.0,
        volatility: float = 0.0,
    ) -> float:
        commission = self.commission
        if price > 0 and shares > 0 and daily_volume > 0:
            order_amount = price * shares
            slippage = self.slippage_model.calculate_slippage(
                order_amount=order_amount,
                daily_volume=daily_volume,
                volatility=volatility,
            )
        else:
            slippage = self.slippage
        return commission + slippage

    def _sell_cost(
        self,
        price: float = 0.0,
        shares: int = 0,
        daily_volume: float = 0.0,
        volatility: float = 0.0,
    ) -> float:
        commission = self.commission
        stamp_tax = self.stamp_tax
        if price > 0 and shares > 0 and daily_volume > 0:
            order_amount = price * shares
            slippage = self.slippage_model.calculate_slippage(
                order_amount=order_amount,
                daily_volume=daily_volume,
                volatility=volatility,
            )
        else:
            slippage = self.slippage
        return commission + stamp_tax + slippage

    def _empty_result(self):
        from aimoon.enhanced_backtest.metrics import empty_result as _empty_result_fn
        return _empty_result_fn()


    def _compute_alpha_signals(self, klines: dict[str, pd.DataFrame]) -> dict | None:
        """Pre-compute Alpha Zoo panel + ICIR weights + factor quality filtering.

        Delegates to ml_integration.compute_alpha_signals.
        """
        from aimoon.enhanced_backtest.ml_integration import (
            compute_alpha_signals as _compute_alpha_signals_fn,
        )
        return _compute_alpha_signals_fn(self, klines)

    def _score_stock(
        self,
        code: str,
        name: str,
        kline: pd.DataFrame,
        ctx: dict | None = None,
        alpha_signals: dict[str, list] | None = None,
        ic_weights: dict[str, float] | None = None,
        ml_scores: dict[str, int] | None = None,
        regime: str | None = None,
        _ti: TechInd | None = None,
    ) -> int | None:
        """组合技术指标 + alpha 信号 + ML 评分（与 screener.screen_stock 一致）。

        Delegates to ml_integration.score_stock.
        """
        from aimoon.enhanced_backtest.ml_integration import score_stock as _score_stock_fn
        return _score_stock_fn(
            self, code, name, kline, ctx=ctx, alpha_signals=alpha_signals,
            ic_weights=ic_weights, ml_scores=ml_scores, regime=regime, _ti=_ti,
        )

    def _get_ml_scores_for_date(self, target_date) -> dict[str, int] | None:
        """获取指定日期的 ML 集成预测百分位分数 (0-100)。

        Delegates to ml_integration.get_ml_scores_for_date.
        """
        from aimoon.enhanced_backtest.ml_integration import (
            get_ml_scores_for_date as _get_ml_scores_for_date_fn,
        )
        return _get_ml_scores_for_date_fn(self, target_date)

    def precompute_ml_scores(self, sorted_dates: list, klines: dict) -> None:
        """Batch-precompute ML scores for all trading dates in one pass.

        Populates _ml_score_cache so the per-bar loop only does dict lookups.
        """
        if not self.use_ml or self._ml_predictor is None:
            return
        total = len(sorted_dates)
        for i, date in enumerate(sorted_dates):
            if i % 20 == 0:
                logger.info("Precomputing ML scores: %d/%d", i, total)
            try:
                self._get_ml_scores_for_date(date)
            except Exception as e:
                logger.debug("ML precompute failed @ %s: %s", date, e)
        logger.info("ML score precomputation complete: %d dates", total)


    def _extract_features_cached(self, target_date) -> pd.DataFrame | None:
        """Extract features using the same pipeline as training.

        Delegates to ml_integration.extract_features_cached.
        """
        from aimoon.enhanced_backtest.ml_integration import extract_features_cached as _extract_fn
        return _extract_fn(self, target_date)

    def _get_fallback_ml_scores(self, target_date) -> dict[str, int] | None:
        """ML 模型不可用时的回退方案。

        Delegates to ml_integration.get_fallback_ml_scores.
        """
        from aimoon.enhanced_backtest.ml_integration import get_fallback_ml_scores as _get_fn
        return _get_fn(self, target_date)

    def _get_alpha_signals_for_date(self, alpha_ctx, target_date):
        """获取指定日期的 Alpha Zoo 截面信号（含 ICIR 权重和因子衰减）。

        Delegates to ml_integration.get_alpha_signals_for_date.
        """
        from aimoon.enhanced_backtest.ml_integration import get_alpha_signals_for_date as _get_fn
        return _get_fn(self, alpha_ctx, target_date)

    def _generate_rumi_signals(
        self,
        klines: dict[str, pd.DataFrame],
        names: dict[str, str],
        bar_date: pd.Timestamp,
    ) -> dict[str, RumiSignal]:
        """生成 Rumi 信号。

        Delegates to rumi_signals.generate_rumi_signals.
        """
        from aimoon.enhanced_backtest.rumi_signals import generate_rumi_signals as _generate_fn
        return _generate_fn(klines, names, bar_date)

    def _check_rumi_exit(
        self,
        code: str,
        position: EnhancedPosition,
        klines: dict[str, pd.DataFrame],
        bar_date: pd.Timestamp,
        rumi_score: float,
        regime: str,
    ) -> KRangeExit | None:
        """检查 Rumi/KRange 离场信号。

        Delegates to rumi_signals.check_rumi_exit.
        """
        from aimoon.enhanced_backtest.rumi_signals import check_rumi_exit as _check_fn
        return _check_fn(code, position, klines, bar_date, rumi_score, regime)

    def _phase0_execute_pending(
        self,
        bar_date: pd.Timestamp,
        positions: dict[str, EnhancedPosition],
        pending_entries: dict[str, dict],
        klines: dict[str, pd.DataFrame],
        effective_positions: int,
        cash: list[float],
        pending_expiry: dict[str, int] | None = None,
        max_pending_age: int = 5,
    ) -> None:
        """执行上一轮的待入场订单（T+1 开盘价）。

        Delegates to exit_rules.phase0_execute_pending.
        """
        from aimoon.enhanced_backtest.exit_rules import phase0_execute_pending as _phase0_fn
        _phase0_fn(
            bar_date, positions, pending_entries, klines, effective_positions, cash,
            engine=self, pending_expiry=pending_expiry, max_pending_age=max_pending_age,
        )

    def _phase1_stop_loss_take_profit(
        self,
        bar_date: pd.Timestamp,
        positions: dict[str, EnhancedPosition],
        klines: dict[str, pd.DataFrame],
        trades: list[EnhancedTrade],
        cash: list[float],
        current_regime: str,
        max_hold_bars: int,
        sector_ctx: dict[str, Any] | None,
        alpha_signals: dict[str, Any] | None,
        weak_streak: dict[str, int],
        recent_exits: dict[str, int],
        stop_loss_count: dict[str, int],
        bar_count: int,
        partial_taken_set: set[str] | None = None,
        prev_date: pd.Timestamp | None = None,
    ) -> list[tuple[str, float, str, int]]:
        """止损/盈/最大持仓期检查。

        Delegates to exit_rules.phase1_stop_loss_take_profit.
        """
        from aimoon.enhanced_backtest.exit_rules import phase1_stop_loss_take_profit as _phase1_fn
        return _phase1_fn(
            bar_date, positions, klines, trades, cash, current_regime, max_hold_bars,
            engine=self, sector_ctx=sector_ctx, alpha_signals=alpha_signals,
            weak_streak=weak_streak, recent_exits=recent_exits,
            stop_loss_count=stop_loss_count, bar_count=bar_count, partial_taken_set=partial_taken_set, prev_date=prev_date,
        )

    def _phase2_momentum_check(
        self,
        bar_date: pd.Timestamp,
        prev_date: pd.Timestamp | None,
        positions: dict[str, EnhancedPosition],
        klines: dict[str, pd.DataFrame],
        trades: list[EnhancedTrade],
        alpha_signals: dict[str, Any] | None,
        sector_ctx: dict[str, Any] | None,
        weak_streak: dict[str, int],
        recent_exits: dict[str, int],
        bar_count: int,
        cash: list[float],
    ) -> None:
        """动量检查（每 3 个 bar）。

        Delegates to exit_rules.phase2_momentum_check.
        """
        from aimoon.enhanced_backtest.exit_rules import phase2_momentum_check as _phase2_fn
        _phase2_fn(
            bar_date, prev_date, positions, klines, trades, engine=self,
            alpha_signals=alpha_signals, sector_ctx=sector_ctx,
            weak_streak=weak_streak, recent_exits=recent_exits,
            bar_count=bar_count, cash=cash,
        )

    def _phase4_open_replacements(
        self,
        bar_date: pd.Timestamp,
        prev_date: pd.Timestamp | None,
        positions: dict[str, EnhancedPosition],
        pending_entries: dict[str, dict],
        klines: dict[str, pd.DataFrame],
        trades: list[EnhancedTrade],
        names: dict[str, str],
        sector_map: dict[str, str],
        alpha_signals: dict[str, Any] | None,
        sector_ctx: dict[str, Any] | None,
        recent_exits: dict[str, int],
        stop_loss_count: dict[str, int],
        effective_positions: int,
        effective_threshold: int,
        current_regime: str,
        dd_scale: float,
        bar_count: int,
        rumi_signals: dict[str, object] | None = None,
    ) -> None:
        """开新仓补位。

        Delegates to entry_rules.phase4_open_replacements.
        """
        from aimoon.enhanced_backtest.entry_rules import phase4_open_replacements as _phase4_fn
        _phase4_fn(
            bar_date, prev_date, positions, pending_entries, klines, trades,
            names, sector_map, engine=self,
            alpha_signals=alpha_signals, sector_ctx=sector_ctx,
            recent_exits=recent_exits, stop_loss_count=stop_loss_count,
            effective_positions=effective_positions,
            effective_threshold=effective_threshold,
            current_regime=current_regime, dd_scale=dd_scale,
            bar_count=bar_count, rumi_signals=rumi_signals,
        )

    def run_portfolio(self, klines, names=None, sectors=None, ctx=None):
        """Momentum-driven portfolio: delegate to portfolio_runner."""
        return _run_backtest(self, klines, names=names, sectors=sectors, ctx=ctx)

    def _compute_metrics(
        self,
        trades,
        equity,
        dd_curve,
        benchmark_equity,
        ic_series=None,
        ic_dates=None,
    ):
        from aimoon.enhanced_backtest.metrics import compute_metrics as _compute_metrics_fn
        return _compute_metrics_fn(
            trades, equity, dd_curve, benchmark_equity,
            ic_series=ic_series, ic_dates=ic_dates,
        )

    def _compute_position_weights(
        self,
        trades: list,
        max_positions: int,
        klines: dict,
        scores: dict[str, float],
        regime: str = "sideways",
        drawdown_scale: float = 1.0,
    ) -> dict[str, float]:
        """Compute position weights — delegates to backtest.position module."""
        return compute_position_weights(
            trades=trades,
            max_positions=max_positions,
            klines=klines,
            scores=scores,
            use_kelly=self.use_kelly,
            regime=regime,
        )


# ── Standalone helpers (delegated to backtest.risk_controls) ──

    def run_parallel(
        self,
        klines: dict[str, "pd.DataFrame"],
        names: dict[str, str],
        n_workers: int | None = None,
        date_slices: list[tuple[str, str]] | None = None,
    ) -> "Any":
        """???????????????

        ????????????? worker ????????
        ???????

        Parameters
        ----------
        klines : dict
            K????
        names : dict
            ???????
        n_workers : int | None
            ????????? CPU ???
        date_slices : list[tuple[str, str]] | None
            ?????? [(start, end), ...]????????

        Returns
        -------
        BacktestResult
            ?????????
        """
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor, as_completed

        if n_workers is None:
            n_workers = min(multiprocessing.cpu_count(), 4)

        # ??????
        all_dates: set[str] = set()
        for kdf in klines.values():
            if hasattr(kdf, "index"):
                all_dates.update(str(d)[:10] for d in kdf.index)
        sorted_dates = sorted(all_dates)

        if not sorted_dates:
            return self.run_portfolio(klines, names)

        # ??????
        if date_slices is None:
            n_slices = n_workers
            slice_size = max(1, len(sorted_dates) // n_slices)
            date_slices = []
            for i in range(0, len(sorted_dates), slice_size):
                end_idx = min(i + slice_size, len(sorted_dates))
                date_slices.append((sorted_dates[i], sorted_dates[end_idx - 1]))

        if len(date_slices) <= 1:
            return self.run_portfolio(klines, names)

        # ??????? klines
        slice_data: list[tuple[str, str, dict[str, pd.DataFrame]]] = []
        for start, end in date_slices:
            slice_klines = {}
            for code, kdf in klines.items():
                mask = (kdf.index >= start) & (kdf.index <= end)
                filtered = kdf[mask]
                if not filtered.empty:
                    slice_klines[code] = filtered
            if slice_klines:
                slice_data.append((start, end, slice_klines))

        if not slice_data:
            return self.run_portfolio(klines, names)

        logger.info(
            "Parallel backtest: %d slices across %d workers",
            len(slice_data), n_workers,
        )

        # ?????????ProcessPoolExecutor ????? self?
        # ????????????????
        from aimoon.enhanced_backtest.models import BacktestResult
        combined_trades: list[Any] = []
        combined_equity: list[float] = []
        peak_equity = 0.0
        max_drawdown = 0.0
        total_return = 0.0

        for start, end, slice_klines in slice_data:
            try:
                result = self.run_portfolio(slice_klines, names)
                if hasattr(result, "trades") and result.trades:
                    combined_trades.extend(result.trades)
                if hasattr(result, "equity_curve") and result.equity_curve:
                    combined_equity.extend(result.equity_curve)
            except Exception as e:
                logger.warning("Slice %s-%s failed: %s", start, end, e)

        # ????
        if hasattr(self, "_last_result") and self._last_result is not None:
            return self._last_result

        # ??????
        return self.run_portfolio(klines, names)


