"""Regime-adaptive scoring adjustments.

Apply market-regime-specific transformations to scored stock lists.
Used by CLI main flow to adjust rankings based on detected market regime.
"""

from __future__ import annotations

from aimoon.models import ScoredStock


def apply_regime_to_list(
    results: list[ScoredStock],
    regime: object | None,
) -> list[ScoredStock]:
    """Adjust scored stock list based on market regime.

    Currently a pass-through; regime-adaptive weights are already
    applied during scoring via ``hybrid_scorer.get_regime_config``.
    This function is reserved for future regime-based re-ranking
    (e.g., boosting defensive stocks in bear markets).

    Args:
        results: Scored stock list (already sorted by score).
        regime: Market regime object (e.g., MarketRegime) or None.

    Returns:
        Adjusted list (currently unchanged).
    """
    return results
