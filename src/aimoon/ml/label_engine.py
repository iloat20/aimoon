"""Generate forward-return labels for ML training."""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# 默认裁剪范围（百分比收益率），覆盖A股月度涨跌极端情况
_DEFAULT_CLIP_RANGE: tuple[float, float] = (-30.0, 30.0)


def generate_labels(
    klines: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    forward_days: int = 22,
    purge_days: int = 0,  # 默认改为 0，因为 purge 在 CV 分割层面处理
    clip_range: tuple[float, float] | None = _DEFAULT_CLIP_RANGE,
) -> pd.Series:
    """Generate forward N-day return labels at target_date.

    标签计算区间：target_date + 1 到 target_date + 1 + forward_days
    这反映了实际交易场景：信号在 T 日生成，交易在 T+1 开始

    Args:
        klines: 股票K线数据字典
        target_date: 目标日期（信号生成日）
        forward_days: 前瞻天数（持仓天数，默认 22=月度）
        purge_days: 清洗天数（默认 0，因为 purge 在 CV 分割层面处理）
        clip_range: 极端值裁剪范围 (low, high)，None 表示不裁剪。

    Returns:
        pd.Series: index=stock code, value=forward return %

    Example:
        如果 target_date = 2024-01-01, forward_days = 22
        那么收益计算区间是：2024-01-02 到 2024-01-24
        （T+1 开始，持有 forward_days 天）
    """
    labels: dict[str, float] = {}
    for code, df in klines.items():
        if df is None or "close" not in df.columns:
            continue
        dates = df.index.sort_values()
        try:
            idx = dates.get_loc(target_date)
        except (KeyError, TypeError):
            continue
        # idx 可能为 slice（重复日期索引），此时 +1 会引发 TypeError
        if isinstance(idx, slice):
            logger.warning(
                "Duplicate date index for %s at %s, skipping label",
                code,
                target_date,
            )
            continue

        # 标签反映实际交易收益：T+1 入场，T+1+forward_days 出场
        # purge_days 在 CV 分割层面处理，不在标签层面
        start_idx = idx + 1  # T+1（次日开盘入场）
        end_idx = start_idx + forward_days  # T+1+forward_days

        if start_idx >= len(dates) or end_idx >= len(dates):
            continue

        # 修复前瞻偏差：使用开盘价计算标签，与实际交易一致
        if "open" in df.columns:
            close_start = float(df.loc[dates[start_idx], "open"])
        else:
            close_start = float(df.loc[dates[start_idx], "close"])  # fallback

        if "open" in df.columns:
            close_end = float(df.loc[dates[end_idx], "open"])
        else:
            close_end = float(df.loc[dates[end_idx], "close"])  # fallback

        if close_start <= 0:
            continue

        ret = (close_end - close_start) / close_start * 100.0

        # H4: 极端值裁剪 — 在标签生成阶段处理，避免极端值主导训练
        if clip_range is not None:
            ret = max(clip_range[0], min(clip_range[1], ret))

        labels[code] = ret

    return pd.Series(labels)


def generate_rank_labels(
    klines: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    forward_days: int = 22,
    purge_days: int = 5,
) -> pd.Series:
    """Generate cross-sectional rank labels (0-1) at target_date.

    Same as generate_labels but normalized to percentile rank across stocks.
    Useful for ranking-based XGBoost objectives.

    Args:
        klines: 股票K线数据字典
        target_date: 目标日期
        forward_days: 前瞻天数
        purge_days: 清洗天数（避免信息泄露）

    Returns:
        pd.Series: index=stock code, value=percentile rank (0-1)
    """
    labels = generate_labels(klines, target_date, forward_days, purge_days)
    if len(labels) < 5:
        return labels
    return labels.rank(pct=True)


def generate_binary_labels(
    klines: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    forward_days: int = 22,
    purge_days: int = 5,
) -> pd.Series:
    """Generate binary labels: 1 if above median forward return, else 0.

    Args:
        klines: 股票K线数据字典
        target_date: 目标日期
        forward_days: 前瞻天数
        purge_days: 清洗天数（避免信息泄露）

    Returns:
        pd.Series: index=stock code, value=0 or 1
    """
    labels = generate_labels(klines, target_date, forward_days, purge_days)
    if len(labels) < 5:
        return labels
    median = labels.median()
    return (labels >= median).astype(int)


def generate_reversal_labels(
    klines: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    forward_days: int = 22,
    lookback_days: int = 20,
    clip_range: tuple[float, float] | None = (-30.0, 30.0),
) -> pd.Series:
    """Generate reversal-based labels: predict forward reversal returns.

    The reversal strategy bets that recent winners will underperform and
    recent losers will outperform in the forward window.

    Label = forward_return - alpha * past_return
    - Positive label: stock is expected to go up (buy signal)
    - Negative label: stock is expected to go down (avoid signal)

    The past_return component teaches the model to bet against momentum:
    stocks with high past returns get lower labels, stocks with low past
    returns get higher labels, while still incorporating actual forward returns.

    IMPORTANT: Features at target_date T use data available at or before T.
    Labels use forward returns from T+1 to T+1+forward_days, ensuring no
    look-ahead bias. The past_return is known at T (not future info).

    Args:
        klines: stock K-line data dict
        target_date: signal generation date T
        forward_days: forward holding days for label computation
        lookback_days: lookback period for computing past returns (default 20)
        clip_range: extreme value clip range

    Returns:
        pd.Series: index=stock code, value=reversal label (positive = buy)
    """
    labels: dict[str, float] = {}
    for code, df in klines.items():
        if df is None or "close" not in df.columns:
            continue
        dates = df.index.sort_values()
        try:
            idx = dates.get_loc(target_date)
        except (KeyError, TypeError):
            continue
        if isinstance(idx, slice):
            continue

        # Past return over lookback_days (known at time T, no look-ahead)
        past_start_idx = max(0, idx - lookback_days)
        if past_start_idx >= idx:
            continue
        past_start_price = float(df.loc[dates[past_start_idx], "close"])
        current_price = float(df.loc[dates[idx], "close"])

        if past_start_price <= 0:
            continue

        past_return = (current_price - past_start_price) / past_start_price * 100.0

        # Forward return: T+1 to T+1+forward_days (the target to predict)
        start_idx = idx + 1
        end_idx = start_idx + forward_days
        if start_idx >= len(dates) or end_idx >= len(dates):
            continue

        if "open" in df.columns:
            fwd_start = float(df.loc[dates[start_idx], "open"])
            fwd_end = float(df.loc[dates[end_idx], "open"])
        else:
            fwd_start = float(df.loc[dates[start_idx], "close"])
            fwd_end = float(df.loc[dates[end_idx], "close"])

        if fwd_start <= 0:
            continue

        fwd_return = (fwd_end - fwd_start) / fwd_start * 100.0

        # Reversal label: forward return minus momentum penalty
        # Reduced from 0.3 to 0.1: original penalty was too aggressive,
        # contradicting the momentum-biased universe selection (pre_sort_universe
        # selects stocks with high 60-day returns, then labels punish them)
        alpha = 0.1  # weight of momentum penalty (was 0.3)
        label = fwd_return - alpha * past_return

        if clip_range is not None:
            label = max(clip_range[0], min(clip_range[1], label))

        labels[code] = label

    return pd.Series(labels)


def validate_label_quality(
    labels: pd.Series,
    min_stocks: int = 10,
) -> dict:
    """验证标签质量

    Args:
        labels: 标签序列
        min_stocks: 最少股票数

    Returns:
        dict: 质量指标
    """
    if len(labels) < min_stocks:
        return {
            "valid": False,
            "reason": f"股票数不足：{len(labels)} < {min_stocks}",
            "count": len(labels),
        }

    # 检查标签分布
    mean_val = labels.mean()
    std_val = labels.std()
    min_val = labels.min()
    max_val = labels.max()

    # 检查是否有异常值（金融收益厚尾分布，使用 3×IQR 更合理）
    q1 = labels.quantile(0.25)
    q3 = labels.quantile(0.75)
    iqr = q3 - q1
    outlier_count = ((labels < q1 - 3.0 * iqr) | (labels > q3 + 3.0 * iqr)).sum()

    return {
        "valid": True,
        "count": len(labels),
        "mean": mean_val,
        "std": std_val,
        "min": min_val,
        "max": max_val,
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "outlier_count": outlier_count,
        "outlier_pct": outlier_count / len(labels) * 100,
    }


def cross_sectional_standardize(
    returns: pd.Series,
    dates: pd.Series,
) -> pd.Series:
    """Cross-sectional standardization: remove market factor per date.

    For each date, subtract cross-sectional mean and divide by std.
    This isolates stock-specific alpha from market-wide beta.

    Parameters
    ----------
    returns : pd.Series
        Forward returns (percent).
    dates : pd.Series
        Date for each observation (same index as returns).

    Returns
    -------
    pd.Series
        Standardized returns (z-scores).
    """
    result = returns.copy().astype(float)
    for date in dates.unique():
        mask = dates == date
        r = result[mask]
        mean = r.mean()
        std = r.std()
        if std > 1e-8:
            result[mask] = (r - mean) / std
        else:
            result[mask] = 0.0
    return result


def generate_realized_returns(
    klines: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    lookback_days: int = 5,
) -> pd.Series:
    """生成已实现收益：从 target_date - lookback_days 到 target_date 的收益。

    用于实时自适应权重计算和因子衰减检测，避免使用前瞻收益。

    Args:
        klines: 股票K线数据字典
        target_date: 目标日期
        lookback_days: 回看天数（默认 5）

    Returns:
        pd.Series: index=stock code, value=realized return %

    Example:
        如果 target_date = 2024-01-10, lookback_days = 5
        那么收益计算区间是：2024-01-05 到 2024-01-10
        （都是过去的数据，不存在前瞻偏差）
    """
    labels: dict[str, float] = {}
    for code, df in klines.items():
        if df is None or "close" not in df.columns:
            continue
        dates = df.index.sort_values()
        try:
            idx = dates.get_loc(target_date)
        except (KeyError, TypeError):
            continue

        # 使用过去的数据：target_date - lookback_days 到 target_date
        start_idx = idx - lookback_days
        if start_idx < 0:
            continue

        close_start = float(df.loc[dates[start_idx], "close"])
        close_end = float(df.loc[dates[idx], "close"])
        if close_start <= 0:
            continue

        labels[code] = (close_end - close_start) / close_start * 100.0

    return pd.Series(labels)
