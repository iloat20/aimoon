"""A股鲁棒时序特征 — 涨跌停干扰免疫 + 筹码分布 + regime 标记。

针对 A 股市场特性设计：
- 高散户占比 → 需要 winsorized 统计量抑制极端值
- ±10% 涨跌停 → 收益率截断，非对称波动率
- 行业轮动快 → regime 标记分离高/低波动状态

所有特征使用 shift(1) 对齐，确保无前瞻偏差。

特征总数：~60 个（可配置 rolling windows）
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ──

_LIMIT_PCT = 0.10  # A 股涨跌停幅度
_WINSORIZE_BOUND = 0.095  # 略低于涨跌停，保留信号但去除极端值


# ════════════════════════════════════════════════════════════════
#  1. Winsorized Return Statistics（涨跌停鲁棒统计量）
# ════════════════════════════════════════════════════════════════


def winsorized_returns(
    close: pd.DataFrame,
    bound: float = _WINSORIZE_BOUND,
) -> pd.DataFrame:
    """计算 winsorized 日收益率。

    将收益率截断到 ±bound，消除涨跌停对统计量的扭曲。
    对 A 股：bound=0.095（略低于 10% 涨跌停）。

    公式:
        ret_t = close_t / close_{t-1} - 1
        winsorized_ret_t = clip(ret_t, -bound, +bound)

    Parameters
    ----------
    close : pd.DataFrame
        收盘价矩阵，index=日期, columns=股票代码。
    bound : float
        Winsorize 上下界（绝对值）。

    Returns
    -------
    pd.DataFrame
        Winsorized 收益率矩阵（与 close 同 shape）。
    """
    ret = close.pct_change(fill_method=None)
    return ret.clip(lower=-bound, upper=bound)


def winsorized_mean_ret(
    close: pd.DataFrame,
    windows: tuple[int, ...] = (5, 10, 20),
) -> dict[str, pd.DataFrame]:
    """多窗口 winsorized 均值收益率。

    公式:
        winsorized_mean_ret_N = mean(clip(ret, -0.095, +0.095), window=N)

    Parameters
    ----------
    close : pd.DataFrame
        收盘价矩阵。
    windows : tuple[int, ...]
        滚动窗口列表。

    Returns
    -------
    dict[str, pd.DataFrame]
        key=f"wmr_{w}d", value=winsorized mean return DataFrame。
    """
    wr = winsorized_returns(close)
    result = {}
    for w in windows:
        result[f"wmr_{w}d"] = wr.rolling(w, min_periods=max(1, w // 2)).mean()
    return result


def winsorized_std(
    close: pd.DataFrame,
    windows: tuple[int, ...] = (5, 10, 20),
) -> dict[str, pd.DataFrame]:
    """多窗口 winsorized 波动率。

    公式:
        winsorized_std_N = std(clip(ret, -0.095, +0.095), window=N)

    Parameters
    ----------
    close : pd.DataFrame
        收盘价矩阵。
    windows : tuple[int, ...]
        滚动窗口列表。

    Returns
    -------
    dict[str, pd.DataFrame]
        key=f"wstd_{w}d", value=winsorized std DataFrame。
    """
    wr = winsorized_returns(close)
    result = {}
    for w in windows:
        result[f"wstd_{w}d"] = wr.rolling(w, min_periods=max(1, w // 2)).std()
    return result


# ════════════════════════════════════════════════════════════════
#  2. Asymmetric Volatility（非对称波动率）
# ════════════════════════════════════════════════════════════════


def asymmetric_volatility(
    close: pd.DataFrame,
    window: int = 20,
) -> dict[str, pd.DataFrame]:
    """分离上行/下行波动率。

    A 股散户占比高，上涨和下跌的波动特征不对称：
    - 上涨时散户追涨 → 波动率较低（单边涨）
    - 下跌时恐慌抛售 → 波动率较高（踩踏）

    公式:
        wr_t = clip(ret_t, -0.095, +0.095)
        upside_vol  = std(wr_t | wr_t > 0, window=N)
        downside_vol = std(wr_t | wr_t < 0, window=N)
        vol_ratio   = upside_vol / downside_vol

    实现: 用 mask 将非目标方向的收益率置 NaN，再 rolling std。

    Parameters
    ----------
    close : pd.DataFrame
        收盘价矩阵。
    window : int
        滚动窗口。

    Returns
    -------
    dict[str, pd.DataFrame]
        upside_vol, downside_vol, vol_ratio。
    """
    wr = winsorized_returns(close)

    # 上行波动率：仅保留正收益
    up_masked = wr.where(wr > 0)
    upside_vol = up_masked.rolling(window, min_periods=max(1, window // 2)).std()

    # 下行波动率：仅保留负收益
    down_masked = wr.where(wr < 0)
    downside_vol = down_masked.rolling(window, min_periods=max(1, window // 2)).std()

    # 波动率比值：>1 表示上行波动更大（牛市特征），<1 表示下行波动更大（熊市特征）
    vol_ratio = upside_vol / downside_vol.replace(0, np.nan)

    return {
        "upside_vol": upside_vol,
        "downside_vol": downside_vol,
        "vol_ratio": vol_ratio,
    }


# ════════════════════════════════════════════════════════════════
#  3. Chip Distribution Features（筹码分布特征）
# ════════════════════════════════════════════════════════════════


def chip_distribution_features(
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    volume: pd.DataFrame | None = None,
    windows: tuple[int, ...] = (10, 20, 60),
) -> dict[str, pd.DataFrame]:
    """基于 OHLC 估算的筹码分布特征。

    核心思想：
    - 支撑位 = rolling min(low) — 最近 N 天的最低价是"密集成交区下沿"
    - 阻力位 = rolling max(high) — 最近 N 天的最高价是"密集成交区上沿"
    - 典型价格 = (H + L + C) / 3 — 作为筹码重心代理
    - 筹码集中度 = (resistance - support) / typical_price — 越小越集中

    公式:
        support_N     = rolling_min(low, N)
        resistance_N  = rolling_max(high, N)
        typical_price = (H + L + C) / 3
        chip_conc_N   = (resistance_N - support_N) / typical_price
        support_dist  = (close - support_N) / typical_price
        resists_dist  = (resistance_N - close) / typical_price
        midpoint_pos  = (close - support_N) / (resistance_N - support_N)

    Parameters
    ----------
    high, low, close : pd.DataFrame
        OHLC 价格矩阵。
    volume : pd.DataFrame | None
        成交量矩阵（用于 VWAP 加权筹码重心）。
    windows : tuple[int, ...]
        滚动窗口列表。

    Returns
    -------
    dict[str, pd.DataFrame]
        各筹码分布特征。
    """
    typical_price = (high + low + close) / 3.0
    # 防除零
    tp_safe = typical_price.replace(0, np.nan)

    result = {}
    for w in windows:
        support = low.rolling(w, min_periods=1).min()
        resistance = high.rolling(w, min_periods=1).max()

        # 筹码集中度：区间宽度 / 典型价格
        result[f"chip_conc_{w}d"] = (resistance - support) / tp_safe

        # 收盘价到支撑位的距离（归一化）
        result[f"support_dist_{w}d"] = (close - support) / tp_safe

        # 收盘价到阻力位的距离（归一化）
        result[f"resist_dist_{w}d"] = (resistance - close) / tp_safe

        # 收盘价在区间中的位置 [0, 1]：0=在支撑位，1=在阻力位
        range_width = (resistance - support).replace(0, np.nan)
        result[f"midpoint_pos_{w}d"] = (close - support) / range_width

    return result


# ════════════════════════════════════════════════════════════════
#  4. Higher-Order Moments + Regime（高阶矩 + regime 标记）
# ════════════════════════════════════════════════════════════════


def rolling_skew_kurt(
    close: pd.DataFrame,
    windows: tuple[int, ...] = (20, 60),
) -> dict[str, pd.DataFrame]:
    """滚动偏度和峰度。

    偏度 < 0：左偏（下行风险大，散户亏损多）
    峰度 > 3：厚尾（极端事件概率高于正态分布）

    公式:
        skew_N = rolling_skew(ret, N)
        kurt_N = rolling_kurt(ret, N)  # excess kurtosis (正态=0)

    Parameters
    ----------
    close : pd.DataFrame
        收盘价矩阵。
    windows : tuple[int, ...]
        滚动窗口列表。

    Returns
    -------
    dict[str, pd.DataFrame]
        各窗口的偏度和峰度。
    """
    ret = close.pct_change(fill_method=None)
    result = {}
    for w in windows:
        result[f"skew_{w}d"] = ret.rolling(w, min_periods=max(1, w // 2)).skew()
        result[f"kurt_{w}d"] = ret.rolling(w, min_periods=max(1, w // 2)).kurt()
    return result


def vol_regime_flag(
    close: pd.DataFrame,
    short_window: int = 20,
    long_window: int = 120,
    threshold_percentile: float = 60.0,
) -> dict[str, pd.DataFrame]:
    """波动率 regime 标记。

    当短期波动率超过长期波动率的 N 百分位时，标记为高波动 regime。

    公式:
        short_vol = std(ret, short_window)
        long_vol_median = rolling_median(std(ret, long_window))
        regime = (short_vol > percentile(long_vol, threshold_percentile)).astype(float)

    Parameters
    ----------
    close : pd.DataFrame
        收盘价矩阵。
    short_window : int
        短期波动率窗口。
    long_window : int
        长期波动率参考窗口。
    threshold_percentile : float
        百分位阈值（0-100）。

    Returns
    -------
    dict[str, pd.DataFrame]
        vol_regime: 1=高波动, 0=低波动。
    """
    ret = close.pct_change(fill_method=None)
    short_vol = ret.rolling(short_window, min_periods=max(1, short_window // 2)).std()

    # 长期滚动百分位作为动态阈值
    long_vol = ret.rolling(long_window, min_periods=max(1, long_window // 2)).std()
    # 用 rolling apply 计算百分位（避免全量计算）
    vol_threshold = long_vol.rolling(long_window, min_periods=max(1, long_window // 2)).quantile(
        threshold_percentile / 100.0
    )

    regime = (short_vol > vol_threshold).astype(float)
    return {"vol_regime": regime}


def regime_conditional_moments(
    close: pd.DataFrame,
    skew_window: int = 20,
    vol_window: int = 20,
    long_window: int = 120,
) -> dict[str, pd.DataFrame]:
    """Regime 条件偏度/峰度。

    高波动 regime 下的偏度更值得关注（恐慌踩踏时偏度极负）。

    公式:
        high_vol_skew = skew(ret, N) * regime
        low_vol_kurt  = kurt(ret, N) * (1 - regime)

    Parameters
    ----------
    close : pd.DataFrame
        收盘价矩阵。
    skew_window : int
        偏度/峰度计算窗口。
    vol_window : int
        短期波动率窗口。
    long_window : int
        长期波动率参考窗口。

    Returns
    -------
    dict[str, pd.DataFrame]
        条件偏度和峰度。
    """
    ret = close.pct_change(fill_method=None)
    skew_val = ret.rolling(skew_window, min_periods=max(1, skew_window // 2)).skew()
    kurt_val = ret.rolling(skew_window, min_periods=max(1, skew_window // 2)).kurt()

    regime_info = vol_regime_flag(close, vol_window, long_window)
    regime = regime_info["vol_regime"]

    return {
        "high_vol_skew": skew_val * regime,
        "low_vol_kurt": kurt_val * (1 - regime),
    }


# ════════════════════════════════════════════════════════════════
#  5. Volume-Price Features（量价特征）
# ════════════════════════════════════════════════════════════════


def volume_price_features(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    high: pd.DataFrame | None = None,
    low: pd.DataFrame | None = None,
    windows: tuple[int, ...] = (5, 10, 20),
) -> dict[str, pd.DataFrame]:
    """量价关系特征。

    公式:
        vwap_dev      = (close - vwap) / vwap,  vwap = sum(HLC/3 * V) / sum(V)
        vol_momentum_N = volume / rolling_mean(volume, N)
        price_vol_corr_N = rolling_corr(ret, vol_change, N)

    Parameters
    ----------
    close, volume : pd.DataFrame
        收盘价和成交量矩阵。
    high, low : pd.DataFrame | None
        最高/最低价（用于 VWAP 计算）。
    windows : tuple[int, ...]
        滚动窗口列表。

    Returns
    -------
    dict[str, pd.DataFrame]
        量价关系特征。
    """
    if high is None or low is None:
        high = close
        low = close

    typical_price = (high + low + close) / 3.0
    # 近似 VWAP：典型价格 × 成交量
    pv = typical_price * volume
    vwap_cum = pv.rolling(20, min_periods=1).sum()
    vol_cum = volume.rolling(20, min_periods=1).sum()
    vwap = vwap_cum / vol_cum.replace(0, np.nan)

    result = {}

    # VWAP 偏离度
    result["vwap_dev"] = (close - vwap) / vwap.replace(0, np.nan)

    # 成交量动量
    for w in windows:
        vol_ma = volume.rolling(w, min_periods=1).mean()
        result[f"vol_mom_{w}d"] = volume / vol_ma.replace(0, np.nan)

    # 量价相关性
    ret = close.pct_change(fill_method=None)
    vol_change = volume.pct_change(fill_method=None)
    for w in windows:
        result[f"pv_corr_{w}d"] = ret.rolling(w, min_periods=max(1, w // 2)).corr(vol_change)

    return result


# ════════════════════════════════════════════════════════════════
#  6. 组合入口（shift 对齐防前瞻）
# ════════════════════════════════════════════════════════════════


def extract_robust_features(
    panel: dict[str, pd.DataFrame],
    target_date: pd.Timestamp | None = None,
    shift: int = 1,
) -> pd.DataFrame:
    """提取 A 股鲁棒时序特征（组合入口）。

    所有特征在计算后统一 shift(shift) 天，确保 t 日特征仅使用
    t-shift 日及之前的数据，避免前瞻偏差。

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo 宽表，需含 open/high/low/close/volume。
        每张表 index=日期, columns=股票代码。
    target_date : pd.Timestamp | None
        截面日期。None 则取最后一行。
    shift : int
        前移天数（默认 1 = 使用昨天数据）。

    Returns
    -------
    pd.DataFrame
        index=stock codes, columns=特征名。
        Empty if insufficient data。
    """
    close = panel.get("close")
    if close is None or len(close) < 130:
        return pd.DataFrame()

    codes = list(close.columns)
    if len(codes) < 5:
        return pd.DataFrame()

    # 确定截面位置
    if target_date is not None and target_date in close.index:
        end_idx = close.index.get_loc(target_date)
    else:
        end_idx = len(close) - 1

    if end_idx + 1 < 130:  # 需要至少 130 天数据（最长 rolling window）
        return pd.DataFrame()

    start_idx = end_idx - 129  # 取 130 天窗口（含目标日）

    # 截取窗口
    close_w = close.iloc[start_idx : end_idx + 1]
    high_w = panel.get("high", close).iloc[start_idx : end_idx + 1]
    low_w = panel.get("low", close).iloc[start_idx : end_idx + 1]
    volume_w = panel.get("volume", pd.DataFrame(0, index=close.index, columns=close.columns)).iloc[
        start_idx : end_idx + 1
    ]

    # 过滤无效股票
    base_price = close_w.iloc[0]
    valid_codes = base_price[base_price > 0].index
    if len(valid_codes) < 5:
        return pd.DataFrame()

    close_w = close_w[valid_codes]
    high_w = high_w[valid_codes]
    low_w = low_w[valid_codes]
    volume_w = volume_w[valid_codes]

    # ── 计算所有特征（返回 dict[str, DataFrame]）──
    all_features: dict[str, pd.DataFrame] = {}

    # 1. Winsorized 统计量
    all_features.update(winsorized_mean_ret(close_w, windows=(5, 10, 20)))
    all_features.update(winsorized_std(close_w, windows=(5, 10, 20)))

    # 2. 非对称波动率
    all_features.update(asymmetric_volatility(close_w, window=20))

    # 3. 筹码分布
    all_features.update(
        chip_distribution_features(high_w, low_w, close_w, volume_w, windows=(10, 20, 60))
    )

    # 4. 高阶矩 + regime
    all_features.update(rolling_skew_kurt(close_w, windows=(20, 60)))
    all_features.update(vol_regime_flag(close_w))
    all_features.update(regime_conditional_moments(close_w))

    # 5. 量价特征
    all_features.update(volume_price_features(close_w, volume_w, high_w, low_w))

    # ── 组装为矩阵，统一 shift 防前瞻 ──
    # 取最后一个日期的截面值
    feature_series: dict[str, pd.Series] = {}
    for name, df in all_features.items():
        if df.empty:
            continue
        # shift: 确保 t 日特征仅使用 ≤ t-shift 日数据
        shifted = df.shift(shift)
        if end_idx - start_idx < len(shifted):
            last_row = shifted.iloc[-1]
        else:
            last_row = shifted.iloc[end_idx - start_idx]
        feature_series[name] = last_row

    if not feature_series:
        return pd.DataFrame()

    result = pd.DataFrame(feature_series)
    result = result.fillna(0)

    # Clip extreme values (winsorize final features)
    result = result.clip(lower=-10, upper=10)

    logger.info(
        "Robust features: %d stocks × %d features (shift=%d)",
        len(result),
        result.shape[1],
        shift,
    )
    return result
