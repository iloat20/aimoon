"""Demo 模式模拟数据生成 — 使用机构持仓池真实股票代码"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _load_pool_codes(n: int = 30) -> list[str]:
    """从持仓池加载股票代码，取前 n 只。"""
    try:
        from aimoon.data.filters import get_holdings_pool
        from aimoon.config import Config
        pool = get_holdings_pool(Config())
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
        "000001", "000002", "000333", "000568", "000651", "000725", "000858",
        "002049", "002230", "002304", "002415", "002475", "002594", "002714",
        "300059", "300064", "300124", "300750", "600030", "600036", "600276",
        "600309", "600519", "600585", "600887", "601012", "601166", "601318",
        "601398", "601888",
    ]


def generate_demo(n_stocks: int = 30) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """生成模拟数据。优先使用持仓池真实股票代码。"""
    np.random.seed(42)

    codes = _load_pool_codes(n_stocks)
    if not codes:
        codes = _fallback_codes()[:n_stocks]

    # 生成 spot_df（模拟实时行情）
    rows = []
    for code in codes:
        price = float(np.random.uniform(10, 200))
        rows.append({
            "stock_code": code, "stock_name": code,  # demo 用代码代替名称
            "price": price,
            "pct_change": float(np.random.uniform(-5, 5)),
            "turnover": float(np.random.uniform(1, 15)),
            "volume": float(np.random.randint(100000, 10000000)),
            "amount": float(np.random.randint(10000000, 1000000000)),
            "amplitude": float(np.random.uniform(1, 8)),
            "high": price * 1.02, "low": price * 0.98,
            "open": price * 1.001, "prev_close": price * 0.99,
            "volume_ratio": float(np.random.uniform(0.5, 3)),
            "pe": float(np.random.uniform(5, 50)),
            "pb": float(np.random.uniform(0.5, 10)),
            "total_market_cap": float(np.random.uniform(5e9, 3e12)),
            "float_market_cap": float(np.random.uniform(1e9, 2e12)),
            "pct_60d": float(np.random.uniform(-30, 30)),
            "pct_ytd": float(np.random.uniform(-20, 50)),
        })
    spot_df = pd.DataFrame(rows)

    # 生成 K 线（模拟 260 个交易日）
    klines: dict[str, pd.DataFrame] = {}
    dates = pd.date_range(end=pd.Timestamp.today(), periods=260, freq="B")
    n = len(dates)
    for code in codes:
        c = np.random.uniform(10, 200)
        close = np.maximum(c + np.cumsum(np.random.randn(n) * c * 0.02), 1.0)
        high = close + np.abs(np.random.randn(n) * close * 0.02)
        low = close - np.abs(np.random.randn(n) * close * 0.02)
        open_ = close + np.random.randn(n) * close * 0.01
        vol = np.random.randint(100000, 10000000, n).astype(float)
        df = pd.DataFrame({
            "open": open_, "close": close, "high": high, "low": low,
            "volume": vol, "turnover": np.random.uniform(0.5, 15, n),
            "pct_change": np.random.randn(n) * 3,
        }, index=dates)
        df.index.name = "date"
        klines[code] = df

    return spot_df, klines
