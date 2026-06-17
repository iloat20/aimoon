"""报告生成 — 图表输出与绩效摘要。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 中文字体
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def plot_equity_curve(
    equity: pd.Series,
    output_path: str | Path,
    title: str = "Equity Curve",
) -> None:
    """绘制权益曲线。"""
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(equity.index, equity.values, linewidth=1.0, color="steelblue")
    ax.set_title(title, fontsize=14)
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative Return")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150)
    plt.close(fig)
    logger.info("Equity curve saved to %s", output_path)


def plot_drawdown(
    daily_returns: pd.Series,
    output_path: str | Path,
) -> None:
    """绘制回撤图。"""
    cum = (1.0 + daily_returns).cumprod()
    running_max = cum.cummax()
    drawdown = cum / running_max - 1.0

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.fill_between(drawdown.index, drawdown.values, 0, color="coral", alpha=0.6)
    ax.set_title("Drawdown", fontsize=14)
    ax.set_ylabel("Drawdown")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150)
    plt.close(fig)
    logger.info("Drawdown plot saved to %s", output_path)


def plot_monthly_returns_heatmap(
    monthly_returns: pd.Series,
    output_path: str | Path,
) -> None:
    """绘制月度收益热力图。"""
    if monthly_returns.empty:
        logger.warning("No monthly returns to plot")
        return

    df = pd.DataFrame({"return": monthly_returns})
    df["year"] = df.index.year
    df["month"] = df.index.month

    pivot = df.pivot_table(index="year", columns="month", values="return", aggfunc="sum")
    pivot = pivot.fillna(0)

    month_labels = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    pivot.columns = [month_labels[m - 1] for m in pivot.columns]

    fig, ax = plt.subplots(figsize=(12, max(3, len(pivot) * 0.8)))
    im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto", vmin=-0.1, vmax=0.1)
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels(pivot.columns)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index)

    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            val = pivot.values[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.1%}", ha="center", va="center", fontsize=8)

    ax.set_title("Monthly Returns Heatmap", fontsize=14)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150)
    plt.close(fig)
    logger.info("Monthly returns heatmap saved to %s", output_path)


def plot_ic_decay(
    ic_df: pd.DataFrame,
    output_path: str | Path,
) -> None:
    """绘制 IC 衰减图（不同滞后期的 IC 均值）。"""
    fig, ax = plt.subplots(figsize=(10, 5))

    for col in ic_df.columns[:10]:  # 最多画 10 个因子
        series = ic_df[col].dropna()
        if len(series) < 10:
            continue
        # 滚动 IC
        rolling_ic = series.rolling(window=20, min_periods=5).mean()
        ax.plot(rolling_ic.index, rolling_ic.values, linewidth=0.8, alpha=0.7, label=col)

    ax.axhline(y=0, color="gray", linestyle="--", linewidth=0.5)
    ax.set_title("IC Decay (20-day Rolling Mean)", fontsize=14)
    ax.set_xlabel("Date")
    ax.set_ylabel("Rolling IC")
    ax.legend(fontsize=7, loc="upper right", ncol=2)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150)
    plt.close(fig)
    logger.info("IC decay plot saved to %s", output_path)


def plot_factor_importance(
    importance: dict[str, float],
    output_path: str | Path,
    top_n: int = 20,
) -> None:
    """绘制因子重要性图。"""
    sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:top_n]
    names = [x[0] for x in sorted_imp]
    values = [x[1] for x in sorted_imp]

    fig, ax = plt.subplots(figsize=(10, max(4, top_n * 0.3)))
    y_pos = range(len(names))
    ax.barh(y_pos, values, color="steelblue", height=0.6)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_title(f"Top {top_n} Factor Importance", fontsize=14)
    ax.set_xlabel("Importance")
    ax.grid(True, alpha=0.3, axis="x")
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150)
    plt.close(fig)
    logger.info("Factor importance plot saved to %s", output_path)


def plot_quantile_returns(
    factor_df: pd.DataFrame,
    forward_returns: pd.DataFrame,
    factor_name: str,
    output_path: str | Path,
    n_quantiles: int = 5,
) -> None:
    """绘制因子分位数收益对比图。"""
    merged = factor_df.merge(forward_returns, on=["date", "symbol"], how="inner")
    if factor_name not in merged.columns:
        logger.warning("Factor %s not found for quantile plot", factor_name)
        return

    merged = merged.dropna(subset=[factor_name, "forward_return"])
    merged["quantile"] = merged.groupby("date")[factor_name].transform(
        lambda x: pd.qcut(x, n_quantiles, labels=False, duplicates="drop")
    )

    quantile_means = merged.groupby("quantile")["forward_return"].mean()

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(range(len(quantile_means)), quantile_means.values, color="steelblue")
    ax.set_xticks(range(len(quantile_means)))
    ax.set_xticklabels([f"Q{i + 1}" for i in range(len(quantile_means))])
    ax.set_title(f"Quantile Returns: {factor_name}", fontsize=14)
    ax.set_xlabel("Quantile")
    ax.set_ylabel("Mean Forward Return")
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150)
    plt.close(fig)
    logger.info("Quantile returns plot saved to %s", output_path)


def generate_summary_report(
    metrics: Any,
    output_path: str | Path,
    extra_info: dict[str, Any] | None = None,
) -> str:
    """生成回测摘要报告。"""
    lines = [
        "# 回测绩效报告",
        "",
        "## 核心指标",
        "",
        "| 指标 | 值 |",
        "|------|------|",
        f"| 总收益率 | {metrics.total_return:.2%} |",
        f"| 年化收益率 | {metrics.annual_return:.2%} |",
        f"| 年化波动率 | {metrics.annual_volatility:.2%} |",
        f"| 夏普比率 | {metrics.sharpe_ratio:.4f} |",
        f"| 最大回撤 | {metrics.max_drawdown:.2%} |",
        f"| Calmar 比率 | {metrics.calmar_ratio:.4f} |",
        f"| 胜率 | {metrics.win_rate:.2%} |",
        f"| 盈亏比 | {metrics.profit_loss_ratio:.4f} |",
        f"| 交易天数 | {metrics.n_trading_days} |",
    ]

    if extra_info:
        lines.append("")
        lines.append("## 优化信息")
        lines.append("")
        for k, v in extra_info.items():
            lines.append(f"- **{k}**: {v}")

    report = "\n".join(lines)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(report, encoding="utf-8")
    logger.info("Summary report saved to %s", output_path)
    return report


class Reporter:
    """报告生成器：统一管理所有图表和报告输出。"""

    def __init__(self, output_dir: str = "output") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(
        self,
        backtest_result: Any,
        ic_df: pd.DataFrame | None = None,
        factor_importance: dict[str, float] | None = None,
        optimization_result: Any = None,
        factor_df: pd.DataFrame | None = None,
        forward_returns: pd.DataFrame | None = None,
    ) -> None:
        """生成全部报告。"""
        # 权益曲线
        if not backtest_result.equity_curve.empty:
            plot_equity_curve(
                backtest_result.equity_curve,
                self.output_dir / "equity_curve.png",
            )

        # 回撤图
        if not backtest_result.daily_returns.empty:
            plot_drawdown(
                backtest_result.daily_returns,
                self.output_dir / "drawdown.png",
            )

        # 月度收益热力图
        if not backtest_result.monthly_returns.empty:
            plot_monthly_returns_heatmap(
                backtest_result.monthly_returns,
                self.output_dir / "monthly_returns.png",
            )

        # IC 衰减图
        if ic_df is not None and not ic_df.empty:
            plot_ic_decay(
                ic_df,
                self.output_dir / "ic_decay.png",
            )

        # 因子重要性
        if factor_importance:
            plot_factor_importance(
                factor_importance,
                self.output_dir / "factor_importance.png",
            )

        # 绩效摘要
        extra_info = {}
        if optimization_result is not None:
            extra_info = {
                "Forward Days": optimization_result.forward_days,
                "Val Sharpe": f"{optimization_result.best_val_sharpe:.4f}",
                "Train Sharpe": f"{optimization_result.train_sharpe:.4f}",
                "Overfit Ratio": f"{optimization_result.overfit_ratio:.2f}",
                "Optuna Trials": optimization_result.n_trials,
                "Duration": f"{optimization_result.duration_seconds:.1f}s",
            }

        generate_summary_report(
            backtest_result.metrics,
            self.output_dir / "backtest_report.md",
            extra_info=extra_info,
        )

        logger.info("All reports generated in %s", self.output_dir)
