"""因子衰减检测 — 统计显著性驱动的因子预测力监控。

当因子的 IC 显著下降时，自动降权或移除。
使用前瞻收益 (generate_labels) 与 ICIR 加权保持一致，
通过滚动窗口 + 标准误进行统计显著性判断，避免小样本误判。
"""

from __future__ import annotations

import json
import logging
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import ConstantInputWarning, spearmanr

logger = logging.getLogger(__name__)

_DEFAULT_RECENT_WINDOW = 20
_DEFAULT_DECAY_THRESHOLD = 0.5
_MIN_IC_SERIES_LEN = 10
_DECAY_CACHE_TTL_HOURS = 168  # 7 days


@dataclass(frozen=True)
class DecayAlert:
    """因子衰减警报。"""

    alpha_id: str
    current_ic: float
    historical_ic_mean: float
    decay_ratio: float  # current / historical, clamped to [0.1, 1.0]
    detected_at: str
    t_statistic: float = 0.0  # 显著性检验统计量
    is_significant: bool = False  # 是否通过显著性检验


def compute_rolling_ic(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    alpha_id: str,
    registry: Any,
    n_dates: int = 60,
    forward_days: int = 5,
) -> pd.Series:
    """计算单个因子的滚动 IC 序列。

    使用前瞻收益 (generate_labels) 与 ICIR weighter 保持一致，
    两者都衡量因子对未来收益的预测能力。

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo 宽表数据。
    klines : dict[str, pd.DataFrame]
        单股 K 线数据。
    alpha_id : str
        因子 ID。
    registry : Any
        因子注册表。
    n_dates : int
        采样日期数。
    forward_days : int
        前瞻天数。

    Returns
    -------
    pd.Series
        日期 -> IC 值。
    """
    close = panel.get("close")
    if close is None or len(close) < n_dates + forward_days + 20:
        return pd.Series(dtype=float)

    available = close.index[20:].tolist()
    if len(available) < n_dates:
        n_dates = len(available)
    step = max(1, len(available) // n_dates)
    dates = [available[i * step] for i in range(n_dates)]

    ic_values: dict[str, float] = {}

    try:
        factor_df = registry.compute(alpha_id, panel)
    except Exception:
        return pd.Series(dtype=float)

    from aimoon.ml.label_engine import generate_labels

    for date in dates:
        if date not in factor_df.index:
            continue

        row = factor_df.loc[date]
        labels = generate_labels(klines, date, forward_days)
        common = row.dropna().index.intersection(labels.index)
        if len(common) < 10:
            continue

        factor_vals = row[common].values
        label_vals = labels[common].values

        if np.std(factor_vals) == 0 or np.std(label_vals) == 0:
            continue

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=ConstantInputWarning)
            try:
                ic, _ = spearmanr(factor_vals, label_vals)
                if not np.isnan(ic):
                    ic_values[str(date)] = float(ic)
            except Exception:
                continue

    return pd.Series(ic_values, dtype=float)


def _compute_decay_significance(
    ic_series: pd.Series,
    recent_window: int,
    decay_threshold: float,
) -> tuple[float, float, bool]:
    """计算衰减比例和统计显著性。

    Returns
    -------
    tuple[float, float, bool]
        (decay_ratio, t_statistic, is_significant)
    """
    historical_mean = float(ic_series.mean())
    recent_mean = float(ic_series.iloc[-recent_window:].mean())
    recent_std = float(ic_series.iloc[-recent_window:].std())

    if abs(historical_mean) < 1e-6:
        return (1.0, 0.0, False)

    # 统一衰减比例计算，边界保护到 [0.1, 1.0]
    decay_ratio = recent_mean / historical_mean
    decay_ratio = max(0.1, min(1.0, decay_ratio))

    # 显著性检验: t = (recent_mean - historical_mean) / (recent_std / sqrt(n))
    # t < -1.645 表示在 5% 水平下单侧显著下降
    is_significant = False
    t_stat = 0.0
    if recent_std > 0 and len(ic_series) >= recent_window:
        t_stat = (recent_mean - historical_mean) / (recent_std / np.sqrt(recent_window))
        is_significant = bool(t_stat < -1.645 and decay_ratio < decay_threshold)

    return (decay_ratio, t_stat, is_significant)


def scan_factor_decay(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Any,
    n_dates: int = 60,
    forward_days: int = 5,
    decay_threshold: float = _DEFAULT_DECAY_THRESHOLD,
    recent_window: int = _DEFAULT_RECENT_WINDOW,
) -> list[DecayAlert]:
    """扫描所有因子，检测衰减。

    使用 20 天滚动窗口 + 标准误进行统计显著性判断，
    避免小样本噪声导致的误判。

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo 宽表数据。
    klines : dict[str, pd.DataFrame]
        单股 K 线数据。
    registry : Any
        因子注册表。
    n_dates : int
        采样日期数。
    forward_days : int
        前瞻天数。
    decay_threshold : float
        衰减比例阈值，低于此值且显著时触发警报。
    recent_window : int
        滚动窗口大小（天数）。

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
        if len(ic_series) < _MIN_IC_SERIES_LEN:
            continue

        # 确保窗口不超过序列长度
        actual_window = min(recent_window, len(ic_series))
        decay_ratio, t_stat, is_significant = _compute_decay_significance(
            ic_series, actual_window, decay_threshold
        )

        if is_significant:
            recent_mean = float(ic_series.iloc[-actual_window:].mean())
            historical_mean = float(ic_series.mean())
            alerts.append(
                DecayAlert(
                    alpha_id=alpha_id,
                    current_ic=recent_mean,
                    historical_ic_mean=historical_mean,
                    decay_ratio=decay_ratio,
                    detected_at=(
                        str(ic_series.index[-1]) if len(ic_series) > 0 else ""
                    ),
                    t_statistic=t_stat,
                    is_significant=is_significant,
                )
            )

    if alerts:
        logger.info("Factor decay detected: %d factors", len(alerts))
        for alert in alerts[:5]:
            logger.info(
                "  %s: IC %.4f → %.4f (ratio=%.2f, t=%.2f)",
                alert.alpha_id,
                alert.historical_ic_mean,
                alert.current_ic,
                alert.decay_ratio,
                alert.t_statistic,
            )

    return alerts


def get_decayed_factors(
    cache_dir: str | Path | None = None,
) -> dict[str, float]:
    """加载缓存的衰减因子权重衰减系数。

    Parameters
    ----------
    cache_dir : str | Path | None
        缓存目录路径。默认为 Path(".aimoon_cache") / "factor_decay"。

    Returns
    -------
    dict[str, float]
        alpha_id -> decay_factor (0.1-1.0)。1.0 = 无衰减，0.5 = 半权。
    """
    cache_path = Path(cache_dir or Path(".aimoon_cache") / "factor_decay") / "decayed.json"
    if not cache_path.exists():
        return {}

    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        age_hours = (time.time() - data.get("timestamp", 0)) / 3600
        if age_hours > _DECAY_CACHE_TTL_HOURS:  # 7 days
            return {}
        return data.get("factors", {})
    except Exception:
        return {}


def save_decay_results(
    alerts: list[DecayAlert],
    cache_dir: str | Path | None = None,
) -> None:
    """保存衰减检测结果到缓存。

    Parameters
    ----------
    alerts : list[DecayAlert]
        衰减警报列表。
    cache_dir : str | Path | None
        缓存目录路径。默认为 Path(".aimoon_cache") / "factor_decay"。
    """
    save_path = Path(cache_dir or Path(".aimoon_cache") / "factor_decay")
    save_path.mkdir(parents=True, exist_ok=True)

    factors: dict[str, float] = {}
    for alert in alerts:
        # 衰减系数已在 _compute_decay_significance 中 clamp 到 [0.1, 1.0]
        factors[alert.alpha_id] = alert.decay_ratio

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
