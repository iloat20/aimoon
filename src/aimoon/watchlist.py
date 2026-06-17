"""自选股票管理 -- 支持手动添加/删除/列出自选股票"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_WATCHLIST_FILE = Path(".aimoon_watchlist.json")


def _load_watchlist() -> set[str]:
    """加载自选股票列表。"""
    if not _WATCHLIST_FILE.exists():
        return set()
    try:
        data = json.loads(_WATCHLIST_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(str(code) for code in data)
        return set()
    except Exception as e:
        logger.warning("加载自选股票失败: %s", e)
        return set()


def _save_watchlist(codes: set[str]) -> None:
    """保存自选股票列表。"""
    try:
        _WATCHLIST_FILE.write_text(
            json.dumps(sorted(codes), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("保存自选股票: %d 只", len(codes))
    except Exception as e:
        logger.error("保存自选股票失败: %s", e)


def add_watchlist(codes: list[str]) -> tuple[bool, str]:
    """添加股票到自选列表。

    Args:
        codes: 股票代码列表 (如 ['000001', '600036'])

    Returns:
        (success, message)
    """
    if not codes:
        return False, "未提供股票代码"

    # 验证股票代码格式
    valid_codes = []
    invalid_codes = []
    for code in codes:
        code = str(code).strip()
        if code and len(code) == 6 and code.isdigit():
            valid_codes.append(code)
        else:
            invalid_codes.append(code)

    if not valid_codes:
        return False, f"所有股票代码格式无效: {invalid_codes}"

    # 加载现有自选列表
    current = _load_watchlist()
    original_size = len(current)

    # 添加新股票
    current.update(valid_codes)
    new_size = len(current)
    added_count = new_size - original_size

    # 保存
    _save_watchlist(current)

    message = f"已添加 {added_count} 只股票到自选列表"
    if invalid_codes:
        message += f" (跳过无效代码: {invalid_codes})"
    message += f"\n自选列表共 {new_size} 只股票"

    return True, message


def remove_watchlist(codes: list[str]) -> tuple[bool, str]:
    """从自选列表删除股票。

    Args:
        codes: 股票代码列表

    Returns:
        (success, message)
    """
    if not codes:
        return False, "未提供股票代码"

    # 加载现有自选列表
    current = _load_watchlist()
    if not current:
        return False, "自选列表为空"

    # 删除指定股票
    codes_to_remove = set(str(code).strip() for code in codes if code)
    removed = current & codes_to_remove
    current -= codes_to_remove

    # 保存
    _save_watchlist(current)

    if not removed:
        return False, f"未找到指定股票: {codes}"

    return True, f"已从自选列表删除 {len(removed)} 只股票，剩余 {len(current)} 只"


def list_watchlist() -> tuple[bool, list[str] | str]:
    """列出所有自选股票。

    Returns:
        (success, codes_list_or_error_message)
    """
    current = _load_watchlist()
    if not current:
        return True, []
    return True, sorted(current)


def clear_watchlist() -> tuple[bool, str]:
    """清空自选列表。

    Returns:
        (success, message)
    """
    current = _load_watchlist()
    if not current:
        return True, "自选列表已为空"

    _save_watchlist(set())
    return True, f"已清空自选列表（删除 {len(current)} 只股票）"


def get_watchlist_codes() -> set[str]:
    """获取自选股票代码集合（供持仓池使用）。"""
    return _load_watchlist()


def get_all_codes_for_pool(pool_codes: set[str]) -> set[str]:
    """合并机构持仓池和自选股票。

    Args:
        pool_codes: 机构持仓池股票代码

    Returns:
        合并后的股票代码集合
    """
    watchlist = get_watchlist_codes()
    if watchlist:
        logger.info("合并持仓池: 机构 %d 只 + 自选 %d 只", len(pool_codes), len(watchlist))
    return pool_codes | watchlist
