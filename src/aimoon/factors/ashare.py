"""A 股因子模块 — 11 个手写稳定因子 + panel 构建。

替代旧的 factors/zoo（452 因子）+ registry/panel/dag 等。设计目标：
计算稳定（除零保护、无复杂链式算子）、向量化快、A 股文献验证。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_PANEL_COLUMNS = ("open", "high", "low", "close", "volume")
_OPTIONAL_COLUMNS = ("turnover", "amount", "northbound")

# A 股 11 个稳定因子列表——增删调参入口，无需 AST 注册
ASHARE_FACTORS = (
    "rev_5d",
    "rev_20d",
    "turnover_20d",
    "vol_20d",
    "mom_60d",
    "amihud_20d",
    "ep",
    "bp",
    "div_yield",
    "northbound_chg_20d",
    "sector_mom_20d",
)

_EPS = 1e-12


def robust_zscore(
    series: pd.Series | pd.DataFrame,
    clip: float = 3.0,
) -> pd.Series | pd.DataFrame:
    """稳健 z-score：使用中位数和 MAD，clip 到 ±clip。

    NaN 值在计算前用截面中位数填充（skipna=True）。

    Args:
        series: 输入序列/DataFrame（沿列计算截面 z-score）。
        clip: 裁剪阈值（默认 3）。

    Returns:
        z-score 标准化后的序列/DataFrame（NaN 填充 0）。
    """
    if isinstance(series, pd.DataFrame):
        return series.apply(lambda s: robust_zscore(s, clip=clip))

    if series.empty:
        return series

    # 用截面中位数填充 NaN（skipna=True 兼容全 NaN 列）
    median = series.median()
    if pd.isna(median):
        return series * 0.0
    filled = series.fillna(median)

    mad = (filled - median).abs().median()
    if mad < _EPS:
        return filled * 0.0

    z = (filled - median) / mad * 1.4826
    return z.clip(-clip, clip).fillna(0.0)


def build_panel(
    klines: dict[str, pd.DataFrame],
    min_rows: int = 60,
) -> dict[str, pd.DataFrame] | None:
    """将 {code: kline_df} 转为宽表 {field: DataFrame(日期×股票)}。"""
    if not klines:
        return None

    from aimoon.data.validator import fix_kline_dates

    klines = {code: fix_kline_dates(df) for code, df in klines.items()}

    valid_codes: list[str] = []
    for code, df in klines.items():
        if df is None or len(df) < min_rows:
            continue
        if any(c not in df.columns for c in _PANEL_COLUMNS):
            continue
        valid_codes.append(code)

    if len(valid_codes) < 1:
        logger.warning("Panel 需至少 1 只有效股票")
        return None

    dt_indices: dict[str, pd.DatetimeIndex] = {}
    for code in valid_codes:
        idx = klines[code].index
        dt_indices[code] = idx if isinstance(idx, pd.DatetimeIndex) else pd.to_datetime(idx)

    panel: dict[str, pd.DataFrame] = {}
    for col in _PANEL_COLUMNS + _OPTIONAL_COLUMNS:
        col_data: dict[str, pd.Series] = {}
        for code in valid_codes:
            df = klines[code]
            if col in df.columns:
                s = df[col].copy()
                s.index = dt_indices[code]
                col_data[code] = s
        if col_data:
            wide = pd.DataFrame(col_data).sort_index().ffill(limit=5)
            panel[col] = wide
    return panel


# ------------------------------------------------------------------
# 11 个 A 股因子计算
# ------------------------------------------------------------------


def _negate_cross_section(s: pd.DataFrame) -> pd.DataFrame:
    """稳健 z-score 后取负（用于反转/波动率等负向因子）。"""
    return -robust_zscore(s, clip=3.0)


def compute_rev_5d(close: pd.DataFrame) -> pd.DataFrame:
    """5 日反转因子。A 股短期反转效应极强。"""
    return _negate_cross_section(close.pct_change(5))


def compute_rev_20d(close: pd.DataFrame) -> pd.DataFrame:
    """20 日反转因子。中短期反转。"""
    return _negate_cross_section(close.pct_change(20))


def compute_turnover_20d(turnover: pd.DataFrame) -> pd.DataFrame:
    """20 日平均换手率。高换手→低未来收益（流动性溢价反转）。"""
    avg = turnover.rolling(20, min_periods=10).mean()
    return _negate_cross_section(avg)


def compute_vol_20d(close: pd.DataFrame) -> pd.DataFrame:
    """20 日实现波动率。低波动异常。"""
    ret = close.pct_change()
    vol = ret.rolling(20, min_periods=10).std()
    return _negate_cross_section(vol)


def compute_mom_60d(close: pd.DataFrame) -> pd.DataFrame:
    """60 日动量。A 股中期动量有效。"""
    mom = close.pct_change(60)
    return robust_zscore(mom, clip=3.0)


def compute_amihud_20d(close: pd.DataFrame, amount: pd.DataFrame) -> pd.DataFrame:
    """Amihud 非流动性因子。非流动性溢价，正方向。"""
    daily_ret = close.pct_change().abs()
    # 保护除零：amount < _EPS 的位置填 NaN
    illiq = daily_ret / amount.where(amount >= _EPS)
    avg = illiq.rolling(20, min_periods=10).mean()
    return robust_zscore(avg, clip=3.0)


def compute_ep(pe: pd.DataFrame) -> pd.DataFrame:
    """盈利收益率 = 1/PE。价值因子，正方向。"""
    ep = 1.0 / pe.where(pe >= 1.0)  # 排除 PE < 1（含 0/负/NaN）
    return robust_zscore(ep, clip=3.0)


def compute_bp(pb: pd.DataFrame) -> pd.DataFrame:
    """账面市值比 = 1/PB。价值因子，正方向。"""
    bp = 1.0 / pb.where(pb >= 0.1)  # 排除 PB < 0.1
    return robust_zscore(bp, clip=3.0)


def compute_div_yield(dividend: pd.DataFrame) -> pd.DataFrame:
    """股息率因子。红利溢价，正方向。"""
    return robust_zscore(dividend, clip=3.0)


def compute_northbound_chg_20d(northbound: pd.DataFrame) -> pd.DataFrame:
    """北向持仓 20 日变化。聪明资金信号。"""
    return robust_zscore(northbound.pct_change(20), clip=3.0)


def compute_sector_mom_20d(
    close: pd.DataFrame,
    sector_map: dict[str, str],
) -> pd.DataFrame:
    """板块 20 日动量。每只股票获得所属板块等权平均 20 日收益。"""
    ret_20d = close.pct_change(20)
    sector_to_codes: dict[str, list[str]] = {}
    for code, sector in sector_map.items():
        if code in close.columns:
            sector_to_codes.setdefault(sector, []).append(code)

    result = pd.DataFrame(0.0, index=ret_20d.index, columns=ret_20d.columns)
    for sector, codes in sector_to_codes.items():
        sector_ret = ret_20d[codes].mean(axis=1)
        for code in codes:
            result[code] = sector_ret
    return robust_zscore(result, clip=3.0)


# ------------------------------------------------------------------
# 批量因子计算入口
# ------------------------------------------------------------------


def compute_ashare_factors(
    panel: dict[str, pd.DataFrame],
    sector_map: dict[str, str] | None = None,
    pe: pd.DataFrame | None = None,
    pb: pd.DataFrame | None = None,
    dividend: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """计算全部 A 股因子，返回 {factor_id: DataFrame(日期×股票)}。

    Args:
        panel: build_panel 输出的宽表 {field: DataFrame(日期×股票)}。
        sector_map: {code: sector_name} 映射（sector_mom_20d 需要）。
        pe: PE 面板数据（日期×股票），None 则跳过 ep。
        pb: PB 面板数据，None 则跳过 bp。
        dividend: 股息率面板数据，None 则跳过 div_yield。

    Returns:
        dict[str, pd.DataFrame]: 仅包含数据可用的因子。
    """
    close = panel.get("close")
    if close is None or close.empty:
        logger.warning("compute_ashare_factors: close 为空")
        return {}

    factors: dict[str, pd.DataFrame] = {}

    # 价格类因子
    factors["rev_5d"] = compute_rev_5d(close)
    factors["rev_20d"] = compute_rev_20d(close)
    factors["vol_20d"] = compute_vol_20d(close)
    factors["mom_60d"] = compute_mom_60d(close)

    # 换手率（可选字段）
    if "turnover" in panel:
        factors["turnover_20d"] = compute_turnover_20d(panel["turnover"])

    # Amihud（可选字段）
    if "amount" in panel:
        factors["amihud_20d"] = compute_amihud_20d(close, panel["amount"])

    # 基本面因子
    if pe is not None and not pe.empty:
        factors["ep"] = compute_ep(pe)
    if pb is not None and not pb.empty:
        factors["bp"] = compute_bp(pb)
    if dividend is not None and not dividend.empty:
        factors["div_yield"] = compute_div_yield(dividend)

    # 北向资金（可选字段）
    if "northbound" in panel:
        factors["northbound_chg_20d"] = compute_northbound_chg_20d(panel["northbound"])

    # 板块动量
    if sector_map:
        factors["sector_mom_20d"] = compute_sector_mom_20d(close, sector_map)

    return factors
