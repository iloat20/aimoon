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
