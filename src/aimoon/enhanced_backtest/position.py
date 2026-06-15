"""Position sizing and weight computation.

Extracts position management logic from EnhancedBacktestEngine for reusability.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.risk import kelly_criterion


def compute_position_weights(
    trades: list,
    max_positions: int,
    klines: dict[str, pd.DataFrame],
    scores: dict[str, float],
    use_kelly: bool = True,
    regime: str = "sideways",
) -> dict[str, float]:
    """Compute position weights: Kelly + volatility + score-proportional.

    1. Kelly criterion for base sizing (based on trade history)
    2. Regime-adaptive Kelly scaling (bear/half, bull/full)
    3. Volatility targeting: scale down when market is volatile
    4. Per-stock vol adjustment: less volatile stocks get more weight
    5. Score-proportional adjustment: higher-scoring stocks get more weight

    Parameters
    ----------
    trades : list
        Historical trades with .return_pct attribute.
    max_positions : int
        Maximum number of concurrent positions.
    klines : dict[str, pd.DataFrame]
        Stock code -> kline DataFrame.
    scores : dict[str, float]
        Stock code -> scoring value.
    use_kelly : bool
        Whether to use Kelly criterion (requires >= 20 trades).
    regime : str
        Market regime for adaptive Kelly scaling.

    Returns
    -------
    dict[str, float]
        Stock code -> position weight (normalized, max 0.20 per stock).
    """
    equal_weight = 1.0 / max_positions

    # ── 波动率目标：组合级调整 ──
    vol_scale = 1.0
    target_vol = 0.20  # 目标年化波动率 20%
    avg_vol = 0.0
    vol_cache: dict[str, float] = {}
    realized_vols = []
    for code in scores:
        df = klines.get(code)
        if df is not None and len(df) >= 20:
            rv = float(df["close"].pct_change().iloc[-20:].std() * np.sqrt(252))
            vol_cache[code] = rv
            realized_vols.append(rv)
    if realized_vols:
        avg_vol = float(np.mean(realized_vols))
        if avg_vol > 0.01:
            vol_scale = min(2.0, max(0.3, target_vol / avg_vol))

    # ── Score-proportional adjustment ──
    avg_score = np.mean(list(scores.values())) if scores else 1.0
    score_scale = {
        code: max(0.5, min(1.5, score / avg_score)) for code, score in scores.items()
    }

    if not use_kelly or len(trades) < 20:
        base = equal_weight * vol_scale
        weights: dict[str, float] = {
            code: float(min(base * score_scale[code], 0.20)) for code in scores
        }
        total = sum(weights.values())
        if total > 0:
            weights = {c: float(min(v / total, 0.20)) for c, v in weights.items()}
        return weights

    # ── Kelly 基础仓位 ──
    win_rate = sum(1 for t in trades if t.return_pct > 0) / len(trades)
    wins = [t.return_pct for t in trades if t.return_pct > 0]
    losses = [abs(t.return_pct) for t in trades if t.return_pct < 0]
    avg_win = np.mean(wins) if wins else 0.0
    avg_loss = np.mean(losses) if losses else 1.0

    kelly = kelly_criterion(win_rate, avg_win, avg_loss)
    if kelly <= 0:
        return {code: equal_weight * vol_scale for code in scores}

    # ── Regime-adaptive Kelly scaling ──
    regime_kelly_scale = {
        "bull": 1.0,
        "sideways": 0.85,  # 从 0.7 提高，增加震荡市仓位利用率
        "bear": 0.3,
        "high_volatility": 0.5,
        "crisis": 0.1,     # 补充缺失的 crisis 条目
    }
    kelly *= regime_kelly_scale.get(regime, 0.85)

    # ── 个股仓位 ──
    kelly_weights: dict[str, float] = {}
    for code in scores:
        stock_vol = vol_cache.get(code)
        if stock_vol is not None:
            vol_adj = 1.0 / max(stock_vol / avg_vol, 0.5) if avg_vol > 0 else 1.0
            w = kelly * 0.5 * vol_scale * vol_adj * score_scale[code]
        else:
            w = kelly * 0.5 * vol_scale * score_scale[code]
        kelly_weights[code] = float(max(w, 0.02))

    total = sum(kelly_weights.values())
    if total > 0:
        kelly_weights = {
            c: float(min(v / total, 0.20)) for c, v in kelly_weights.items()
        }
    return kelly_weights
