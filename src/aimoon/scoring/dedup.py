"""?????? ? ??????????????????

?? ALL_SCORERS ?????????????
????? > threshold ??????????????????? IC ????
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_scorer_correlations(
    signals_by_scorer: dict[str, list[dict[str, float]]],
    min_overlap: int = 20,
) -> pd.DataFrame:
    names = list(signals_by_scorer.keys())
    n = len(names)
    corr_matrix = pd.DataFrame(np.eye(n), index=names, columns=names)
    for i in range(n):
        for j in range(i + 1, n):
            s1 = pd.Series(signals_by_scorer[names[i]])
            s2 = pd.Series(signals_by_scorer[names[j]])
            common = s1.dropna().index.intersection(s2.dropna().index)
            if len(common) < min_overlap:
                continue
            corr = s1[common].corr(s2[common])
            corr_matrix.iloc[i, j] = corr
            corr_matrix.iloc[j, i] = corr
    return corr_matrix


def find_redundant_scorers(
    corr_matrix: pd.DataFrame,
    threshold: float = 0.85,
    performance_rank: dict[str, float] | None = None,
) -> tuple[list[str], list[tuple[str, str, float]]]:
    names = list(corr_matrix.index)
    removed: set[str] = set()
    reasons: list[tuple[str, str, float]] = []
    pairs: list[tuple[str, str, float]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            c = abs(corr_matrix.iloc[i, j])
            if c > threshold:
                pairs.append((names[i], names[j], c))
    pairs.sort(key=lambda x: x[2], reverse=True)
    for name_a, name_b, corr_val in pairs:
        if name_a in removed or name_b in removed:
            continue
        if performance_rank:
            rank_a = performance_rank.get(name_a, 0.0)
            rank_b = performance_rank.get(name_b, 0.0)
            to_remove = name_b if rank_a >= rank_b else name_a
        else:
            to_remove = name_b
        removed.add(to_remove)
        reasons.append((name_a, name_b, corr_val))
    kept = [n for n in names if n not in removed]
    return kept, reasons


def build_deduped_scorers(
    all_scorers: list[Any],
    signals_by_scorer: dict[str, list[dict[str, float]]],
    threshold: float = 0.85,
    performance_rank: dict[str, float] | None = None,
) -> list[Any]:
    corr_matrix = compute_scorer_correlations(signals_by_scorer)
    kept_names, reasons = find_redundant_scorers(corr_matrix, threshold, performance_rank)
    if reasons:
        logger.info("Scorer dedup: %d -> %d scorers (removed %d redundant)",
                    len(all_scorers), len(kept_names), len(reasons))
    name_to_scorer = {s.__name__: s for s in all_scorers}
    return [name_to_scorer[n] for n in kept_names if n in name_to_scorer]
