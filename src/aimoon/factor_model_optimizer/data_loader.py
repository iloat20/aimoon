"""数据加载器 — 从 CSV 读取 OHLCV 面板数据。"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume", "symbol"]


def load_ohlcv_csv(
    path: str | Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    """从 CSV 加载 OHLCV 数据，返回面板字典。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    logger.info("Loading OHLCV data from %s", path)
    df = pd.read_csv(path, parse_dates=["date"], dtype={"symbol": str})

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    if start_date:
        df = df[df["date"] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]

    if df.empty:
        raise ValueError("No data after date filtering")

    panels: dict[str, pd.DataFrame] = {}
    for col in ("open", "high", "low", "close", "volume"):
        wide = df.pivot_table(index="date", columns="symbol", values=col, aggfunc="first")
        panels[col] = wide.sort_index()

    if "amount" in df.columns:
        panels["amount"] = df.pivot_table(
            index="date",
            columns="symbol",
            values="amount",
            aggfunc="first",
        ).sort_index()

    n_symbols = panels["close"].shape[1]
    n_dates = panels["close"].shape[0]
    logger.info(
        "Loaded panel: %d symbols x %d dates (%s ~ %s)",
        n_symbols,
        n_dates,
        panels["close"].index.min().strftime("%Y-%m-%d"),
        panels["close"].index.max().strftime("%Y-%m-%d"),
    )
    return panels
