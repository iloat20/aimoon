"""Chart generation with matplotlib (optional dependency)."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def _require() -> None:
    if not HAS_MATPLOTLIB:
        raise ImportError("matplotlib required. Install: pip install matplotlib>=3.8")


def plot_equity_curve(
    equity_curve: tuple[float, ...],
    benchmark_curve: tuple[float, ...] | None = None,
    title: str = "Portfolio Equity Curve",
    filepath: str = "equity_curve.png",
) -> str:
    """组合权益曲线（含可选基准）。"""
    _require()
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(equity_curve, label="Portfolio", linewidth=2, color="#2196F3")
    if benchmark_curve and len(benchmark_curve) > 1:
        scale = equity_curve[0] / benchmark_curve[0] if benchmark_curve[0] != 0 else 1.0
        ax.plot(
            [v * scale for v in benchmark_curve],
            label="Benchmark",
            linewidth=1.5,
            color="#FF9800",
            linestyle="--",
        )
    ax.set_title(title, fontsize=14)
    ax.set_xlabel("Rebalance Period")
    ax.set_ylabel("Equity")
    ax.legend()
    ax.grid(True, alpha=0.3)
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return filepath


def plot_drawdown(
    drawdown_curve: tuple[float, ...],
    filepath: str = "drawdown.png",
) -> str:
    """回撤面积图。"""
    _require()
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.fill_between(
        range(len(drawdown_curve)),
        [d * 100 for d in drawdown_curve],
        alpha=0.4,
        color="#F44336",
    )
    ax.set_title("Drawdown", fontsize=14)
    ax.set_xlabel("Rebalance Period")
    ax.set_ylabel("Drawdown %")
    ax.grid(True, alpha=0.3)
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return filepath


def plot_monthly_returns(
    trades: tuple,
    filepath: str = "monthly_returns.png",
) -> str:
    """月度平均收益柱状图。"""
    _require()
    from collections import defaultdict
    from datetime import datetime

    monthly: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        try:
            dt = datetime.strptime(t.exit_date[:10], "%Y-%m-%d")
            monthly[dt.strftime("%Y-%m")].append(t.return_pct)
        except (ValueError, AttributeError):
            continue

    if not monthly:
        return filepath

    months = sorted(monthly.keys())
    avg_returns = [sum(monthly[m]) / len(monthly[m]) for m in months]

    fig, ax = plt.subplots(figsize=(14, 5))
    colors = ["#4CAF50" if v >= 0 else "#F44336" for v in avg_returns]
    ax.bar(months, avg_returns, color=colors, alpha=0.8)
    ax.set_title("Average Trade Return by Month", fontsize=14)
    ax.set_xlabel("Month")
    ax.set_ylabel("Avg Return %")
    ax.tick_params(axis="x", rotation=45)
    ax.grid(True, alpha=0.3, axis="y")
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return filepath
