"""Stock screener - strategy-based filtering"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from aimoon.config import CONFIG

if TYPE_CHECKING:
    from aimoon.strategies.base import Strategy

logger = logging.getLogger(__name__)


@dataclass
class SignalScore:
    stock_code: str
    stock_name: str
    price: float
    pct_change: float
    turnover: float
    pe: float = 0.0
    pb: float = 0.0
    total_market_cap_yi: float = 0.0
    float_market_cap_yi: float = 0.0
    trend_score: int = 0
    rsi_score: int = 0
    macd_score: int = 0
    kdj_score: int = 0
    volume_score: int = 0
    boll_score: int = 0
    total_score: int = 0
    signals: list[str] = field(default_factory=list)
    suggestion: str = "观望"
    confidence: str = "低"


class StockScreener:
    def __init__(self, strategies: list[Strategy] | None = None) -> None:
        self._strategies = strategies
        self.results: list[SignalScore] = []
        self._lock = threading.Lock()

    def _get_strategies(self) -> list[Strategy]:
        if self._strategies is None:
            from aimoon.strategies.technical import TechnicalStrategy
            self._strategies = [TechnicalStrategy()]
        return self._strategies

    def screen_stock(
        self, stock_code: str, stock_name: str,
        kline_df: pd.DataFrame, spot_row: pd.Series | None = None,
    ) -> SignalScore | None:
        for strategy in self._get_strategies():
            result = strategy.score(stock_code, stock_name, kline_df, spot_row)
            if result:
                self.add_result(result)
                return result
        return None

    def add_result(self, score: SignalScore) -> None:
        with self._lock:
            self.results.append(score)

    def get_top_picks(self, n: int | None = None) -> list[SignalScore]:
        n = n or CONFIG.top_n
        with self._lock:
            sorted_results = sorted(self.results, key=lambda x: x.total_score, reverse=True)
        return sorted_results[:n]
