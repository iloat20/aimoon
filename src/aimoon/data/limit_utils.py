"""A股涨跌停限制工具 — 检测涨跌停状态并判断可成交性。

功能：
1. 判断给定价格是否达到涨跌停限制
2. 判断给定日期是否可买入（排除涨停）
3. 判断给定日期是否可卖出（排除跌停）
4. 计算理论可成交量（排除涨跌停日的流动性限制）
"""

from __future__ import annotations

import pandas as pd

# A 股涨跌停限制
_NORMAL_LIMIT_PCT = 0.10  # ±10% 涨跌停
_ST_LIMIT_PCT = 0.05  # ±5% ST 涨跌停
_STAR_LIMIT_PCT = 0.20  # 科创板/创业板 ±20%
_STAR_ST_LIMIT_PCT = 0.10  # 科创板/创业板 ST ±10%


def detect_price_limit(
    prev_close: float,
    current_price: float,
    is_st: bool = False,
    is_star: bool = False,
) -> str | None:
    """检测价格是否达到涨跌停限制。

    Args:
        prev_close: 前一日收盘价
        current_price: 当前价格
        is_st: 是否为 ST 股票
        is_star: 是否为科创板/创业板股票

    Returns:
        "limit_up": 涨停
        "limit_down": 跌停
        None: 未触及涨跌停
    """
    if prev_close <= 0:
        return None

    pct_change = (current_price - prev_close) / prev_close

    if is_star:
        limit_up = _STAR_ST_LIMIT_PCT if is_st else _STAR_LIMIT_PCT
    else:
        limit_up = _ST_LIMIT_PCT if is_st else _NORMAL_LIMIT_PCT
    limit_down = limit_up

    if pct_change >= limit_up * 0.995:
        return "limit_up"
    if pct_change <= -limit_down * 0.995:
        return "limit_down"
    return None


def is_limit_up(kline: pd.DataFrame, date, is_st: bool = False, is_star: bool = False) -> bool:
    """判断指定日期是否涨停。"""
    if date not in kline.index:
        return False
    idx = kline.index.get_loc(date)
    if idx < 1:
        return False
    prev_close = float(kline["close"].iloc[idx - 1])
    current_close = float(kline["close"].iloc[idx])
    result = detect_price_limit(prev_close, current_close, is_st=is_st, is_star=is_star)
    return result == "limit_up"


def is_limit_down(kline: pd.DataFrame, date, is_st: bool = False, is_star: bool = False) -> bool:
    """判断指定日期是否跌停。"""
    if date not in kline.index:
        return False
    idx = kline.index.get_loc(date)
    if idx < 1:
        return False
    prev_close = float(kline["close"].iloc[idx - 1])
    current_close = float(kline["close"].iloc[idx])
    result = detect_price_limit(prev_close, current_close, is_st=is_st, is_star=is_star)
    return result == "limit_down"


def _is_limit_at_open(
    kline: pd.DataFrame, date, check_up: bool, is_st: bool = False, is_star: bool = False
) -> bool:
    """Check if the OPEN price of `date` hits limit-up/down.

    Uses prev_close → current_open, which is knowable at the open.
    Unlike is_limit_up/is_limit_down which use current_close (look-ahead bias).
    """
    if date not in kline.index or "open" not in kline.columns:
        return False
    idx = kline.index.get_loc(date)
    if idx < 1:
        return False
    prev_close = float(kline["close"].iloc[idx - 1])
    current_open = float(kline["open"].iloc[idx])
    result = detect_price_limit(prev_close, current_open, is_st=is_st, is_star=is_star)
    target = "limit_up" if check_up else "limit_down"
    return result == target


def can_buy_at_open(kline: pd.DataFrame, date, is_st: bool = False, is_star: bool = False) -> bool:
    """判断是否可以在指定日期开盘买入。开盘涨停时无法买入（封板）。

    使用开盘价检查（前收盘 vs 现开盘），在开盘时即可获知。
    """
    return not _is_limit_at_open(kline, date, check_up=True, is_st=is_st, is_star=is_star)


def can_sell_at_open(kline: pd.DataFrame, date, is_st: bool = False, is_star: bool = False) -> bool:
    """判断是否可以在指定日期开盘卖出。开盘跌停时无法卖出（封板）。

    使用开盘价检查（前收盘 vs 现开盘），在开盘时即可获知。
    """
    return not _is_limit_at_open(kline, date, check_up=False, is_st=is_st, is_star=is_star)


def can_sell_at_close(
    kline: pd.DataFrame, date, is_st: bool = False, is_star: bool = False
) -> bool:
    """判断是否可以在指定日期收盘卖出。收盘跌停时无法卖出（封板）。

    使用收盘价检查（前收盘 vs 现收盘），仅在收盘后可知。

    Note: For open-based exit decisions, use can_sell_at_open instead
    to avoid look-ahead bias.
    """
    return not is_limit_down(kline, date, is_st=is_st, is_star=is_star)


def check_min_hold_days(entry_date, exit_date) -> bool:
    """检查持仓天数是否满足 T+1 要求。

    A 股实行 T+1 制度：当日买入的股票不可当日卖出。
    卖出日期必须严格晚于买入日期。
    """
    if entry_date is None or exit_date is None:
        return True
    entry_ts = pd.Timestamp(entry_date) if not isinstance(entry_date, pd.Timestamp) else entry_date
    exit_ts = pd.Timestamp(exit_date) if not isinstance(exit_date, pd.Timestamp) else exit_date
    return exit_ts > entry_ts
