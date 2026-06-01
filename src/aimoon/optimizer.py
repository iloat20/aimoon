"""Parameter optimization (grid search) + walk-forward validation."""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from aimoon.cache import DataCache
from aimoon.config import Config

logger = logging.getLogger(__name__)


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


_PARAM_RANGES: dict[str, list] = {
    "rsi_period": [8, 10, 12, 14],
    "macd_fast": [8, 10, 12, 14],
    "stop_loss_pct": [0.05, 0.07, 0.10],
    "hold_days": [10, 15, 20, 25, 30],
}


def grid_search(
    klines: dict, names: dict, cfg: Config, cache: DataCache,
    param_ranges: dict[str, list] | None = None,
    metric: str = "sharpe",
    max_trials: int = 0,
    ctx: dict | None = None,
) -> list[OptResult]:
    """网格搜索参数组合，按 metric 降序返回。"""
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
        )
        res = engine.run_portfolio(klines, names, ctx=ctx)
        results.append(OptResult(
            params=params,
            sharpe=res.sharpe_ratio,
            sortino=res.sortino_ratio,
            total_return=res.total_return,
            max_drawdown=res.max_drawdown,
            trade_count=res.trade_count,
        ))

    metric_key = {"sharpe": "sharpe", "sortino": "sortino", "return": "total_return"}
    key = metric_key.get(metric, "sharpe")
    results.sort(key=lambda r: getattr(r, key), reverse=True)
    return results


def walk_forward_validate(
    klines: dict, names: dict, cfg: Config, cache: DataCache,
    train_pct: float = 0.7, n_splits: int = 3,
    ctx: dict | None = None,
) -> WalkForwardResult:
    """Walk-forward 验证：滚动窗口训练+测试，检测过拟合。"""
    from aimoon.enhanced_backtest import EnhancedBacktestEngine

    all_dates: set = set()
    for df in klines.values():
        all_dates.update(df.index)
    sorted_dates = sorted(all_dates)
    total = len(sorted_dates)
    if total < 120:
        return WalkForwardResult((), 0.0, 0.0, 0.0)

    split_size = total // n_splits
    splits: list[WalkForwardSplit] = []

    for i in range(n_splits):
        start = i * split_size
        end = min(start + split_size + split_size // 2, total)
        window_dates = sorted_dates[start:end]
        if len(window_dates) < 60:
            continue

        train_end_idx = int(len(window_dates) * train_pct)
        train_dates = set(window_dates[:train_end_idx])
        test_dates = set(window_dates[train_end_idx:])

        train_klines = {}
        for code, df in klines.items():
            sub = df[df.index.isin(train_dates)]
            if len(sub) >= 60:
                train_klines[code] = sub

        test_klines = {}
        for code, df in klines.items():
            sub = df[df.index.isin(test_dates | train_dates)]
            if len(sub) >= 60:
                test_klines[code] = sub

        # Quick grid on train
        train_results = grid_search(
            train_klines, names, cfg, cache,
            param_ranges={"stop_loss_pct": [0.05, 0.07, 0.10], "hold_days": [10, 15, 20]},
            max_trials=10, ctx=ctx,
        )
        best = train_results[0].params if train_results else {}

        engine = EnhancedBacktestEngine(
            hold_days=best.get("hold_days", cfg.hold_days),
            stop_loss_pct=best.get("stop_loss_pct", cfg.stop_loss_pct),
        )
        train_res = engine.run_portfolio(train_klines, names, ctx=ctx)
        test_res = engine.run_portfolio(test_klines, names, ctx=ctx)

        splits.append(WalkForwardSplit(
            split_idx=i,
            train_start=str(window_dates[0]),
            train_end=str(window_dates[train_end_idx - 1]),
            test_start=str(window_dates[train_end_idx]),
            test_end=str(window_dates[-1]),
            train_sharpe=train_res.sharpe_ratio,
            test_sharpe=test_res.sharpe_ratio,
            test_return=test_res.total_return,
            test_max_dd=test_res.max_drawdown,
        ))

    sharpes = [s.test_sharpe for s in splits]
    std = float(np.std(sharpes)) if len(sharpes) > 1 else 0.0
    stability = float(np.mean(sharpes) / std) if std > 0 else 0.0

    return WalkForwardResult(
        splits=tuple(splits),
        stability_score=round(stability, 2),
        avg_test_sharpe=round(float(np.mean(sharpes)), 2) if sharpes else 0.0,
        avg_test_return=round(float(np.mean([s.test_return for s in splits])), 2) if splits else 0.0,
    )
