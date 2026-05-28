"""回测引擎 - 在历史数据上模拟策略表现"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from aimoon.config import CONFIG
from aimoon.strategies.base import Strategy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeRecord:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    signal: str


@dataclass(frozen=True)
class BacktestResult:
    stock_code: str
    stock_name: str
    total_return: float
    win_rate: float
    max_drawdown: float
    trade_count: int
    trades: list[TradeRecord]


class BacktestEngine:
    """在历史K线上逐日回测策略。"""

    def __init__(self, strategy: Strategy, hold_days: int = 5) -> None:
        self.strategy = strategy
        self.hold_days = hold_days

    def run(self, code: str, name: str, kline: pd.DataFrame) -> BacktestResult:
        """逐日滚动窗口运行策略。"""
        min_window = CONFIG.ma_long
        if len(kline) < min_window + self.hold_days:
            return BacktestResult(
                stock_code=code, stock_name=name,
                total_return=0.0, win_rate=0.0, max_drawdown=0.0,
                trade_count=0, trades=[],
            )

        trades: list[TradeRecord] = []
        dates = kline.index.tolist()
        in_trade = False
        exit_idx = 0

        for i in range(min_window, len(kline) - self.hold_days):
            if in_trade and i < exit_idx:
                continue
            in_trade = False

            window = kline.iloc[:i + 1]
            sig = self.strategy.score(code, name, window)
            if sig is None or sig.total_score < 2:
                continue

            entry_price = float(kline["close"].iloc[i])
            exit_i = min(i + self.hold_days, len(kline) - 1)
            exit_price = float(kline["close"].iloc[exit_i])
            ret = (exit_price - entry_price) / entry_price * 100

            trades.append(TradeRecord(
                entry_date=str(dates[i].date()),
                exit_date=str(dates[exit_i].date()),
                entry_price=entry_price,
                exit_price=exit_price,
                return_pct=ret,
                signal=", ".join(sig.signals[:3]),
            ))
            in_trade = True
            exit_idx = exit_i + 1

        return self._calc_metrics(code, name, trades, kline)

    def run_batch(self, stocks: dict[str, tuple[str, pd.DataFrame]]) -> list[BacktestResult]:
        """批量回测。stocks: {code: (name, kline_df)}"""
        return [self.run(code, name, kline) for code, (name, kline) in stocks.items()]

    def _calc_metrics(
        self, code: str, name: str, trades: list[TradeRecord], kline: pd.DataFrame,
    ) -> BacktestResult:
        if not trades:
            return BacktestResult(
                stock_code=code, stock_name=name,
                total_return=0.0, win_rate=0.0, max_drawdown=0.0,
                trade_count=0, trades=[],
            )
        total_ret = sum(t.return_pct for t in trades)
        wins = sum(1 for t in trades if t.return_pct > 0)
        win_rate = wins / len(trades)

        # 最大回撤计算
        equity = [100.0]
        trade_idx = 0
        for i in range(1, len(kline)):
            if trade_idx < len(trades) and str(kline.index[i].date()) == trades[trade_idx].exit_date:
                equity.append(equity[-1] * (1 + trades[trade_idx].return_pct / 100))
                trade_idx += 1
            else:
                equity.append(equity[-1])
        peak = equity[0]
        max_dd = 0.0
        for v in equity:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd

        return BacktestResult(
            stock_code=code, stock_name=name,
            total_return=total_ret,
            win_rate=win_rate,
            max_drawdown=max_dd,
            trade_count=len(trades),
            trades=trades,
        )
