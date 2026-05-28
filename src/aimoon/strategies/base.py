"""策略抽象基类"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from aimoon.strategies.screener import SignalScore


class Strategy(ABC):
    """策略基类，所有打分策略实现此接口。"""

    @abstractmethod
    def score(
        self,
        code: str,
        name: str,
        kline: pd.DataFrame,
        spot: pd.Series | None = None,
        market_context: dict | None = None,
    ) -> SignalScore | None:
        """对单只股票打分，返回 None 表示跳过。
        market_context 可包含 sector_map, sector_returns, top_sectors, top_stocks。
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """策略显示名称。"""
