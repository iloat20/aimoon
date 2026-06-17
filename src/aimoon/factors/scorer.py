"""Alpha Zoo 因子 → Signal 转换器。

职责: 将截面因子值（宽表最后一行）转换为每只股票的 Signal 对象。
转换逻辑: 提取最后一行 → 截面排名 → 百分位 → 分数。
ICIR 加权和主题均衡委托给 weighting.py，质量过滤委托给 quality.py。
信号组合与加权评分由 scoring/hybrid_scorer.py 负责。
"""

from __future__ import annotations

import logging

import pandas as pd

from aimoon.factors.registry import Registry, RegistryError, SkipAlphaError
from aimoon.factors.weighting import (
    apply_theme_balancing,
    compute_icir_multipliers,
)
from aimoon.models import Signal

logger = logging.getLogger(__name__)


def compute_alpha_signals(
    registry: Registry,
    panel: dict[str, pd.DataFrame],
    target_date: pd.Timestamp | None = None,
    icir_weights: dict[str, float] | None = None,
    decay_factors: dict[str, float] | None = None,
    filter_to_ids: list[str] | None = None,
    factor_cache: dict[str, pd.DataFrame] | None = None,
    icir_temperature: float = 1.0,
    apply_theme_balance: bool = False,
    theme_target_weights: dict[str, float] | None = None,
) -> dict[str, list[Signal]]:
    """Run registered alpha factors, convert to Signal objects per stock.

    Parameters
    ----------
    registry, panel, target_date : standard alpha computation parameters.
    icir_weights : alpha_id -> ICIR weight for adaptive signal scaling.
    decay_factors : alpha_id -> decay multiplier [0.1, 1.0].
    filter_to_ids : if provided, only compute these factor IDs.
    factor_cache : pre-computed factor DataFrames.
    icir_temperature : softmax temperature (default 1.0).
    apply_theme_balance : whether to apply theme balancing.
    theme_target_weights : target theme weights (None = equal).

    Returns
    -------
    dict[str, list[Signal]] : code -> list of alpha Signals.
    """
    if not panel or "close" not in panel:
        return {}

    codes = list(panel["close"].columns)
    if not codes:
        return {}

    factor_snapshots: dict[str, pd.Series] = {}
    alpha_ids = filter_to_ids if filter_to_ids else registry.list()

    for alpha_id in alpha_ids:
        try:
            if factor_cache is not None and alpha_id in factor_cache:
                factor_df = factor_cache[alpha_id]
            else:
                factor_df = registry.compute(alpha_id, panel)
        except SkipAlphaError:
            continue
        except RegistryError as exc:
            logger.debug("Alpha %s 计算失败: %s", alpha_id, exc)
            continue
        except Exception as exc:
            logger.debug("Alpha %s 异常: %s", alpha_id, exc)
            continue

        if target_date is not None and target_date in factor_df.index:
            row = factor_df.loc[target_date]
        else:
            row = factor_df.iloc[-1]
        if row.isna().all():
            continue
        factor_snapshots[alpha_id] = row

    if not factor_snapshots:
        return {}

    icir_mults = compute_icir_multipliers(icir_weights or {}, temperature=icir_temperature)

    if apply_theme_balance and icir_weights:
        icir_mults = apply_theme_balancing(
            icir_mults,
            icir_weights,
            theme_target_weights=theme_target_weights,
        )

    signals_by_code: dict[str, list[Signal]] = {code: [] for code in codes}

    for alpha_id, snapshot in factor_snapshots.items():
        meta = registry.get(alpha_id).meta
        nickname = meta.get("nickname") or alpha_id
        themes = meta.get("theme", [])

        icir_mult = icir_mults.get(alpha_id, 1.0)
        decay_mult = decay_factors.get(alpha_id, 1.0) if decay_factors else 1.0

        ranked = snapshot.rank(pct=True, na_option="keep")

        for code in codes:
            if code not in ranked.index:
                continue
            pct_val = ranked.loc[code]
            if isinstance(pct_val, pd.Series):
                pct_val = pct_val.iloc[0]
            if pd.isna(pct_val):
                continue

            score = _pct_to_score(float(pct_val), themes)
            if score == 0:
                continue

            # Use floor instead of round to avoid truncating small scores to 0
            # Original: int(round(score * icir_mult * decay_mult)) → 0 for <0.5
            # Fixed: max(1, int(floor(...))) preserves weak-but-valid signals
            import math

            raw_score = score * icir_mult * decay_mult
            scaled_score = max(1, int(math.floor(abs(raw_score)))) * (1 if raw_score >= 0 else -1)

            signal = Signal(
                name=f"alpha_{alpha_id}",
                label=f"\u03b1:{nickname}({pct_val:.0%})",
                score=scaled_score,
                category="alpha",
            )
            signals_by_code[code].append(signal)

    return {code: sigs for code, sigs in signals_by_code.items() if sigs}


def _pct_to_score(pct: float, themes: list[str]) -> int:
    """Convert percentile rank to signal score.

    Symmetric thresholds:
    - >= 0.85: +3  |  <= 0.15: -3
    - >= 0.65: +2  |  <= 0.35: -2
    - other: 0 (no signal)

    Reversal themes flip the sign.
    """
    is_reversal = "reversal" in themes

    if pct >= 0.85:
        score = +3
    elif pct >= 0.65:
        score = +2
    elif pct <= 0.15:
        score = -3
    elif pct <= 0.35:
        score = -2
    else:
        return 0

    return -score if is_reversal else score
