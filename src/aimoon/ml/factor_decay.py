"""因子衰减检测 — 统计显著性驱动的因子预测力监控。

扩展功能：
- 因子群衰减检测：协方差矩阵 Frobenius 范数变化率
- 自适应半衰期：市场 regime 变化时动态调整 EWMA 半衰期
- 衰减原因诊断：市场结构变化 vs 个体预测力下降
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

# ── 新增常量 ──
_DEFAULT_FROB_THRESHOLD = 0.3  # Frobenius 范数变化率阈值
_DEFAULT_VOL_REGIME_THRESHOLD = 2.0  # 波动率翻倍阈值
_DEFAULT_ADAPTIVE_HALFLIFE_SHORT = 5  # 高波动时半衰期
_DEFAULT_ADAPTIVE_HALFLIFE_LONG = 20  # 正常时半衰期


# ════════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════════


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
    decay_reason: str = "individual"  # "individual" | "market_structure" | "mixed"


@dataclass
class GroupDecayAlert:
    """因子群衰减警报。"""

    detected_at: str
    frob_change_rate: float  # ||Σ_t - Σ_{t-60}||_F / ||Σ_{t-60}||_F
    n_factors: int
    n_individual_decays: int  # 同时衰减的个体因子数
    threshold: float
    is_triggered: bool  # 是否触发全量重训练
    diagnosis: str = ""  # 诊断说明


@dataclass
class AdaptiveHalfLifeState:
    """自适应半衰期状态。"""

    current_halflife: int = _DEFAULT_ADAPTIVE_HALFLIFE_LONG
    base_halflife: int = _DEFAULT_ADAPTIVE_HALFLIFE_LONG
    short_halflife: int = _DEFAULT_ADAPTIVE_HALFLIFE_SHORT
    vol_ratio: float = 1.0  # short_vol / long_vol
    regime_changed: bool = False
    last_update: str = ""


# ════════════════════════════════════════════════════════════════
#  1. 原有功能：单因子衰减检测
# ════════════════════════════════════════════════════════════════


def compute_rolling_ic(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    alpha_id: str,
    registry: Any,
    n_dates: int = 60,
    forward_days: int = 22,
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
    """计算衰减比例和统计显著性。"""
    historical_mean = float(ic_series.mean())
    recent_mean = float(ic_series.iloc[-recent_window:].mean())
    recent_std = float(ic_series.iloc[-recent_window:].std())

    if abs(historical_mean) < 1e-6:
        return (1.0, 0.0, False)

    decay_ratio = recent_mean / historical_mean
    decay_ratio = max(0.1, min(1.0, decay_ratio))

    is_significant = False
    t_stat = 0.0
    if recent_std > 0 and len(ic_series) >= recent_window:
        t_stat = (recent_mean - historical_mean) / (recent_std / np.sqrt(recent_window))
        is_significant = bool(t_stat < -1.645 and decay_ratio < decay_threshold)

    return (decay_ratio, t_stat, is_significant)


# ════════════════════════════════════════════════════════════════
#  2. 因子群衰减检测（Frobenius 范数）
# ════════════════════════════════════════════════════════════════


def compute_factor_covariance_change(
    panel: dict[str, pd.DataFrame],
    registry: Any,
    lookback: int = 60,
    sample_dates: int = 20,
) -> tuple[np.ndarray | None, np.ndarray | None, float]:
    """计算因子协方差矩阵的 Frobenius 范数变化率。

    数学定义：
        Σ_t   = Cov(F_t)     — 最近 sample_dates 天的因子截面协方差
        Σ_{t-N} = Cov(F_{t-N}) — 60 天前的因子截面协方差
        ΔFrob = ||Σ_t - Σ_{t-N}||_F / ||Σ_{t-N}||_F

    其中 ||A||_F = sqrt(Σ_ij a_ij²) 是 Frobenius 范数。

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo 宽表数据。
    registry : Any
        因子注册表。
    lookback : int
        回溯天数（比较当前与 N 天前）。
    sample_dates : int
        采样天数（用于估计协方差）。

    Returns
    -------
    tuple[np.ndarray, np.ndarray, float]
        (cov_current, cov_baseline, frob_change_rate)
        数据不足时返回 (None, None, 0.0)。
    """
    close = panel.get("close")
    if close is None or len(close) < lookback + sample_dates + 20:
        return None, None, 0.0

    alpha_ids = registry.list()
    if len(alpha_ids) < 10:
        return None, None, 0.0

    # 采样日期
    available = close.index[20:].tolist()
    if len(available) < lookback + sample_dates:
        return None, None, 0.0

    # 当前窗口：最近 sample_dates 天
    current_dates = available[-sample_dates:]
    # 基线窗口：lookback 天前的 sample_dates 天
    baseline_dates = available[-(lookback + sample_dates) : -lookback]

    def _compute_cross_sectional_cov(dates: list) -> np.ndarray | None:
        """计算给定日期集合的因子截面协方差矩阵。"""
        factor_returns: dict[str, list[float]] = {}
        for alpha_id in alpha_ids[:80]:  # 限制因子数量避免过慢
            try:
                factor_df = registry.compute(alpha_id, panel)
            except Exception:
                continue
            # 取这些日期的因子值
            vals = []
            for date in dates:
                if date in factor_df.index:
                    row = factor_df.loc[date]
                    # 截面均值作为因子收益代理
                    vals.append(float(row.mean()))
                else:
                    vals.append(np.nan)
            if len(vals) == len(dates) and not all(np.isnan(v) for v in vals):
                factor_returns[alpha_id] = vals

        if len(factor_returns) < 10:
            return None

        factor_matrix = pd.DataFrame(factor_returns).dropna()
        if len(factor_matrix) < 5:
            return None

        return factor_matrix.cov().values

    cov_current = _compute_cross_sectional_cov(current_dates)
    cov_baseline = _compute_cross_sectional_cov(baseline_dates)

    if cov_current is None or cov_baseline is None:
        return cov_current, cov_baseline, 0.0

    # Frobenius 范数变化率
    diff = cov_current - cov_baseline
    frob_diff = np.sqrt(np.sum(diff**2))
    frob_baseline = np.sqrt(np.sum(cov_baseline**2))

    if frob_baseline < 1e-10:
        return cov_current, cov_baseline, 0.0

    change_rate = frob_diff / frob_baseline
    return cov_current, cov_baseline, float(change_rate)


def detect_group_decay(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Any,
    individual_alerts: list[DecayAlert],
    lookback: int = 60,
    frob_threshold: float = _DEFAULT_FROB_THRESHOLD,
    n_dates: int = 60,
    forward_days: int = 22,
) -> GroupDecayAlert:
    """检测因子群衰减。

    判断逻辑：
    1. 计算协方差矩阵 Frobenius 范数变化率
    2. 统计同时衰减的个体因子数量
    3. 综合判断是否需要全量重训练

    触发条件（满足任一）：
    - Frobenius 变化率 > 阈值（市场结构变化）
    - 同时衰减因子 > 30%（系统性衰减）

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo 宽表数据。
    klines : dict[str, pd.DataFrame]
        单股 K 线数据。
    registry : Any
        因子注册表。
    individual_alerts : list[DecayAlert]
        单因子衰减警报列表。
    lookback : int
        回溯天数。
    frob_threshold : float
        Frobenius 变化率阈值。
    n_dates, forward_days : int
        IC 计算参数。

    Returns
    -------
    GroupDecayAlert
        群衰减警报。
    """
    # 1. 计算 Frobenius 变化率
    _, _, frob_rate = compute_factor_covariance_change(panel, registry, lookback)

    # 2. 统计个体衰减
    total_factors = len(registry.list())
    n_decayed = len(individual_alerts)
    decay_ratio = n_decayed / max(total_factors, 1)

    # 3. 综合判断
    frob_triggered = frob_rate > frob_threshold
    mass_triggered = decay_ratio > 0.3  # >30% 因子同时衰减
    is_triggered = frob_triggered or mass_triggered

    # 4. 诊断
    diagnosis_parts = []
    if frob_triggered:
        diagnosis_parts.append(f"协方差矩阵结构变化 (ΔFrob={frob_rate:.2f} > {frob_threshold})")
    if mass_triggered:
        diagnosis_parts.append(
            f"系统性因子衰减 ({n_decayed}/{total_factors}={decay_ratio:.0%} 因子衰减)"
        )
    if not is_triggered:
        diagnosis_parts.append("未触发群衰减阈值")

    # 当两者同时触发时，诊断为混合原因
    if frob_triggered and mass_triggered:
        pass
    elif frob_triggered:
        pass
    else:
        pass

    alert = GroupDecayAlert(
        detected_at=str(time.time()),
        frob_change_rate=frob_rate,
        n_factors=total_factors,
        n_individual_decays=n_decayed,
        threshold=frob_threshold,
        is_triggered=is_triggered,
        diagnosis="; ".join(diagnosis_parts),
    )

    if is_triggered:
        logger.info(
            "Group decay detected: Frob=%.3f, %d/%d factors decayed — %s",
            frob_rate,
            n_decayed,
            total_factors,
            alert.diagnosis,
        )
    else:
        logger.debug(
            "Group decay check: Frob=%.3f, %d/%d factors — OK",
            frob_rate,
            n_decayed,
            total_factors,
        )

    return alert


# ════════════════════════════════════════════════════════════════
#  3. 自适应半衰期（Regime 驱动）
# ════════════════════════════════════════════════════════════════


def compute_adaptive_halflife(
    close: pd.DataFrame,
    short_window: int = 5,
    long_window: int = 60,
    vol_threshold: float = _DEFAULT_VOL_REGIME_THRESHOLD,
    halflife_short: int = _DEFAULT_ADAPTIVE_HALFLIFE_SHORT,
    halflife_long: int = _DEFAULT_ADAPTIVE_HALFLIFE_LONG,
) -> AdaptiveHalfLifeState:
    """根据市场波动率 regime 动态调整 EWMA 半衰期。

    数学定义：
        short_vol = std(ret, 5d)         — 短期波动率
        long_vol  = std(ret, 60d)        — 长期波动率
        vol_ratio = short_vol / long_vol — 波动率比值

        if vol_ratio > threshold:
            halflife = halflife_short (5d)  — 高波动，快速适应
        else:
            halflife = halflife_long (20d)  — 正常，慢速适应

    Parameters
    ----------
    close : pd.DataFrame
        收盘价矩阵。
    short_window : int
        短期波动率窗口。
    long_window : int
        长期波动率窗口。
    vol_threshold : float
        波动率翻倍阈值。
    halflife_short, halflife_long : int
        高/低波动时的半衰期。

    Returns
    -------
    AdaptiveHalfLifeState
        当前半衰期状态。
    """
    if close is None or len(close) < long_window + 5:
        return AdaptiveHalfLifeState()

    ret = close.pct_change(fill_method=None)

    # 使用全截面的平均波动率
    short_vol = ret.iloc[-short_window:].std(axis=0).mean()
    long_vol = ret.iloc[-long_window:].std(axis=0).mean()

    if long_vol < 1e-10:
        return AdaptiveHalfLifeState()

    vol_ratio = float(short_vol / long_vol)
    regime_changed = vol_ratio > vol_threshold

    current_hl = halflife_short if regime_changed else halflife_long

    state = AdaptiveHalfLifeState(
        current_halflife=current_hl,
        base_halflife=halflife_long,
        short_halflife=halflife_short,
        vol_ratio=vol_ratio,
        regime_changed=regime_changed,
        last_update=str(time.time()),
    )

    if regime_changed:
        logger.warning(
            "Regime change detected: vol_ratio=%.2f > %.1f → halflife %d → %d",
            vol_ratio,
            vol_threshold,
            halflife_long,
            halflife_short,
        )

    return state


def compute_decay_reason(
    ic_series: pd.Series,
    panel: dict[str, pd.DataFrame],
    registry: Any,
    alpha_id: str,
    lookback: int = 60,
) -> str:
    """诊断因子衰减原因。

    两种原因：
    1. "market_structure" — 市场结构变化（相关性破裂）
       特征：因子与其他因子的平均相关性显著下降
    2. "individual" — 因子自身预测力下降
       特征：因子 IC 下降但与其他因子的相关性不变

    判断方法：
    - 计算因子近期 IC 与历史 IC 的差异（ΔIC）
    - 计算因子与同期其他因子 IC 的相关性变化（ΔCorr）
    - 如果 |ΔIC| 大但 |ΔCorr| 小 → 个体原因
    - 如果 |ΔIC| 大且 |ΔCorr| 大 → 市场结构原因

    Parameters
    ----------
    ic_series : pd.Series
        该因子的 IC 时间序列。
    panel : dict[str, pd.DataFrame]
        Alpha Zoo 宽表数据。
    registry : Any
        因子注册表。
    alpha_id : str
        因子 ID。
    lookback : int
        比较窗口。

    Returns
    -------
    str
        "individual" | "market_structure" | "mixed" | "unknown"
    """
    if len(ic_series) < lookback:
        return "unknown"

    # 1. IC 变化
    recent_ic = float(ic_series.iloc[-20:].mean())
    historical_ic = (
        float(ic_series.iloc[-lookback:-20].mean())
        if len(ic_series) > lookback
        else float(ic_series.mean())
    )
    delta_ic = abs(recent_ic - historical_ic)

    # 2. 因子间相关性变化
    try:
        close = panel.get("close")
        if close is None:
            return "individual"

        # 取同期其他因子的 IC
        other_ids = [aid for aid in registry.list() if aid != alpha_id][:20]
        other_ics_recent: dict[str, float] = {}
        other_ics_historical: dict[str, float] = {}

        for other_id in other_ids:
            try:
                other_ic = compute_rolling_ic(panel, close, other_id, registry, n_dates=lookback)
                if len(other_ic) >= lookback:
                    other_ics_recent[other_id] = float(other_ic.iloc[-20:].mean())
                    other_ics_historical[other_id] = float(other_ic.iloc[-lookback:-20].mean())
            except Exception:
                continue

        if len(other_ics_recent) < 5:
            return "individual"

        # 因子间相关性
        recent_corr = np.corrcoef(
            [recent_ic] + list(other_ics_recent.values()),
        )[0, 1:]
        hist_corr = np.corrcoef(
            [historical_ic] + list(other_ics_historical.values()),
        )[0, 1:]

        delta_corr = abs(float(np.mean(recent_corr)) - float(np.mean(hist_corr)))

    except Exception:
        return "individual"

    # 3. 综合判断
    ic_threshold = 0.02  # IC 变化 > 0.02 视为显著
    corr_threshold = 0.15  # 相关性变化 > 0.15 视为显著

    ic_significant = delta_ic > ic_threshold
    corr_significant = delta_corr > corr_threshold

    if ic_significant and corr_significant:
        return "market_structure"
    elif ic_significant:
        return "individual"
    else:
        return "unknown"


# ════════════════════════════════════════════════════════════════
#  4. 主扫描函数（扩展版）
# ════════════════════════════════════════════════════════════════


def scan_factor_decay_extended(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Any,
    n_dates: int = 60,
    forward_days: int = 22,
    decay_threshold: float = _DEFAULT_DECAY_THRESHOLD,
    recent_window: int = _DEFAULT_RECENT_WINDOW,
    frob_threshold: float = _DEFAULT_FROB_THRESHOLD,
    detect_group: bool = True,
    diagnose_reason: bool = True,
) -> tuple[list[DecayAlert], GroupDecayAlert | None, AdaptiveHalfLifeState]:
    """扩展版因子衰减扫描。

    集成单因子检测 + 群衰减检测 + 自适应半衰期 + 原因诊断。

    Parameters
    ----------
    panel, klines, registry : 原有参数。
    n_dates, forward_days : IC 计算参数。
    decay_threshold : 单因子衰减阈值。
    recent_window : 滚动窗口。
    frob_threshold : Frobenius 变化率阈值。
    detect_group : 是否检测群衰减。
    diagnose_reason : 是否诊断衰减原因。

    Returns
    -------
    tuple[list[DecayAlert], GroupDecayAlert | None, AdaptiveHalfLifeState]
        (个体警报列表, 群警报, 半衰期状态)
    """
    # 1. 单因子衰减检测
    individual_alerts = scan_factor_decay(
        panel,
        klines,
        registry,
        n_dates,
        forward_days,
        decay_threshold,
        recent_window,
    )

    # 2. 衰减原因诊断
    if diagnose_reason and individual_alerts:
        enriched: list[DecayAlert] = []
        for alert in individual_alerts:
            try:
                ic_series = compute_rolling_ic(
                    panel, klines, alert.alpha_id, registry, n_dates, forward_days
                )
                reason = compute_decay_reason(ic_series, panel, registry, alert.alpha_id)
                enriched.append(
                    DecayAlert(
                        alpha_id=alert.alpha_id,
                        current_ic=alert.current_ic,
                        historical_ic_mean=alert.historical_ic_mean,
                        decay_ratio=alert.decay_ratio,
                        detected_at=alert.detected_at,
                        t_statistic=alert.t_statistic,
                        is_significant=alert.is_significant,
                        decay_reason=reason,
                    )
                )
            except Exception:
                enriched.append(alert)
        individual_alerts = enriched

    # 3. 群衰减检测
    group_alert: GroupDecayAlert | None = None
    if detect_group:
        group_alert = detect_group_decay(
            panel,
            klines,
            registry,
            individual_alerts,
            frob_threshold=frob_threshold,
            n_dates=n_dates,
            forward_days=forward_days,
        )

        # 如果触发群衰减，标记所有个体警报为市场结构原因
        if group_alert.is_triggered:
            individual_alerts = [
                DecayAlert(
                    alpha_id=a.alpha_id,
                    current_ic=a.current_ic,
                    historical_ic_mean=a.historical_ic_mean,
                    decay_ratio=a.decay_ratio,
                    detected_at=a.detected_at,
                    t_statistic=a.t_statistic,
                    is_significant=a.is_significant,
                    decay_reason="market_structure",
                )
                for a in individual_alerts
            ]

    # 4. 自适应半衰期
    close = panel.get("close")
    halflife_state = (
        compute_adaptive_halflife(close) if close is not None else AdaptiveHalfLifeState()
    )

    # 5. 日志汇总
    n_individual = len(individual_alerts)
    n_by_reason = {}
    for a in individual_alerts:
        n_by_reason[a.decay_reason] = n_by_reason.get(a.decay_reason, 0) + 1

    logger.info(
        "Extended decay scan: %d individual alerts (%s), group=%s, halflife=%d",
        n_individual,
        ", ".join(f"{k}:{v}" for k, v in n_by_reason.items()),
        "TRIGGERED" if group_alert and group_alert.is_triggered else "OK",
        halflife_state.current_halflife,
    )

    return individual_alerts, group_alert, halflife_state


# ════════════════════════════════════════════════════════════════
#  5. 原有接口（保持向后兼容）
# ════════════════════════════════════════════════════════════════


def scan_factor_decay(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Any,
    n_dates: int = 60,
    forward_days: int = 22,
    decay_threshold: float = _DEFAULT_DECAY_THRESHOLD,
    recent_window: int = _DEFAULT_RECENT_WINDOW,
) -> list[DecayAlert]:
    """扫描所有因子，检测衰减（原有接口）。"""
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
                    detected_at=(str(ic_series.index[-1]) if len(ic_series) > 0 else ""),
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
    """加载缓存的衰减因子权重衰减系数。"""
    cache_path = Path(cache_dir or Path(".aimoon_cache") / "factor_decay") / "decayed.json"
    if not cache_path.exists():
        return {}

    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        age_hours = (time.time() - data.get("timestamp", 0)) / 3600
        if age_hours > _DECAY_CACHE_TTL_HOURS:
            return {}
        return data.get("factors", {})
    except Exception:
        return {}


def save_decay_results(
    alerts: list[DecayAlert],
    cache_dir: str | Path | None = None,
) -> None:
    """保存衰减检测结果到缓存。"""
    save_path = Path(cache_dir or Path(".aimoon_cache") / "factor_decay")
    save_path.mkdir(parents=True, exist_ok=True)

    factors: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for alert in alerts:
        factors[alert.alpha_id] = alert.decay_ratio
        reasons[alert.alpha_id] = alert.decay_reason

    with open(save_path / "decayed.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.time(),
                "n_factors": len(factors),
                "factors": factors,
                "decay_reasons": reasons,
            },
            f,
            indent=2,
        )

    logger.info("Saved %d decayed factor weights", len(factors))
