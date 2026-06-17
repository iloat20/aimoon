"""Grid search for backtest parameters with rolling window validation.

Searches over stop_loss, take_profit, entry_threshold combinations,
validates on rolling windows to avoid overfitting, and outputs heatmaps.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GridSearchConfig:
    """Parameter ranges for grid search."""

    stop_loss_range: tuple[float, ...] = (0.03, 0.04, 0.05, 0.06, 0.08)
    take_profit_range: tuple[float, ...] = (0.08, 0.10, 0.12, 0.15, 0.20)
    entry_threshold_range: tuple[float, ...] = (50.0, 55.0, 60.0, 65.0)
    n_windows: int = 4  # Rolling validation windows
    train_ratio: float = 0.6  # Fraction of data for training per window


@dataclass(frozen=True)
class GridSearchResult:
    """Result of grid search for one parameter combination."""

    stop_loss: float
    take_profit: float
    entry_threshold: float
    avg_sharpe: float  # Average Sharpe across validation windows
    std_sharpe: float  # Std of Sharpe across windows (stability)
    avg_return: float
    avg_max_dd: float
    avg_win_rate: float
    window_sharpes: tuple[float, ...]
    is_best: bool = False


@dataclass(frozen=True)
class GridSearchOutput:
    """Full grid search output."""

    results: tuple[GridSearchResult, ...]
    best: GridSearchResult
    heatmap_data: pd.DataFrame  # Pivot table for heatmap
    n_combinations: int
    n_windows: int
    total_duration: float


def run_grid_search(
    klines: dict[str, pd.DataFrame],
    names: dict[str, str],
    cfg: object,
    cache: object,
    ctx: dict | None = None,
    grid_cfg: GridSearchConfig | None = None,
    output_dir: str = "output",
) -> GridSearchOutput:
    """Run grid search over stop_loss, take_profit, entry_threshold.

    Uses rolling window validation: splits data into N windows,
    runs backtest on each window's validation period, averages Sharpe.

    Parameters
    ----------
    klines : dict[str, pd.DataFrame]
        Stock kline data.
    names : dict[str, pd.Series]
        Stock name mapping.
    cfg : Config
        Configuration object.
    cache : DataCache
        Cache instance.
    ctx : dict | None
        Context (sector_map, etc.).
    grid_cfg : GridSearchConfig | None
        Grid search configuration.
    output_dir : str
        Directory for output charts.

    Returns
    -------
    GridSearchOutput
    """
    grid_cfg = grid_cfg or GridSearchConfig()
    start_time = time.time()

    # Find common date range across all stocks
    all_dates = _find_common_dates(klines, min_bars=60)
    if len(all_dates) < 90:
        logger.error("Insufficient data for grid search: %d dates", len(all_dates))
        return _empty_output()

    # Generate rolling windows
    windows = _generate_windows(all_dates, grid_cfg.n_windows, grid_cfg.train_ratio)
    logger.info(
        "Grid search: %d windows, %d SL x %d TP x %d ET = %d combinations",
        len(windows),
        len(grid_cfg.stop_loss_range),
        len(grid_cfg.take_profit_range),
        len(grid_cfg.entry_threshold_range),
        len(grid_cfg.stop_loss_range)
        * len(grid_cfg.take_profit_range)
        * len(grid_cfg.entry_threshold_range),
    )

    # Run grid search
    combinations = list(
        product(
            grid_cfg.stop_loss_range,
            grid_cfg.take_profit_range,
            grid_cfg.entry_threshold_range,
        )
    )
    results: list[GridSearchResult] = []

    for i, (sl, tp, et) in enumerate(combinations):
        window_sharpes: list[float] = []
        window_returns: list[float] = []
        window_dds: list[float] = []
        window_wrs: list[float] = []

        for win_start, val_start, val_end in windows:
            sharpe, ret, dd, wr = _run_window_backtest(
                klines,
                names,
                cfg,
                cache,
                ctx,
                stop_loss=sl,
                take_profit=tp,
                entry_threshold=et,
                backtest_start=_to_date_str(val_start),
            )
            window_sharpes.append(sharpe)
            window_returns.append(ret)
            window_dds.append(dd)
            window_wrs.append(wr)

        result = GridSearchResult(
            stop_loss=sl,
            take_profit=tp,
            entry_threshold=et,
            avg_sharpe=float(np.mean(window_sharpes)) if window_sharpes else 0.0,
            std_sharpe=float(np.std(window_sharpes)) if window_sharpes else 0.0,
            avg_return=float(np.mean(window_returns)) if window_returns else 0.0,
            avg_max_dd=float(np.mean(window_dds)) if window_dds else 0.0,
            avg_win_rate=float(np.mean(window_wrs)) if window_wrs else 0.0,
            window_sharpes=tuple(window_sharpes),
        )
        results.append(result)

        if (i + 1) % 10 == 0 or i == len(combinations) - 1:
            logger.info("Grid search progress: %d/%d", i + 1, len(combinations))

    # Find best
    best = max(results, key=lambda r: r.avg_sharpe)
    best_key = (best.stop_loss, best.take_profit, best.entry_threshold)
    results_with_flag = tuple(
        GridSearchResult(
            stop_loss=r.stop_loss,
            take_profit=r.take_profit,
            entry_threshold=r.entry_threshold,
            avg_sharpe=r.avg_sharpe,
            std_sharpe=r.std_sharpe,
            avg_return=r.avg_return,
            avg_max_dd=r.avg_max_dd,
            avg_win_rate=r.avg_win_rate,
            window_sharpes=r.window_sharpes,
            is_best=((r.stop_loss, r.take_profit, r.entry_threshold) == best_key),
        )
        for r in results
    )

    # Build heatmap (fix entry_threshold at best value, vary SL x TP)
    heatmap_data = _build_heatmap(results, best.entry_threshold)

    duration = time.time() - start_time
    logger.info(
        "Grid search complete in %.1fs: best SL=%.2f TP=%.2f ET=%.0f (Sharpe=%.2f)",
        duration,
        best.stop_loss,
        best.take_profit,
        best.entry_threshold,
        best.avg_sharpe,
    )

    # Generate heatmap chart
    _save_heatmap(heatmap_data, best, output_dir)

    return GridSearchOutput(
        results=results_with_flag,
        best=best,
        heatmap_data=heatmap_data,
        n_combinations=len(combinations),
        n_windows=len(windows),
        total_duration=duration,
    )


def _to_date_str(dt: object) -> str:
    """Convert date-like object to YYYY-MM-DD string."""
    return str(dt.date()) if hasattr(dt, "date") else str(dt)


def _find_common_dates(
    klines: dict[str, pd.DataFrame],
    min_bars: int = 60,
) -> list:
    """Find dates common to enough stocks."""
    date_counts: dict = {}
    for kline in klines.values():
        for d in kline.index[min_bars:]:
            date_counts[d] = date_counts.get(d, 0) + 1

    min_stocks = max(2, len(klines) // 2)
    return sorted(d for d, c in date_counts.items() if c >= min_stocks)


def _generate_windows(
    dates: list,
    n_windows: int,
    train_ratio: float,
) -> list[tuple]:
    """Generate rolling (train_start, val_start, val_end) windows.

    Each window: first train_ratio of data = training (used implicitly
    by the ML model), remaining = validation (where we measure Sharpe).
    Returns list of (full_start, val_start, val_end).
    """
    n = len(dates)
    window_size = n // n_windows
    windows = []

    for i in range(n_windows):
        start_idx = i * window_size
        end_idx = min(start_idx + window_size + 30, n)  # slight overlap for stability
        val_start_idx = start_idx + int(window_size * train_ratio)

        if val_start_idx >= end_idx - 5:
            continue

        windows.append(
            (
                dates[start_idx],
                dates[val_start_idx],
                dates[min(end_idx - 1, n - 1)],
            )
        )

    return windows


def _run_window_backtest(
    klines: dict[str, pd.DataFrame],
    names: dict[str, str],
    cfg: Any,
    cache: Any,
    ctx: dict | None,
    stop_loss: float,
    take_profit: float,
    entry_threshold: float,
    backtest_start: str,
) -> tuple[float, float, float, float]:
    """Run enhanced backtest with given parameters, return (sharpe, return, max_dd, win_rate)."""
    from aimoon.enhanced_backtest import EnhancedBacktestEngine

    try:
        engine = EnhancedBacktestEngine(
            hold_days=cfg.hold_days,
            max_positions=cfg.max_positions,
            commission=cfg.commission if hasattr(cfg, "commission") else 0.0003,
            slippage=cfg.slippage if hasattr(cfg, "slippage") else 0.002,
            stamp_tax=cfg.stamp_tax if hasattr(cfg, "stamp_tax") else 0.001,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            entry_threshold=entry_threshold,
            benchmark_code=cfg.benchmark_code,
            max_sector_pct=cfg.max_sector_pct,
            use_alpha=getattr(cfg, "use_alpha", True),
            use_ml=getattr(cfg, "use_ml", True),
            use_kelly=True,
            backtest_start_date=backtest_start,
        )
        result = engine.run_portfolio(klines, names, ctx=ctx)
        return (
            result.sharpe_ratio,
            result.total_return,
            result.max_drawdown,
            result.win_rate,
        )
    except Exception as e:
        logger.debug(
            "Backtest failed (SL=%.2f TP=%.2f ET=%.0f): %s",
            stop_loss,
            take_profit,
            entry_threshold,
            e,
        )
        return (0.0, 0.0, 0.0, 0.0)


def _build_heatmap(results: list[GridSearchResult], fixed_et: float) -> pd.DataFrame:
    """Build SL x TP heatmap pivot table for the best entry_threshold."""
    filtered = [r for r in results if r.entry_threshold == fixed_et]
    if not filtered:
        # If no exact match, use all results
        filtered = results

    data = {
        "stop_loss": [r.stop_loss for r in filtered],
        "take_profit": [r.take_profit for r in filtered],
        "avg_sharpe": [r.avg_sharpe for r in filtered],
    }
    df = pd.DataFrame(data)
    pivot = df.pivot_table(index="stop_loss", columns="take_profit", values="avg_sharpe")
    return pivot


def _save_heatmap(heatmap_data: pd.DataFrame, best: GridSearchResult, output_dir: str) -> None:
    """Generate and save Sharpe ratio heatmap."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 8))
        im = ax.imshow(heatmap_data.values, cmap="RdYlGn", aspect="auto")

        ax.set_xticks(range(len(heatmap_data.columns)))
        ax.set_xticklabels([f"{v:.0%}" for v in heatmap_data.columns])
        ax.set_yticks(range(len(heatmap_data.index)))
        ax.set_yticklabels([f"{v:.0%}" for v in heatmap_data.index])
        ax.set_xlabel("Take Profit")
        ax.set_ylabel("Stop Loss")
        title = (
            f"Sharpe Heatmap (ET={best.entry_threshold:.0f})\n"
            f"Best: SL={best.stop_loss:.0%} TP={best.take_profit:.0%} "
            f"→ Sharpe={best.avg_sharpe:.2f}"
        )
        ax.set_title(title)

        # Add text annotations
        for i in range(len(heatmap_data.index)):
            for j in range(len(heatmap_data.columns)):
                val = heatmap_data.values[i, j]
                text_color = "white" if abs(val) > heatmap_data.values.std() * 1.5 else "black"
                ax.text(
                    j,
                    i,
                    f"{val:.2f}",
                    ha="center",
                    va="center",
                    color=text_color,
                    fontsize=9,
                )

        fig.colorbar(im, ax=ax, label="Avg Sharpe Ratio")
        plt.tight_layout()

        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        filepath = out_path / "grid_search_heatmap.png"
        fig.savefig(filepath, dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info("Heatmap saved: %s", filepath)
    except Exception as e:
        logger.warning("Failed to save heatmap: %s", e)


def _empty_output() -> GridSearchOutput:
    """Return empty GridSearchOutput."""
    return GridSearchOutput(
        results=(),
        best=GridSearchResult(
            stop_loss=0.0,
            take_profit=0.0,
            entry_threshold=0.0,
            avg_sharpe=0.0,
            std_sharpe=0.0,
            avg_return=0.0,
            avg_max_dd=0.0,
            avg_win_rate=0.0,
            window_sharpes=(),
        ),
        heatmap_data=pd.DataFrame(),
        n_combinations=0,
        n_windows=0,
        total_duration=0.0,
    )
