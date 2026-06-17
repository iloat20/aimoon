"""Alpha Zoo 因子质量过滤 — ICIR + 换手率 + 相关性三闸门。

职责: 在训练/回测前独立运行，输出高质量因子白名单。
不涉及信号生成或加权（分别在 scorer.py 和 weighting.py）。
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

from aimoon.factors.registry import Registry

logger = logging.getLogger(__name__)


# ── 因子质量过滤 ──


def filter_factors(
    factor_icir: pd.Series,
    factor_turnover: pd.Series,
    factor_correlation: pd.DataFrame,
    icir_threshold: float = 0.5,
    turnover_threshold: float = 0.8,
    correlation_threshold: float = 0.6,
) -> list[str]:
    """Filter alpha factors by quality criteria (ICIR + turnover + correlation)."""
    high_icir = factor_icir[factor_icir >= icir_threshold]
    if high_icir.empty:
        max_icir = factor_icir.max() if len(factor_icir) > 0 else 0.0
        logger.info(
            "No factors pass ICIR >= %.2f (max=%.3f), relaxing to 0.3",
            icir_threshold,
            max_icir,
        )
        high_icir = factor_icir[factor_icir >= 0.3]
    if high_icir.empty:
        top_n = min(20, len(factor_icir))
        if top_n == 0:
            logger.warning("No factors available at all")
            return []
        top_ids = factor_icir.nlargest(top_n)
        logger.info(
            "ICIR all below 0.3, keeping top-%d factors (best=%.3f)", top_n, top_ids.iloc[0]
        )
        high_icir = top_ids

    if not factor_turnover.empty:
        low_turnover = factor_turnover[factor_turnover < turnover_threshold]
        candidates = [fid for fid in high_icir.index if fid in low_turnover.index]
        if not candidates:
            logger.warning(
                "No high-ICIR factors pass turnover < %.2f, skipping turnover filter",
                turnover_threshold,
            )
            candidates = list(high_icir.index)
    else:
        candidates = list(high_icir.index)

    if not factor_correlation.empty and len(candidates) > 1:
        candidates = _greedy_correlation_filter(
            candidates, factor_icir, factor_correlation, correlation_threshold
        )

    candidates.sort(key=lambda fid: high_icir.get(fid, factor_icir.get(fid, 0.0)), reverse=True)

    logger.info(
        "Factor filter: %d total -> %d after ICIR>%.1f -> %d after turnover<%.1f -> %d after corr<%.1f",  # noqa: E501
        len(factor_icir),
        len(high_icir),
        icir_threshold,
        len(high_icir),
        turnover_threshold,
        len(candidates),
        correlation_threshold,
    )
    return candidates


def _greedy_correlation_filter(
    candidates: list[str],
    icir: pd.Series,
    corr_matrix: pd.DataFrame,
    threshold: float,
) -> list[str]:
    """Greedy correlation-based deduplication (ICIR-descending order)."""
    sorted_ids = sorted(candidates, key=lambda fid: icir.get(fid, 0.0), reverse=True)
    kept: list[str] = []
    removed: set[str] = set()
    for fid in sorted_ids:
        if fid in removed:
            continue
        kept.append(fid)
        if fid in corr_matrix.columns:
            for peer in sorted_ids:
                if peer == fid or peer in removed:
                    continue
                if peer in corr_matrix.columns:
                    corr_val = abs(corr_matrix.loc[fid, peer])
                    if corr_val >= threshold:
                        removed.add(peer)
    return kept


# ── 换手率和相关性计算 ──


def compute_factor_turnover(
    registry: Registry,
    panel: dict[str, pd.DataFrame],
    n_dates: int = 20,
    factor_cache: dict[str, pd.DataFrame] | None = None,
) -> pd.Series:
    """Compute daily signal turnover for each alpha factor.

    Turnover = mean fraction of stocks whose cross-sectional rank quintile
    changes between consecutive dates. Low turnover = stable factor.
    """
    close = panel.get("close")
    if close is None or len(close) < n_dates + 5:
        return pd.Series(dtype=float)

    dates = close.index[-n_dates:].tolist()
    alpha_ids = registry.list()

    if factor_cache is None:
        factor_cache = {}
        for alpha_id in alpha_ids:
            try:
                factor_cache[alpha_id] = registry.compute(alpha_id, panel)
            except (ValueError, TypeError, KeyError, RuntimeError):
                continue

    turnover_sums: dict[str, float] = {aid: 0.0 for aid in factor_cache}
    turnover_counts: dict[str, int] = {aid: 0 for aid in factor_cache}

    for i in range(1, len(dates)):
        prev_date, curr_date = dates[i - 1], dates[i]
        for alpha_id, factor_df in factor_cache.items():
            if prev_date not in factor_df.index or curr_date not in factor_df.index:
                continue
            prev_row = factor_df.loc[prev_date].dropna()
            curr_row = factor_df.loc[curr_date].dropna()
            common = prev_row.index.intersection(curr_row.index)
            if len(common) < 10:
                continue
            prev_rank = prev_row[common].rank(pct=True)
            curr_rank = curr_row[common].rank(pct=True)
            prev_quintile = (prev_rank * 5).astype(int).clip(0, 4)
            curr_quintile = (curr_rank * 5).astype(int).clip(0, 4)
            changed = (prev_quintile != curr_quintile).sum()
            turnover_rate = changed / len(common)
            turnover_sums[alpha_id] += turnover_rate
            turnover_counts[alpha_id] += 1

    result = {}
    for alpha_id in turnover_sums:
        if turnover_counts[alpha_id] > 0:
            result[alpha_id] = turnover_sums[alpha_id] / turnover_counts[alpha_id]

    return pd.Series(result, dtype=float)


def compute_factor_correlation(
    registry: Registry,
    panel: dict[str, pd.DataFrame],
    n_dates: int = 20,
    factor_cache: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Compute pairwise factor correlation matrix (rank correlation averaged over dates)."""
    from scipy.stats import spearmanr

    close = panel.get("close")
    if close is None or len(close) < n_dates + 5:
        return pd.DataFrame()

    dates = close.index[-n_dates:].tolist()
    alpha_ids = registry.list()

    if factor_cache is None:
        factor_cache = {}
        for alpha_id in alpha_ids:
            try:
                factor_cache[alpha_id] = registry.compute(alpha_id, panel)
            except (ValueError, TypeError, KeyError, RuntimeError):
                continue

    valid_ids = list(factor_cache.keys())
    if len(valid_ids) < 2:
        return pd.DataFrame()

    rank_vectors: dict[str, list[np.ndarray]] = {aid: [] for aid in valid_ids}

    global_codes: set | None = None
    for date in dates:
        date_codes: set | None = None
        for alpha_id, factor_df in factor_cache.items():
            if date in factor_df.index:
                row = factor_df.loc[date].dropna()
                if len(row) >= 10:
                    if date_codes is None:
                        date_codes = set(row.index)
                    else:
                        date_codes &= set(row.index)
        if date_codes is not None and len(date_codes) >= 10:
            if global_codes is None:
                global_codes = date_codes
            else:
                global_codes &= date_codes

    if global_codes is None or len(global_codes) < 10:
        return pd.DataFrame()

    sorted_codes = sorted(global_codes)

    for date in dates:
        date_ranks: dict[str, pd.Series] = {}
        for alpha_id, factor_df in factor_cache.items():
            if date in factor_df.index:
                row = factor_df.loc[date].dropna()
                if len(row) >= 10:
                    date_ranks[alpha_id] = row.rank(pct=True)
        if len(date_ranks) < 2:
            continue
        for alpha_id, ranks in date_ranks.items():
            rank_vectors[alpha_id].append(ranks[sorted_codes].values)

    avg_ranks: dict[str, np.ndarray] = {}
    for alpha_id, vectors in rank_vectors.items():
        if vectors:
            avg_ranks[alpha_id] = np.mean(vectors, axis=0)

    ids = sorted(avg_ranks.keys())
    n = len(ids)
    corr_data = np.eye(n)

    for i in range(n):
        for j in range(i + 1, n):
            try:
                vi = avg_ranks[ids[i]]
                vj = avg_ranks[ids[j]]
                if np.std(vi) == 0 or np.std(vj) == 0:
                    continue
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=RuntimeWarning, module="scipy.stats")
                    corr, _ = spearmanr(vi, vj)
                corr_data[i, j] = corr
                corr_data[j, i] = corr
            except (ValueError, RuntimeError):
                pass

    return pd.DataFrame(corr_data, index=ids, columns=ids)
