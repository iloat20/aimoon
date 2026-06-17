"""数据验证层 - 确保所有数据使用正确的格式

功能：
1. 验证 K 线数据格式
2. 修复日期格式问题
3. 验证数据完整性
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def validate_kline(kline: pd.DataFrame, code: str = "") -> bool:
    """验证 K 线数据

    Args:
        kline: K 线数据 DataFrame
        code: 股票代码（用于日志）

    Returns:
        bool: 数据是否有效
    """
    if kline is None or kline.empty:
        logger.warning("Empty kline data for %s", code)
        return False

    # 检查必需列
    required_columns = ["open", "close", "high", "low", "volume"]
    for col in required_columns:
        if col not in kline.columns:
            logger.warning("Missing column %s for %s", col, code)
            return False

    # 检查数据长度
    if len(kline) < 60:
        logger.warning("Insufficient data for %s: %d days", code, len(kline))
        return False

    # 检查日期格式
    if not isinstance(kline.index, pd.DatetimeIndex):
        logger.warning("Invalid date format for %s: %s", code, type(kline.index[0]))
        return False

    # 检查价格数据
    close = pd.to_numeric(kline["close"], errors="coerce")
    if close.isna().all():
        logger.warning("All close prices are NaN for %s", code)
        return False
    na_ratio = close.isna().mean()
    if na_ratio > 0.05:
        logger.warning("High ratio of NaN close prices for %s: %.1f%%", code, na_ratio * 100)
        return False

    return True


def fix_kline_dates(kline: pd.DataFrame, code: str = "") -> pd.DataFrame:
    """全局日期修复函数 - 确保 K 线数据使用正确的日期格式

    Args:
        kline: K 线数据 DataFrame
        code: 股票代码（用于日志）

    Returns:
        pd.DataFrame: 修复后的 K 线数据
    """
    if kline is None or kline.empty:
        return kline

    # 如果已经是正确的日期格式，直接返回
    if isinstance(kline.index, pd.DatetimeIndex):
        return kline

    # 如果 index 是整数，尝试使用 date 列
    if len(kline) > 0 and isinstance(kline.index[0], (int, np.integer)):
        if "date" in kline.columns:
            try:
                kline = kline.copy()
                kline["date"] = pd.to_datetime(kline["date"])
                kline = kline.set_index("date").sort_index()
                return kline
            except Exception as e:
                logger.warning("Failed to fix dates using date column for %s: %s", code, e)

        # 没有 date 列：记录警告并返回原始数据，不猜测日期
        logger.error(
            "Cannot fix kline dates: index is integer and no date column. "
            "This indicates a data source bug. Returning original data."
        )
        return kline

    return kline


def detect_halt_days(kline: pd.DataFrame, min_zero_vol_days: int = 3) -> set:
    """检测停牌日（连续零成交量或价格不变）。

    Args:
        kline: K 线数据
        min_zero_vol_days: 连续零成交量超过此天数视为停牌

    Returns:
        set: 停牌日期集合
    """
    if kline is None or kline.empty:
        return set()
    halt_dates = set()
    zero_vol_streak = 0
    for i in range(len(kline)):
        vol = float(kline["volume"].iloc[i]) if "volume" in kline.columns else 0.0
        if vol <= 0:
            zero_vol_streak += 1
        else:
            if zero_vol_streak >= min_zero_vol_days:
                for j in range(i - zero_vol_streak, i):
                    halt_dates.add(kline.index[j])
            zero_vol_streak = 0
    # Handle trailing halt
    if zero_vol_streak >= min_zero_vol_days:
        for j in range(len(kline) - zero_vol_streak, len(kline)):
            halt_dates.add(kline.index[j])
    return halt_dates


def validate_and_fix_klines(
    klines: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """验证并修复所有 K 线数据

    Args:
        klines: K 线数据字典 {code: DataFrame}

    Returns:
        dict: 修复后的 K 线数据字典
    """
    fixed_klines = {}
    fixed_count = 0
    failed_count = 0

    for code, kline in klines.items():
        try:
            # 修复日期格式
            fixed = fix_kline_dates(kline, code)

            # 验证数据
            if validate_kline(fixed, code):
                fixed_klines[code] = fixed
                fixed_count += 1
            else:
                logger.warning("Invalid kline data for %s, skipping", code)
                failed_count += 1
        except Exception as e:
            logger.error("Failed to process kline for %s: %s", code, e)
            failed_count += 1

    logger.info(
        "Kline validation: %d fixed, %d failed, %d total",
        fixed_count,
        failed_count,
        len(klines),
    )

    return fixed_klines
