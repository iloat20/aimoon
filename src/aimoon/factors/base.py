"""Alpha Zoo 基础算子 — 16 个纯函数，作用于宽表 DataFrame。

所有算子作用于 **宽表** pd.DataFrame（index=交易日期，columns=股票代码）。
因子计算返回相同形状的 DataFrame — 原始分数，NaN 保留（预热/缺失数据）；
禁止 +/-inf（registry 会拒绝）。

NaN 策略：所有算子传播 NaN；不使用 fillna(0)。常数窗口的 ts_corr/ts_cov 返回 NaN。

Lookahead 禁令：delta(df, d) 要求 d >= 1；不允许负偏移。

移植自 HKUDS/Vibe-Trading (MIT)，保留原始 MIT 许可证。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── 内部工具 ──

def _as_float(df: pd.DataFrame) -> pd.DataFrame:
    if df.dtypes.eq(np.float64).all():
        return df
    return df.astype(np.float64)


# ── 截面算子（每行，跨股票） ──

def rank(df: pd.DataFrame) -> pd.DataFrame:
    """截面百分位排名（axis=1, ties=average, pct=True）。NaN 保留。"""
    return df.rank(axis=1, method="average", pct=True, na_option="keep")


def scale(df: pd.DataFrame, a: float = 1.0) -> pd.DataFrame:
    """每行 L1 归一化，使绝对值之和 = a。零/NaN 行变为 NaN。"""
    df = _as_float(df)
    abs_sum = df.abs().sum(axis=1, skipna=True)
    abs_sum = abs_sum.where(abs_sum > 0)  # zero → NaN
    return df.mul(a).div(abs_sum, axis=0)


def signed_power(df: pd.DataFrame, p: float) -> pd.DataFrame:
    """sign(df) * |df|^p — 保留符号，不会产生复数。"""
    arr = df.to_numpy(dtype=np.float64, na_value=np.nan)
    out = np.sign(arr) * np.power(np.abs(arr), p)
    return pd.DataFrame(out, index=df.index, columns=df.columns)


def safe_div(a: pd.DataFrame, b: pd.DataFrame, eps: float = 1e-12) -> pd.DataFrame:
    """安全除法：a / (b + eps * sign(b))。b=0 或 NaN 时结果为 NaN，不会 inf。"""
    a = _as_float(a)
    b = _as_float(b)
    sign = np.sign(b.to_numpy(dtype=np.float64, na_value=np.nan))
    denom_arr = b.to_numpy(dtype=np.float64, na_value=np.nan) + eps * sign
    denom = pd.DataFrame(denom_arr, index=b.index, columns=b.columns)
    result = a.div(denom)
    return result.replace([np.inf, -np.inf], np.nan)


# ── 时序算子（每列，滚动窗口） ──

def ts_rank(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动排名（最后一个值在 n 窗口内的排名），结果在 [0, 1]。"""
    if n < 1:
        raise ValueError(f"ts_rank window must be >= 1, got {n}")

    def _last_rank(arr: np.ndarray) -> float:
        if np.isnan(arr).all():
            return np.nan
        last = arr[-1]
        if np.isnan(last):
            return np.nan
        valid = arr[~np.isnan(arr)]
        if valid.size == 0:
            return np.nan
        less = (valid < last).sum()
        eq = (valid == last).sum()
        rank_avg = less + 0.5 * (eq + 1)
        return float(rank_avg / valid.size)

    return df.rolling(window=n, min_periods=n).apply(_last_rank, raw=True)


def ts_corr(x: pd.DataFrame, y: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动 Pearson 相关系数（每列），min_periods=n。常数窗口 → NaN。"""
    if n < 2:
        raise ValueError(f"ts_corr window must be >= 2, got {n}")
    x = _as_float(x)
    y = _as_float(y)
    cols = x.columns.union(y.columns)
    xa = x.reindex(columns=cols)
    ya = y.reindex(columns=cols)
    corr = xa.rolling(window=n, min_periods=n).corr(ya)
    return corr.replace([np.inf, -np.inf], np.nan)


def ts_cov(x: pd.DataFrame, y: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动样本协方差（每列），min_periods=n。"""
    if n < 2:
        raise ValueError(f"ts_cov window must be >= 2, got {n}")
    x = _as_float(x)
    y = _as_float(y)
    cols = x.columns.union(y.columns)
    xa = x.reindex(columns=cols)
    ya = y.reindex(columns=cols)
    cov = xa.rolling(window=n, min_periods=n).cov(ya)
    return cov.replace([np.inf, -np.inf], np.nan)


def ts_mean(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动均值（每列），预热 → NaN。"""
    if n < 1:
        raise ValueError(f"ts_mean window must be >= 1, got {n}")
    return df.rolling(window=n, min_periods=n).mean()


def ts_std(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动样本标准差 ddof=1（每列），预热 → NaN。"""
    if n < 2:
        raise ValueError(f"ts_std window must be >= 2, got {n}")
    return df.rolling(window=n, min_periods=n).std(ddof=1)


def ts_max(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动最大值（每列），预热 → NaN。"""
    if n < 1:
        raise ValueError(f"ts_max window must be >= 1, got {n}")
    return df.rolling(window=n, min_periods=n).max()


def ts_min(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动最小值（每列），预热 → NaN。"""
    if n < 1:
        raise ValueError(f"ts_min window must be >= 1, got {n}")
    return df.rolling(window=n, min_periods=n).min()


def _argmax_last(arr: np.ndarray) -> float:
    if np.isnan(arr).all():
        return np.nan
    arr_filled = np.where(np.isnan(arr), -np.inf, arr)
    return float(np.argmax(arr_filled))


def _argmin_last(arr: np.ndarray) -> float:
    if np.isnan(arr).all():
        return np.nan
    arr_filled = np.where(np.isnan(arr), np.inf, arr)
    return float(np.argmin(arr_filled))


def ts_argmax(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动 argmax（窗口内 0-based 索引），预热 → NaN。"""
    if n < 1:
        raise ValueError(f"ts_argmax window must be >= 1, got {n}")
    return df.rolling(window=n, min_periods=n).apply(_argmax_last, raw=True)


def ts_argmin(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动 argmin（窗口内 0-based 索引），预热 → NaN。"""
    if n < 1:
        raise ValueError(f"ts_argmin window must be >= 1, got {n}")
    return df.rolling(window=n, min_periods=n).apply(_argmin_last, raw=True)


def delta(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """滞后 d 阶差分：df - df.shift(d)。Lookahead 禁令：d >= 1。"""
    if d < 1:
        raise ValueError(f"delta lag must be >= 1 (lookahead ban), got {d}")
    return df - df.shift(d)


def decay_linear(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """线性衰减加权移动平均，权重 n, n-1, ..., 1 归一化。预热 → NaN。"""
    if n < 1:
        raise ValueError(f"decay_linear window must be >= 1, got {n}")
    weights = np.arange(n, 0, -1, dtype=np.float64)
    weights /= weights.sum()

    def _apply(arr: np.ndarray) -> float:
        if np.isnan(arr).any():
            return np.nan
        return float(np.dot(arr, weights))

    return df.rolling(window=n, min_periods=n).apply(_apply, raw=True)


# ── 面板级函数 ──

def vwap(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """A 股 VWAP 等效参考价：(amount * 1000) / (volume * 100 + 1)。

    如果 panel 中没有 amount 列，使用 (H+L+C)/3 近似。
    """
    if "amount" in panel and "volume" in panel:
        return safe_div(panel["amount"] * 1000.0, panel["volume"] * 100.0 + 1.0)
    # 回退：典型价格
    required = ("open", "high", "low", "close")
    missing = [k for k in required if k not in panel]
    if missing:
        raise KeyError(f"vwap requires panel keys {required}; missing {missing}")
    return (panel["high"] + panel["low"] + panel["close"]) / 3.0
