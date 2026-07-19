"""财务数据解析/归一化/新浪兜底等模块级辅助函数。

从 ``akshare_adapter.py`` 结构拆分而来:把纯函数式的解析/匹配/归一化 helper
抽到本子模块,原 ``akshare_adapter`` 模块重新导入并再导出所有本模块名字,
保证外部 ``from ...akshare_adapter import X`` 行为不变。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# 合同负债(经销商预收打款蓄水池)— 渠道压货/库存代理指标(消 8.1 缺失清单 #3 代理)。
# 东财列名不稳定(新准则「合同负债」/旧准则「预收款项」),广度匹配:精确候选 + 部分关键词。
_CONTRACT_LIAB_CANDIDATES = [
    "CONTRACT_LIABILITIES",
    "CONTRACT_LIAB",
    "合同负债",
    "ADVANCE_RECEIVING",
    "预收款项",
    "ADVANCE_FROM_CUSTOMERS",
    "PREPAYMENT_RECEIVED",
]
_CONTRACT_LIAB_KWS = ["CONTRACT", "合同负债", "预收", "ADVANCE"]


def _safe_float(value: Any) -> float:
    """Safely convert a value to float, returning 0.0 on failure.

    兼容东方财富返回的逗号千分位字符串(如 "4,418,241,61.55")、中文占位
    ("-"/"--"/"N/A")及空值,避免解析失败静默归零导致关键科目(如 capex)丢失。
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, str):
        s = value.replace(",", "").replace("，", "").strip()
        if s in ("", "-", "--", "N/A", "NA", "None", "null"):
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _is_annual_report(s: object) -> bool:
    """判断新浪「报告日」(如 '20251231'/'2025-12-31')是否为年报(12-31)。"""
    s = str(s)
    return s.endswith("1231") or s.endswith("12-31")


def _get_col(df: pd.DataFrame, col_name: str) -> Any:
    """Get a column value from the first row, returning None if column doesn't exist."""
    if col_name not in df.columns:
        return None
    val = df.iloc[0][col_name]
    if pd.isna(val):
        return None
    return val


def _filter_report_type(df: pd.DataFrame, report_type: str) -> pd.DataFrame:
    """Filter DataFrame to only include rows of the given report type."""
    if "REPORT_TYPE" not in df.columns:
        return df
    filtered = df[df["REPORT_TYPE"] == report_type]
    return filtered if not filtered.empty else df


def _match_amount(
    df: pd.DataFrame,
    candidates: list[str],
    partial_kws: list[str] | None = None,
) -> float:
    """从 DataFrame 首行取第一个命中的正数金额(确定性采集兜底)。

    先精确匹配候选列名,再按部分关键词(列名大写)模糊匹配,
    用于东财接口列名不稳定时的货币资金/在建工程/购建固定资产等科目。
    找不到或全为 0 返回 0.0。
    """
    partial_kws = partial_kws or []
    for c in candidates:
        if c in df.columns:
            v = _safe_float(_get_col(df, c))
            if v != 0.0:
                return abs(v)
    for col in df.columns:
        cu = col.upper()
        if any(kw in cu for kw in partial_kws):
            v = _safe_float(_get_col(df, col))
            if v != 0.0:
                return abs(v)
    return 0.0


def _match_amount_row(
    row: pd.Series,
    candidates: list[str],
    partial_kws: list[str] | None = None,
) -> float:
    """同 ``_match_amount`` 但作用于单行 Series(用于 _merge_statements 历史年报)。"""
    partial_kws = partial_kws or []
    for c in candidates:
        if c in row.index:
            v = row.get(c)
            try:
                if pd.notna(v) and float(v) != 0.0:
                    return abs(float(v))
            except (TypeError, ValueError):
                continue
    for c in row.index:
        cu = str(c).upper()
        if any(kw in cu for kw in partial_kws):
            v = row.get(c)
            try:
                if pd.notna(v) and float(v) != 0.0:
                    return abs(float(v))
            except (TypeError, ValueError):
                continue
    return 0.0
