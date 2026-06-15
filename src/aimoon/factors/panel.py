"""Panel 构建器 — 将 aimoon 的单股 DataFrame 转换为 Alpha Zoo 宽表格式。

Alpha Zoo 因子作用于宽表 dict[str, DataFrame]，其中：
- keys: "open", "high", "low", "close", "volume"
- 每个 DataFrame: index=DatetimeIndex (交易日期), columns=股票代码

aimoon 的 get_kline 返回单股 DataFrame（index=date, columns=OHLCV+...）。
本模块将多只股票的 kline 合并为宽表。
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Alpha Zoo 需要的核心列
_PANEL_COLUMNS = ("open", "high", "low", "close", "volume")


def build_panel(
    klines: dict[str, pd.DataFrame],
    min_rows: int = 60,
) -> dict[str, pd.DataFrame] | None:
    """将 {code: kline_df} 转换为 Alpha Zoo 宽表格式。

    Parameters
    ----------
    klines : dict[str, pd.DataFrame]
        股票代码 -> K 线 DataFrame（index=date, 含 open/close/high/low/volume 列）。
    min_rows : int
        数据行数少于此值的股票将被排除。

    Returns
    -------
    dict[str, pd.DataFrame] | None
        {"open": wide_df, "close": wide_df, ...}，如果有效股票不足则返回 None。
    """
    if not klines:
        return None

    # 修复整数索引的日期（必须在 build_panel 之前）
    from aimoon.data.validator import fix_kline_dates

    klines = {code: fix_kline_dates(df) for code, df in klines.items()}

    # 过滤数据不足的股票
    valid_codes: list[str] = []
    for code, df in klines.items():
        if df is None or len(df) < min_rows:
            continue
        missing = [c for c in _PANEL_COLUMNS if c not in df.columns]
        if missing:
            continue
        valid_codes.append(code)

    if len(valid_codes) < 2:
        logger.warning("Panel 需要至少 2 只有效股票，只有 %d 只", len(valid_codes))
        return None

    # Precompute datetime index per stock
    dt_indices: dict[str, pd.DatetimeIndex] = {}
    for code in valid_codes:
        idx = klines[code].index
        dt_indices[code] = pd.to_datetime(idx) if not isinstance(idx, pd.DatetimeIndex) else idx

    # 构建每列的宽表 — 批量 dict 构造，避免逐 stock Python 循环 + pd.concat
    panel: dict[str, pd.DataFrame] = {}
    for col in _PANEL_COLUMNS:
        # 一次性构建 {code: Series} dict，然后批量构造 DataFrame
        col_data: dict[str, pd.Series] = {}
        for code in valid_codes:
            df = klines[code]
            if col in df.columns:
                s = df[col]
                s.index = dt_indices[code]
                col_data[code] = s

        if col_data:
            wide = pd.DataFrame(col_data)
            wide = wide.sort_index()
            wide = wide.ffill(limit=5)
            panel[col] = wide

    # 如果有 amount 列，也构建它（用于 vwap 计算）
    has_amount = any("amount" in klines[c].columns for c in valid_codes)
    if has_amount:
        amount_data: dict[str, pd.Series] = {}
        for code in valid_codes:
            df = klines[code]
            if "amount" in df.columns and len(df) >= min_rows:
                s = df["amount"]
                s.index = dt_indices[code]
                amount_data[code] = s
        if amount_data:
            panel["amount"] = pd.DataFrame(amount_data).sort_index().ffill(limit=5)

    logger.info(
        "Panel 构建完成: %d 只股票, %d 个交易日",
        len(valid_codes),
        len(panel["close"]),
    )
    return panel
