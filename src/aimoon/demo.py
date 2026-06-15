"""Demo 模式 — 使用持仓池真实股票代码 + 真实行情数据"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.data.spot import get_spot_for_codes

logger = logging.getLogger(__name__)


def _load_pool_codes(n: int = 30) -> list[str]:
    """从持仓池加载股票代码，取前 n 只。"""
    try:
        pool = get_holdings_pool(Config(), cache_dir=Path(Config().cache_dir))
        if pool:
            codes = sorted(pool)[:n]
            logger.info("Demo 使用持仓池 %d 只股票", len(codes))
            return codes
    except Exception as e:
        logger.debug("加载持仓池失败: %s", e)
    return []


def _fallback_codes() -> list[str]:
    """持仓池不可用时的备用股票代码列表。"""
    return [
        "000001",
        "000002",
        "000333",
        "000568",
        "000651",
        "000725",
        "000858",
        "002049",
        "002230",
        "002304",
        "002415",
        "002475",
        "002594",
        "002714",
        "300059",
        "300064",
        "300124",
        "300750",
        "600030",
        "600036",
        "600276",
        "600309",
        "600519",
        "600585",
        "600887",
        "601012",
        "601166",
        "601318",
        "601398",
        "601888",
    ]


def generate_demo(n_stocks: int = 30) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """生成真实数据。优先使用持仓池真实股票代码 + 真实行情。"""
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # 1. 获取股票代码
    codes = _load_pool_codes(n_stocks)
    if not codes:
        codes = _fallback_codes()[:n_stocks]

    logger.info("Demo: 获取 %d 只股票的真实行情...", len(codes))

    # 2. 获取真实实时行情
    spot_result = get_spot_for_codes(set(codes), cfg)
    if spot_result.is_err():
        logger.error("获取实时行情失败: %s", spot_result.error)  # type: ignore[union-attr]
        return pd.DataFrame(), {}

    spot_df = spot_result.unwrap()
    logger.info("Demo: 获取到 %d 只股票的实时行情", len(spot_df))

    # 3. 获取真实 K 线数据
    klines: dict[str, pd.DataFrame] = {}
    valid_codes = (
        spot_df["stock_code"].tolist() if "stock_code" in spot_df.columns else codes
    )

    for code in valid_codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()

    logger.info("Demo: 获取到 %d 只股票的 K 线数据", len(klines))

    return spot_df, klines
