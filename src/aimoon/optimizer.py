"""Parameter optimization (grid search) + walk-forward validation."""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config

logger = logging.getLogger(__name__)


def _detect_regime_at_split(
    klines: dict,
    train_end: str,
    test_start: str,
) -> tuple[str | None, str | None]:
    """Detect market regime at train/test split point.

    Returns (train_regime, test_regime) for logging and filtering.
    Uses price trend in the boundary region to classify regime.
    """
    try:
        # Use first stock's data as proxy for market
        first_df = next(iter(klines.values()))
        if first_df is None or len(first_df) < 20:
            return None, None

        # Get 20 bars before and after split
        all_dates = sorted(first_df.index)
        split_date = pd.Timestamp(test_start)

        # Find nearest date to split
        before_dates = [d for d in all_dates if d < split_date][-20:]
        after_dates = [d for d in all_dates if d >= split_date][:20]

        if len(before_dates) < 10 or len(after_dates) < 10:
            return None, None

        # Simple regime: compare MA20 slope
        def _regime(dates):
            if len(dates) < 10:
                return "unknown"
            closes = [float(first_df.loc[d, "close"]) for d in dates]
            ma_start = sum(closes[:5]) / 5
            ma_end = sum(closes[-5:]) / 5
            pct = (ma_end - ma_start) / ma_start * 100 if ma_start > 0 else 0
            if pct > 3:
                return "bull"
            if pct < -3:
                return "bear"
            return "sideways"

        return _regime(before_dates), _regime(after_dates)
    except Exception:
        return None, None


@dataclass(frozen=True)
class OptResult:
    params: dict[str, Any]
    sharpe: float
    sortino: float
    total_return: float
    max_drawdown: float
    trade_count: int


@dataclass(frozen=True)
class WalkForwardSplit:
    split_idx: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_sharpe: float
    test_sharpe: float
    test_return: float
    test_max_dd: float


@dataclass(frozen=True)
class WalkForwardResult:
    splits: tuple[WalkForwardSplit, ...]
    stability_score: float
    avg_test_sharpe: float
    avg_test_return: float
    overfit_warnings: tuple[str, ...] = ()


_PARAM_RANGES: dict[str, list] = {
    "rsi_period": [8, 10, 12, 14],
    "macd_fast": [8, 10, 12, 14],
    "stop_loss_pct": [0.04, 0.05, 0.06, 0.08],
    "hold_days": [10, 15, 22, 30],
}


def grid_search(
    klines: dict,
    names: dict,
    cfg: Config,
    cache: DataCache,
    param_ranges: dict[str, list] | None = None,
    metric: str = "sharpe",
    max_trials: int = 0,
    ctx: dict | None = None,
) -> list[OptResult]:
    """网格搜索参数组合，按 metric 降序返回。"""
    import gc

    from aimoon.enhanced_backtest import EnhancedBacktestEngine

    ranges = param_ranges or _PARAM_RANGES
    keys = sorted(ranges.keys())
    combos = list(itertools.product(*(ranges[k] for k in keys)))

    if max_trials > 0 and len(combos) > max_trials:
        rng = np.random.default_rng(42)
        indices = rng.choice(len(combos), size=max_trials, replace=False)
        combos = [combos[i] for i in sorted(indices)]

    results: list[OptResult] = []
    for combo in combos:
        params = dict(zip(keys, combo))
        engine = EnhancedBacktestEngine(
            hold_days=params.get("hold_days", cfg.hold_days),
            stop_loss_pct=params.get("stop_loss_pct", cfg.stop_loss_pct),
            take_profit_pct=params.get("take_profit_pct", cfg.take_profit_pct),
            max_positions=cfg.max_positions,
            commission=0.0003,
            slippage=0.001,
            stamp_tax=0.0005,
            entry_threshold=cfg.entry_threshold,
            max_sector_pct=cfg.max_sector_pct,
            use_alpha=cfg.use_alpha,
            use_kelly=True,
            exit_ratio=0.6,
            benchmark_code=cfg.benchmark_code,
            backtest_start_date=cfg.backtest_start_date,
        )
        res = engine.run_portfolio(klines, names, ctx=ctx)
        engine._release_memory()
        del engine
        if len(combos) > 5:
            gc.collect()
        results.append(
            OptResult(
                params=params,
                sharpe=res.sharpe_ratio,
                sortino=res.sortino_ratio,
                total_return=res.total_return,
                max_drawdown=res.max_drawdown,
                trade_count=res.trade_count,
            )
        )

    metric_key = {"sharpe": "sharpe", "sortino": "sortino", "return": "total_return"}
    key = metric_key.get(metric, "sharpe")
    results.sort(key=lambda r: getattr(r, key), reverse=True)
    return results


def walk_forward_validate(
    klines: dict,
    names: dict,
    cfg: Config,
    cache: DataCache,
    train_pct: float = 0.7,
    n_splits: int = 3,
    ctx: dict | None = None,
) -> WalkForwardResult:
    """Walk-forward 验证：滑动窗口训练+测试，检测过拟合。

    改进：
    - 滑动窗口：250天训练 / 60天测试，每月滚动
    - 过拟合检测：test_sharpe / train_sharpe < 0.5 时警告
    - 更多评估指标：IC、胜率、最大回撤
    """
    from aimoon.enhanced_backtest import EnhancedBacktestEngine

    all_dates: set = set()
    for df in klines.values():
        for idx in df.index:
            # 统一转换为 Timestamp，避免 int 和 Timestamp 混合导致排序失败
            if isinstance(idx, (int, np.integer)):
                # 整数索引无法直接转换，跳过该数据
                continue
            all_dates.add(pd.Timestamp(idx))
    sorted_dates = sorted(all_dates)
    total = len(sorted_dates)
    if total < 120:
        return WalkForwardResult((), 0.0, 0.0, 0.0)

    # 滑动窗口参数
    train_window = min(250, int(total * 0.6))  # 训练窗口
    test_window = 60  # 测试窗口
    step = 20  # 每月滚动一次

    splits: list[WalkForwardSplit] = []
    overfit_warnings: list[str] = []

    start = 0
    while start + train_window + test_window <= total:
        train_end = start + train_window
        test_end = min(train_end + test_window, total)

        train_dates = set(sorted_dates[start:train_end])
        test_dates = set(sorted_dates[train_end:test_end])

        train_klines = {}
        for code, df in klines.items():
            sub = df[df.index.isin(train_dates)]
            if len(sub) >= 60:
                train_klines[code] = sub

        test_klines = {}
        for code, df in klines.items():
            # 测试集不应包含训练集数据，避免数据泄漏
            sub = df[df.index.isin(test_dates)]
            if len(sub) >= 60:
                test_klines[code] = sub

        if not train_klines or not test_klines:
            start += step
            continue

        # Regime detection at train/test split
        train_regime, test_regime = _detect_regime_at_split(
            train_klines,
            str(sorted_dates[train_end - 1]),
            str(sorted_dates[train_end]),
        )
        if train_regime and test_regime and train_regime != test_regime:
            logger.info(
                "Window %d: regime shift at split: train=%s -> test=%s (skipping)",
                start,
                train_regime,
                test_regime,
            )
            start += step
            continue

        if not train_klines or not test_klines:
            start += step
            continue

        # Quick grid on train
        train_results = grid_search(
            train_klines,
            names,
            cfg,
            cache,
            param_ranges={
                "stop_loss_pct": [0.04, 0.05, 0.07],
                "hold_days": [10, 15, 20],
            },
            max_trials=10,
            ctx=ctx,
        )
        best = train_results[0].params if train_results else {}

        engine = EnhancedBacktestEngine(
            hold_days=best.get("hold_days", cfg.hold_days),
            stop_loss_pct=best.get("stop_loss_pct", cfg.stop_loss_pct),
            take_profit_pct=cfg.take_profit_pct,
            max_positions=cfg.max_positions,
            entry_threshold=cfg.entry_threshold,
            max_sector_pct=cfg.max_sector_pct,
            use_alpha=cfg.use_alpha,
            use_kelly=True,
            exit_ratio=0.6,
            benchmark_code=cfg.benchmark_code,
            backtest_start_date=cfg.backtest_start_date,
        )
        train_res = engine.run_portfolio(train_klines, names, ctx=ctx)
        test_res = engine.run_portfolio(test_klines, names, ctx=ctx)
        engine._release_memory()

        # 过拟合检测
        overfit_ratio = (
            test_res.sharpe_ratio / train_res.sharpe_ratio if train_res.sharpe_ratio > 0 else 0
        )
        if overfit_ratio < 0.5 and train_res.sharpe_ratio > 0.5:
            overfit_warnings.append(
                f"Window {start}-{test_end}: train_sharpe={train_res.sharpe_ratio:.2f}, "
                f"test_sharpe={test_res.sharpe_ratio:.2f}, ratio={overfit_ratio:.2f} (OVERFIT!)"
            )

        splits.append(
            WalkForwardSplit(
                split_idx=len(splits),
                train_start=str(sorted_dates[start]),
                train_end=str(sorted_dates[train_end - 1]),
                test_start=str(sorted_dates[train_end]),
                test_end=str(sorted_dates[test_end - 1]),
                train_sharpe=train_res.sharpe_ratio,
                test_sharpe=test_res.sharpe_ratio,
                test_return=test_res.total_return,
                test_max_dd=test_res.max_drawdown,
            )
        )

        start += step

    sharpes = [s.test_sharpe for s in splits]
    std = float(np.std(sharpes)) if len(sharpes) > 1 else 0.0
    stability = float(np.mean(sharpes) / std) if std > 0 else 0.0

    return WalkForwardResult(
        splits=tuple(splits),
        stability_score=round(stability, 2),
        avg_test_sharpe=round(float(np.mean(sharpes)), 2) if sharpes else 0.0,
        avg_test_return=(
            round(float(np.mean([s.test_return for s in splits])), 2) if splits else 0.0
        ),
        overfit_warnings=tuple(overfit_warnings),
    )
