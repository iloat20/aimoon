"""因子衰减检测 — CUSUM 算法监控因子预测力突变。

当因子的 IC 突然下降时，自动降权或移除。
基于 CUSUM (Cumulative Sum) 变点检测算法。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)

_DECAY_CACHE_DIR = Path(".aimoon_cache") / "factor_decay"


@dataclass(frozen=True)
class DecayAlert:
    """因子衰减警报。"""

    alpha_id: str
    current_ic: float
    historical_ic_mean: float
    decay_ratio: float  # current / historical
    detected_at: str


def detect_decay_cusum(
    ic_series: pd.Series,
    delta: float = 0.01,
    threshold: float = 0.05,
) -> list[int]:
    """CUSUM 变点检测：检测 IC 序列中的突变点。

    Parameters
    ----------
    ic_series : pd.Series
        因子的滚动 IC 时间序列。
    delta : float
        允许的正常波动范围。
    threshold : float
        累积和超过此阈值时触发警报。

    Returns
    -------
    list[int]
        检测到的变点位置索引。
    """
    if len(ic_series) < 10:
        return []

    ic_mean = float(ic_series.mean())
    changepoints: list[int] = []
    s_h = 0.0

    for i, ic in enumerate(ic_series):
        s_h = max(0, s_h + ic - ic_mean - delta)
        if s_h > threshold:
            changepoints.append(i)
            s_h = 0.0  # Reset

    return changepoints


def compute_rolling_ic(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    alpha_id: str,
    registry: object,
    n_dates: int = 60,
    forward_days: int = 5,
) -> pd.Series:
    """计算单个因子的滚动 IC 序列。"""

    close = panel.get("close")
    if close is None or len(close) < n_dates + forward_days + 20:
        return pd.Series(dtype=float)

    available = close.index[20:].tolist()
    if len(available) < n_dates:
        n_dates = len(available)
    step = max(1, len(available) // n_dates)
    dates = [available[i * step] for i in range(n_dates)]

    ic_values: dict[str, float] = {}

    # Compute factor once (not per date)
    try:
        factor_df = registry.compute(alpha_id, panel)
    except Exception:
        return pd.Series(dtype=float)

    for date in dates:
        if date not in factor_df.index:
            continue

        row = factor_df.loc[date]
        # 修复前瞻偏差：使用已实现收益（过去 forward_days 天的收益）
        # 而不是前瞻收益（未来 forward_days 天的收益）
        from aimoon.ml.label_engine import generate_realized_returns
        labels = generate_realized_returns(klines, date, forward_days)
        common = row.dropna().index.intersection(labels.index)
        if len(common) < 10:
            continue

        try:
            ic, _ = spearmanr(row[common].values, labels[common].values)
            if not np.isnan(ic):
                ic_values[str(date)] = float(ic)
        except Exception:
            continue

    return pd.Series(ic_values, dtype=float)


def scan_factor_decay(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: object,
    n_dates: int = 60,
    forward_days: int = 5,
    decay_threshold: float = 0.5,
) -> list[DecayAlert]:
    """扫描所有因子，检测衰减。

    Parameters
    ----------
    decay_threshold : float
        当前 IC / 历史均值 IC 低于此值时触发警报。

    Returns
    -------
    list[DecayAlert]
        衰减警报列表。
    """
    alerts: list[DecayAlert] = []
    alpha_ids = registry.list()

    for alpha_id in alpha_ids:
        ic_series = compute_rolling_ic(
            panel,
            klines,
            alpha_id,
            registry,
            n_dates,
            forward_days,
        )
        if len(ic_series) < 10:
            continue

        # 检测变点（CUSUM 算法）
        # 注意：当前仅使用均值比率判断衰减，changepoints 结果未使用
        # TODO: 利用 changepoints 做更精细的衰减判断（如检测最近是否有变点）
        changepoints = detect_decay_cusum(ic_series)

        # 计算衰减比例
        historical_mean = float(ic_series.mean())
        recent_mean = float(ic_series.iloc[-5:].mean()) if len(ic_series) >= 5 else historical_mean

        if abs(historical_mean) < 1e-6:
            continue

        decay_ratio = recent_mean / historical_mean
        if historical_mean < 0:
            decay_ratio = 2.0 - decay_ratio  # for negative IC, ratio > 1 means worsened

        if decay_ratio < decay_threshold:
            alerts.append(
                DecayAlert(
                    alpha_id=alpha_id,
                    current_ic=recent_mean,
                    historical_ic_mean=historical_mean,
                    decay_ratio=decay_ratio,
                    detected_at=str(ic_series.index[-1]) if len(ic_series) > 0 else "",
                )
            )

    if alerts:
        logger.info("Factor decay detected: %d factors", len(alerts))
        for alert in alerts[:5]:
            logger.info(
                "  %s: IC %.4f → %.4f (ratio=%.2f)",
                alert.alpha_id,
                alert.historical_ic_mean,
                alert.current_ic,
                alert.decay_ratio,
            )

    return alerts


def get_decayed_factors(cache_dir: str | Path | None = None) -> dict[str, float]:
    """加载缓存的衰减因子权重衰减系数。

    Returns
    -------
    dict[str, float]
        alpha_id -> decay_factor (0-1). 1.0 = no decay, 0.5 = half weight.
    """
    cache_path = Path(cache_dir or _DECAY_CACHE_DIR) / "decayed.json"
    if not cache_path.exists():
        return {}

    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        age_hours = (time.time() - data.get("timestamp", 0)) / 3600
        if age_hours > 168:  # 7 days
            return {}
        return data.get("factors", {})
    except Exception:
        return {}


def save_decay_results(
    alerts: list[DecayAlert],
    cache_dir: str | Path | None = None,
) -> None:
    """保存衰减检测结果到缓存。"""
    save_path = Path(cache_dir or _DECAY_CACHE_DIR)
    save_path.mkdir(parents=True, exist_ok=True)

    factors: dict[str, float] = {}
    for alert in alerts:
        # 衰减系数：ratio 越低，衰减越强
        factors[alert.alpha_id] = max(0.1, alert.decay_ratio)

    with open(save_path / "decayed.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.time(),
                "n_factors": len(factors),
                "factors": factors,
            },
            f,
            indent=2,
        )

    logger.info("Saved %d decayed factor weights", len(factors))
