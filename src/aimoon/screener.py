"""股票筛选器 — 组合评分函数"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.history import get_kline
from aimoon.indicators.technical import TechInd
from aimoon.models import ScoredStock
from aimoon.scoring import collect_signals

logger = logging.getLogger(__name__)


def screen_stock(
    code: str, name: str, kline: pd.DataFrame,
    spot_row: pd.Series | None = None, ctx: dict | None = None,
) -> ScoredStock | None:
    """对单只股票评分。数据不足返回 None。"""
    if kline is None or len(kline) < 60:
        return None
    try:
        ti = TechInd(kline)
    except Exception:
        return None
    signals = collect_signals(ti, code=code, ctx=ctx)
    if not signals:
        return None
    price = float(kline["close"].iloc[-1])
    pct = float(kline["pct_change"].iloc[-1]) if "pct_change" in kline.columns else 0.0
    turnover = float(kline["turnover"].iloc[-1]) if "turnover" in kline.columns else 0.0
    pe = _safe_float(spot_row, "pe")
    pb = _safe_float(spot_row, "pb")
    cap = _safe_float(spot_row, "total_market_cap") / 1e8 if spot_row is not None else 0.0
    return ScoredStock(
        code=code, name=name, price=price,
        pct_change=pct, turnover=turnover,
        pe=pe, pb=pb, market_cap_yi=cap,
        signals=tuple(signals),
    )


def screen_universe(
    universe: pd.DataFrame, cfg: Config,
    cache: DataCache, ctx: dict | None = None,
    klines: dict[str, pd.DataFrame] | None = None,
) -> tuple[list[ScoredStock], dict[str, pd.DataFrame]]:
    """并发评分候选池。返回 (results, kline_tails)。"""
    results: list[ScoredStock] = []
    tails: dict[str, pd.DataFrame] = {}

    def _process(row: pd.Series) -> None:
        code = row["stock_code"]
        name = row["stock_name"]
        kdf = (klines or {}).get(code)
        if kdf is None:
            r = get_kline(code, cfg.history_days, cache)
            if r.is_err():
                return
            kdf = r.unwrap()
        spot_row = row if "pe" in row.index else None
        scored = screen_stock(code, name, kdf, spot_row, ctx)
        if scored:
            results.append(scored)
            tails[code] = kdf.tail(25).copy()

    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        futures = {ex.submit(_process, row): row["stock_code"] for _, row in universe.iterrows()}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                logger.warning("Screen failed: %s", e)
    return results, tails


def _safe_float(row: pd.Series | None, key: str) -> float:
    if row is not None and key in row.index and pd.notna(row[key]):
        return float(row[key])
    return 0.0
