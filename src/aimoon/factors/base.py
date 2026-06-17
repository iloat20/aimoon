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

# Numba JIT kernels for performance-critical rolling operators
try:
    from numba import njit as _njit

    _HAS_NUMBA = True
except ImportError:
    _HAS_NUMBA = False

if _HAS_NUMBA:

    @_njit(cache=True)
    def _ts_corr_kernel(x, y, n):
        T = len(x)
        out = np.full(T, np.nan)
        for i in range(n - 1, T):
            xw = x[i - n + 1 : i + 1]
            yw = y[i - n + 1 : i + 1]
            mx = xw.mean()
            my = yw.mean()
            dx = xw - mx
            dy = yw - my
            num = (dx * dy).sum()
            den = np.sqrt((dx * dx).sum() * (dy * dy).sum())
            out[i] = num / den if den > 1e-10 else np.nan
        return out

    @_njit(cache=True)
    def _ts_cov_kernel(x, y, n):
        T = len(x)
        out = np.full(T, np.nan)
        for i in range(n - 1, T):
            xw = x[i - n + 1 : i + 1]
            yw = y[i - n + 1 : i + 1]
            mx = xw.mean()
            my = yw.mean()
            out[i] = ((xw - mx) * (yw - my)).sum() / (n - 1)
        return out

    @_njit(cache=True)
    def _ts_std_kernel(x, n):
        T = len(x)
        out = np.full(T, np.nan)
        for i in range(n - 1, T):
            xw = x[i - n + 1 : i + 1]
            mx = xw.mean()
            out[i] = np.sqrt(((xw - mx) ** 2).sum() / (n - 1))
        return out

    @_njit(cache=True)
    def _decay_linear_kernel(x, n):
        T = len(x)
        out = np.full(T, np.nan)
        w_sum = n * (n + 1) / 2.0
        for i in range(n - 1, T):
            s = 0.0
            for j in range(n):
                s += x[i - n + 1 + j] * (j + 1)
            out[i] = s / w_sum
        return out

    @_njit(cache=True)
    def _ts_argmax_kernel(x, n):
        T = len(x)
        out = np.full(T, np.nan)
        for i in range(n - 1, T):
            xw = x[i - n + 1 : i + 1]
            best = 0.0
            best_idx = 0
            has_val = False
            for j in range(len(xw)):
                v = xw[j]
                if not np.isnan(v):
                    if not has_val or v > best:
                        best = v
                        best_idx = j
                    has_val = True
            out[i] = float(best_idx) if has_val else np.nan
        return out

    @_njit(cache=True)
    def _ts_argmin_kernel(x, n):
        T = len(x)
        out = np.full(T, np.nan)
        for i in range(n - 1, T):
            xw = x[i - n + 1 : i + 1]
            best = 0.0
            best_idx = 0
            has_val = False
            for j in range(len(xw)):
                v = xw[j]
                if not np.isnan(v):
                    if not has_val or v < best:
                        best = v
                        best_idx = j
                    has_val = True
            out[i] = float(best_idx) if has_val else np.nan
        return out


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
    """滚动排名（最后一个值在 n 窗口内的排名），结果在 [0, 1]。

    优先使用 Numba JIT 加速（快 10-50x），Numba 不可用时回退到纯 Python。
    """
    if n < 1:
        raise ValueError(f"ts_rank window must be >= 1, got {n}")

    try:
        from numba import njit

        @njit(cache=True)
        def _last_rank_numba(arr: np.ndarray) -> float:
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
            return (less + 0.5 * (eq + 1)) / valid.size

        return df.rolling(window=n, min_periods=n).apply(_last_rank_numba, raw=True)

    except ImportError:
        # 回退：纯 Python 实现
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
    xa = x.reindex(columns=cols).to_numpy(dtype=np.float64, na_value=np.nan)
    ya = y.reindex(columns=cols).to_numpy(dtype=np.float64, na_value=np.nan)

    if _HAS_NUMBA:
        result = np.full_like(xa, np.nan)
        for col_idx in range(xa.shape[1]):
            result[:, col_idx] = _ts_corr_kernel(xa[:, col_idx], ya[:, col_idx], n)
    else:
        result = np.full_like(xa, np.nan)
        for col_idx in range(xa.shape[1]):
            for i in range(n - 1, xa.shape[0]):
                xw = xa[i - n + 1 : i + 1, col_idx]
                yw = ya[i - n + 1 : i + 1, col_idx]
                mx, my = np.nanmean(xw), np.nanmean(yw)
                dx, dy = xw - mx, yw - my
                num = np.nansum(dx * dy)
                den = np.sqrt(np.nansum(dx**2) * np.nansum(dy**2))
                result[i, col_idx] = num / den if den > 1e-10 else np.nan

    return pd.DataFrame(result, index=x.index, columns=cols)


def ts_cov(x: pd.DataFrame, y: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动样本协方差（每列），min_periods=n。"""
    if n < 2:
        raise ValueError(f"ts_cov window must be >= 2, got {n}")
    x = _as_float(x)
    y = _as_float(y)
    cols = x.columns.union(y.columns)
    xa = x.reindex(columns=cols).to_numpy(dtype=np.float64, na_value=np.nan)
    ya = y.reindex(columns=cols).to_numpy(dtype=np.float64, na_value=np.nan)

    if _HAS_NUMBA:
        result = np.full_like(xa, np.nan)
        for col_idx in range(xa.shape[1]):
            result[:, col_idx] = _ts_cov_kernel(xa[:, col_idx], ya[:, col_idx], n)
    else:
        result = np.full_like(xa, np.nan)
        for col_idx in range(xa.shape[1]):
            for i in range(n - 1, xa.shape[0]):
                xw = xa[i - n + 1 : i + 1, col_idx]
                yw = ya[i - n + 1 : i + 1, col_idx]
                mx, my = np.nanmean(xw), np.nanmean(yw)
                result[i, col_idx] = np.nansum((xw - mx) * (yw - my)) / (n - 1)

    return pd.DataFrame(result, index=x.index, columns=cols)


def ts_mean(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动均值（每列），预热 → NaN。"""
    if n < 1:
        raise ValueError(f"ts_mean window must be >= 1, got {n}")
    return df.rolling(window=n, min_periods=n).mean()


def ts_std(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动样本标准差 ddof=1（每列），预热 → NaN。"""
    if n < 2:
        raise ValueError(f"ts_std window must be >= 2, got {n}")
    arr = _as_float(df).to_numpy(dtype=np.float64, na_value=np.nan)

    if _HAS_NUMBA:
        result = np.full_like(arr, np.nan)
        for col_idx in range(arr.shape[1]):
            result[:, col_idx] = _ts_std_kernel(arr[:, col_idx], n)
    else:
        result = np.full_like(arr, np.nan)
        for col_idx in range(arr.shape[1]):
            for i in range(n - 1, arr.shape[0]):
                xw = arr[i - n + 1 : i + 1, col_idx]
                mx = np.nanmean(xw)
                result[i, col_idx] = np.sqrt(np.nansum((xw - mx) ** 2) / (n - 1))

    return pd.DataFrame(result, index=df.index, columns=df.columns)


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
    arr = _as_float(df).to_numpy(dtype=np.float64, na_value=np.nan)

    if _HAS_NUMBA:
        result = np.full_like(arr, np.nan)
        for col_idx in range(arr.shape[1]):
            result[:, col_idx] = _ts_argmax_kernel(arr[:, col_idx], n)
    else:
        result = np.full_like(arr, np.nan)
        for col_idx in range(arr.shape[1]):
            for i in range(n - 1, arr.shape[0]):
                xw = arr[i - n + 1 : i + 1, col_idx]
                if np.all(np.isnan(xw)):
                    continue
                mask = ~np.isnan(xw)
                result[i, col_idx] = float(np.argmax(xw[mask]) + np.argmax(mask))

    return pd.DataFrame(result, index=df.index, columns=df.columns)


def ts_argmin(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动 argmin（窗口内 0-based 索引），预热 → NaN。"""
    if n < 1:
        raise ValueError(f"ts_argmin window must be >= 1, got {n}")
    arr = _as_float(df).to_numpy(dtype=np.float64, na_value=np.nan)

    if _HAS_NUMBA:
        result = np.full_like(arr, np.nan)
        for col_idx in range(arr.shape[1]):
            result[:, col_idx] = _ts_argmin_kernel(arr[:, col_idx], n)
    else:
        result = np.full_like(arr, np.nan)
        for col_idx in range(arr.shape[1]):
            for i in range(n - 1, arr.shape[0]):
                xw = arr[i - n + 1 : i + 1, col_idx]
                if np.all(np.isnan(xw)):
                    continue
                mask = ~np.isnan(xw)
                result[i, col_idx] = float(np.argmin(xw[mask]) + np.argmax(mask))

    return pd.DataFrame(result, index=df.index, columns=df.columns)


def delta(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """滞后 d 阶差分：df - df.shift(d)。Lookahead 禁令：d >= 1。"""
    if d < 1:
        raise ValueError(f"delta lag must be >= 1 (lookahead ban), got {d}")
    return df - df.shift(d)


def decay_linear(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """线性衰减加权移动平均，权重 n, n-1, ..., 1 归一化。预热 → NaN。"""
    if n < 1:
        raise ValueError(f"decay_linear window must be >= 1, got {n}")
    arr = _as_float(df).to_numpy(dtype=np.float64, na_value=np.nan)

    if _HAS_NUMBA:
        result = np.full_like(arr, np.nan)
        for col_idx in range(arr.shape[1]):
            result[:, col_idx] = _decay_linear_kernel(arr[:, col_idx], n)
    else:
        weights = np.arange(n, 0, -1, dtype=np.float64)
        weights /= weights.sum()
        result = np.full_like(arr, np.nan)
        for col_idx in range(arr.shape[1]):
            for i in range(n - 1, arr.shape[0]):
                xw = arr[i - n + 1 : i + 1, col_idx]
                if np.any(np.isnan(xw)):
                    continue
                result[i, col_idx] = float(np.dot(xw, weights))

    return pd.DataFrame(result, index=df.index, columns=df.columns)


# ── 面板级函数 ──


def vwap(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """成交量加权平均价 (VWAP)。

    标准公式: VWAP = sum(typical_price * volume) / sum(volume)
    其中 typical_price = (high + low + close) / 3。

    如果 panel 中有 amount 列（A 股成交额，单位元），
    可直接用 amount / volume 作为近似均价（amount 已包含价格×成交量）。
    """
    if "amount" in panel and "volume" in panel:
        # amount 单位: 元, volume 单位: 手 (100 股)
        # amount / volume = 平均每手价格 × 100 = 近似均价
        return safe_div(panel["amount"], panel["volume"])
    # 标准 VWAP: typical_price 的成交量加权平均
    required = ("open", "high", "low", "close", "volume")
    missing = [k for k in required if k not in panel]
    if missing:
        # 回退：典型价格（无成交量权重）
        basic = ("open", "high", "low", "close")
        missing_basic = [k for k in basic if k not in panel]
        if missing_basic:
            raise KeyError(
                f"vwap requires panel keys {required} (or at least {basic}); "
                f"missing {missing_basic}"
            )
        return (panel["high"] + panel["low"] + panel["close"]) / 3.0

    typical_price = (panel["high"] + panel["low"] + panel["close"]) / 3.0
    volume = panel["volume"]
    # VWAP = sum(typical_price * volume) / sum(volume)
    vwap_num = (typical_price * volume).rolling(window=20, min_periods=1).sum()
    vwap_den = volume.rolling(window=20, min_periods=1).sum()
    return safe_div(vwap_num, vwap_den)
