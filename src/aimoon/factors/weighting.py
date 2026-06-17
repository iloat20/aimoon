"""Alpha Zoo 因子自适应加权 — ICIR softmax + 主题均衡。

职责: 因子级别的权重计算和调整。
不涉及信号生成（scorer.py）或质量过滤（quality.py）。
"""

from __future__ import annotations

import numpy as np

# ── 因子分组加权 ──


THEME_GROUPS: dict[str, list[str]] = {
    "momentum": ["academic_carhart_mom", "academic_mkt_rf"],
    "reversal": ["academic_hml"],
    "quality": ["academic_cma", "academic_rmw", "academic_smb"],
    "value": ["academic_hml"],
}


def compute_icir_multipliers(
    icir_weights: dict[str, float],
    temperature: float = 1.0,
) -> dict[str, float]:
    """Compute signal multipliers from ICIR weights using softmax transform.

    Maps ICIR weights to multipliers averaging 1.0. Temperature controls
    sharpness: higher = flatter (multipliers ~1.0), lower = sharper.
    """
    if not icir_weights:
        return {}

    max_w = max(icir_weights.values())
    if max_w <= 0:
        return {k: 1.0 for k in icir_weights}

    exp_weights: dict[str, float] = {}
    for k, v in icir_weights.items():
        try:
            exp_weights[k] = np.exp((v - max_w) / temperature)
        except (OverflowError, FloatingPointError):
            exp_weights[k] = 0.0

    total = sum(exp_weights.values())
    if total <= 0:
        return {k: 1.0 for k in icir_weights}

    n = len(exp_weights)
    return {k: v / total * n for k, v in exp_weights.items()}


def compute_theme_weights(
    icir_weights: dict[str, float],
    theme_groups: dict[str, list[str]] | None = None,
) -> dict[str, float]:
    """Compute theme weight = sum of ICIR weights within each theme group."""
    groups = theme_groups or THEME_GROUPS
    theme_weights: dict[str, float] = {}
    for theme, factor_ids in groups.items():
        theme_weights[theme] = sum(icir_weights.get(fid, 0.0) for fid in factor_ids)

    total = sum(theme_weights.values())
    if total <= 0:
        n_themes = len(theme_weights)
        return {k: 1.0 / n_themes for k in theme_weights}

    return {k: v / total for k, v in theme_weights.items()}


def apply_theme_balancing(
    icir_multipliers: dict[str, float],
    icir_weights: dict[str, float],
    theme_groups: dict[str, list[str]] | None = None,
    theme_target_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Apply theme balancing to adjust factor multipliers.

    Scales multipliers within each theme group so that theme influence
    matches target weights (default: equal). Clamp scale to [0.5, 2.0].
    """
    groups = theme_groups or THEME_GROUPS
    if theme_target_weights is None:
        n_themes = len(groups)
        theme_target_weights = {t: 1.0 / n_themes for t in groups}

    current_theme_weights = compute_theme_weights(icir_weights, groups)

    adjusted = dict(icir_multipliers)
    for theme, factor_ids in groups.items():
        current_w = current_theme_weights.get(theme, 0.0)
        target_w = theme_target_weights.get(theme, 0.0)
        if current_w <= 0:
            continue
        scale = target_w / current_w
        scale = max(0.5, min(2.0, scale))
        for fid in factor_ids:
            if fid in adjusted:
                adjusted[fid] *= scale

    return adjusted
