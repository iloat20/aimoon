"""回测引擎"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from aimoon.config import Config
from aimoon.screener import screen_stock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeRecord:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float


@dataclass(frozen=True)
class BacktestResult:
    code: str
    total_return: float
    win_rate: float
    max_drawdown: float
    trade_count: int
    trades: tuple[TradeRecord, ...]


class BacktestEngine:
    def __init__(self, cfg: Config, hold_days: int = 5) -> None:
        self.cfg = cfg
        self.hold_days = hold_days

    def run(self, code: str, name: str, kline: pd.DataFrame) -> BacktestResult:
        min_window = self.cfg.ma_long
        if len(kline) < min_window + self.hold_days:
            return BacktestResult(code, 0.0, 0.0, 0.0, 0, ())
        trades: list[TradeRecord] = []
        dates = kline.index.tolist()
        in_trade = False
        exit_idx = 0
        for i in range(min_window, len(kline) - self.hold_days):
            if in_trade and i < exit_idx:
                continue
            in_trade = False
            window = kline.iloc[:i + 1]
            scored = screen_stock(code, name, window)
            if scored is None or scored.total_score < 2:
                continue
            entry_price = float(kline["close"].iloc[i])
            exit_i = min(i + self.hold_days, len(kline) - 1)
            exit_price = float(kline["close"].iloc[exit_i])
            ret = (exit_price - entry_price) / entry_price * 100
            trades.append(TradeRecord(str(dates[i].date()), str(dates[exit_i].date()), entry_price, exit_price, ret))
            in_trade = True
            exit_idx = exit_i + 1
        return self._metrics(code, trades, kline)

    def _metrics(self, code: str, trades: list[TradeRecord], kline: pd.DataFrame) -> BacktestResult:
        if not trades:
            return BacktestResult(code, 0.0, 0.0, 0.0, 0, ())
        total_ret = sum(t.return_pct for t in trades)
        win_rate = sum(1 for t in trades if t.return_pct > 0) / len(trades)
        equity = [100.0]
        trade_idx = 0
        for i in range(1, len(kline)):
            if trade_idx < len(trades) and str(kline.index[i].date()) == trades[trade_idx].exit_date:
                equity.append(equity[-1] * (1 + trades[trade_idx].return_pct / 100))
                trade_idx += 1
            else:
                equity.append(equity[-1])
        peak = max_dd = 0.0
        for v in equity:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak if peak > 0 else 0)
        return BacktestResult(code, total_ret, win_rate, max_dd, len(trades), tuple(trades))
