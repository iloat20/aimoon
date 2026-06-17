"""因子筛选与评价 — IC/ICIR 分析、相关性去重。"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)


def compute_rank_ic(
    factor_df: pd.DataFrame,
    forward_returns: pd.DataFrame,
    factor_cols: list[str],
    date_col: str = "date",
    symbol_col: str = "symbol",
) -> pd.DataFrame:
    """计算每个因子每日的 Rank IC (Spearman)。

    Parameters
    ----------
    factor_df : pd.DataFrame
        长表，包含 date, symbol, factor columns。
    forward_returns : pd.DataFrame
        宽表 (date x symbol)，前向收益率。
    factor_cols : list[str]
        因子列名列表。
    date_col : str
        日期列名。
    symbol_col : str
        股票代码列名。

    Returns
    -------
    pd.DataFrame
        每日 IC 时间序列，每列一个因子。
    """
    # factor_df 长表 -> 宽表
    ic_records: list[dict[str, Any]] = []

    merged = factor_df.merge(
        (
            forward_returns.rename(columns={c: f"ret_{c}" for c in forward_returns.columns})
            if False
            else _long_forward_returns(forward_returns, factor_df, date_col, symbol_col)
        ),
        on=[date_col, symbol_col],
        how="inner",
    )

    ret_col = "forward_return"
    for date, group in merged.groupby(date_col):
        if len(group) < 5:
            continue
        row: dict[str, Any] = {date_col: date}
        for fc in factor_cols:
            if fc not in group.columns:
                continue
            vals = group[[fc, ret_col]].dropna()
            if len(vals) < 5:
                row[fc] = np.nan
                continue
            ic, _ = spearmanr(vals[fc], vals[ret_col])
            row[fc] = ic
        ic_records.append(row)

    ic_df = pd.DataFrame(ic_records).set_index(date_col).sort_index()
    return ic_df


def _long_forward_returns(
    forward_returns: pd.DataFrame,
    factor_df: pd.DataFrame,
    date_col: str,
    symbol_col: str,
) -> pd.DataFrame:
    """将宽表前向收益率转为长表。"""
    long = forward_returns.stack(dropna=False).reset_index()
    long.columns = [date_col, symbol_col, "forward_return"]
    return long


def compute_ic_stats(
    ic_df: pd.DataFrame,
    min_abs_ic: float = 0.02,
    min_icir: float = 0.3,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """计算 IC 均值、ICIR，并筛选合格因子。

    Parameters
    ----------
    ic_df : pd.DataFrame
        每日 IC 时间序列 (date x factor)。
    min_abs_ic : float
        |IC| 阈值。
    min_icir : float
        ICIR 阈值。

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        (统计 DataFrame, 筛选后的 IC DataFrame)。
    """
    stats: list[dict[str, Any]] = []
    for col in ic_df.columns:
        series = ic_df[col].dropna()
        if len(series) < 10:
            continue
        ic_mean = series.mean()
        ic_std = series.std(ddof=1)
        icir = ic_mean / ic_std if ic_std > 0 else 0.0
        stats.append(
            {
                "factor": col,
                "ic_mean": ic_mean,
                "ic_abs_mean": abs(ic_mean),
                "ic_std": ic_std,
                "icir": icir,
                "n_days": len(series),
            }
        )

    stats_df = pd.DataFrame(stats).set_index("factor")
    # 筛选
    mask = (stats_df["ic_abs_mean"] > min_abs_ic) & (stats_df["icir"] > min_icir)
    selected = stats_df[mask].sort_values("icir", ascending=False)
    logger.info(
        "IC filter: %d / %d factors passed (|IC|>%.3f & ICIR>%.3f)",
        len(selected),
        len(stats_df),
        min_abs_ic,
        min_icir,
    )
    return stats_df, ic_df[selected.index.tolist()]


def remove_correlated_factors(
    ic_df: pd.DataFrame,
    max_corr: float = 0.6,
) -> list[str]:
    """基于 IC 时间序列的相关性矩阵，用层次聚类去重。

    在每个相关性簇中保留 ICIR 绝对值最大的因子。

    Parameters
    ----------
    ic_df : pd.DataFrame
        筛选后的 IC 时间序列。
    max_corr : float
        最大允许相关性。

    Returns
    -------
    list[str]
        去重后的因子名列表。
    """
    corr = ic_df.corr(method="spearman")
    # 距离矩阵
    dist = 1.0 - corr.abs().fillna(0)
    np.fill_diagonal(dist.values, 0.0)
    # 确保对称
    dist = (dist + dist.T) / 2.0

    condensed = squareform(dist.values, checks=False)
    Z = linkage(condensed, method="average")
    # 聚类：距离阈值 = 1 - max_corr
    clusters = fcluster(Z, t=1.0 - max_corr, criterion="distance")

    # 每个簇保留 ICIR 最大的因子
    icir = {}
    for col in ic_df.columns:
        s = ic_df[col].dropna()
        icir[col] = abs(s.mean() / s.std(ddof=1)) if s.std(ddof=1) > 0 else 0.0

    selected: list[str] = []
    for cl in np.unique(clusters):
        members = [corr.columns[i] for i in range(len(clusters)) if clusters[i] == cl]
        best = max(members, key=lambda x: icir.get(x, 0.0))
        selected.append(best)

    logger.info(
        "Correlation dedup: %d -> %d factors (max_corr=%.2f)",
        len(corr.columns),
        len(selected),
        max_corr,
    )
    return selected


def generate_factor_report(
    stats_df: pd.DataFrame,
    ic_df: pd.DataFrame,
    selected_factors: list[str],
    output_dir: str = "output",
) -> str:
    """生成因子表现报告。

    Parameters
    ----------
    stats_df : pd.DataFrame
        全部因子的 IC 统计。
    ic_df : pd.DataFrame
        IC 时间序列。
    selected_factors : list[str]
        最终选中的因子。
    output_dir : str
        输出目录。

    Returns
    -------
    str
        报告文本。
    """
    from pathlib import Path

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    lines = ["# 因子表现报告", ""]
    lines.append("## 全部因子统计")
    lines.append("")
    lines.append("| 因子 | IC均值 | |IC|均值 | ICIR | 天数 |")
    lines.append("|------|--------|---------|------|------|")
    for idx, row in stats_df.sort_values("icir", ascending=False).iterrows():
        marker = " ✓" if idx in selected_factors else ""
        lines.append(
            f"| {idx}{marker} | {row.ic_mean:+.4f} | {row.ic_abs_mean:.4f} | "
            f"{row.icir:+.4f} | {int(row.n_days)} |"
        )

    lines.append("")
    lines.append(f"## 选中因子 ({len(selected_factors)} 个)")
    lines.append("")
    for f in selected_factors:
        row = stats_df.loc[f]
        lines.append(f"- **{f}**: IC={row.ic_mean:+.4f}, ICIR={row.icir:+.4f}")

    report = "\n".join(lines)
    report_path = out / "factor_report.md"
    report_path.write_text(report, encoding="utf-8")
    logger.info("Factor report saved to %s", report_path)
    return report
