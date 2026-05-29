"""回测引擎 — 单股 + 组合级回测"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from aimoon.indicators.technical import TechInd
from aimoon.models import ScoredStock
from aimoon.scoring import collect_signals

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeRecord:
    """单笔交易记录。"""
    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    cost_pct: float  # 交易成本占比


@dataclass(frozen=True)
class BacktestResult:
    """单股回测结果（兼容旧接口）。"""
    code: str
    total_return: float
    win_rate: float
    max_drawdown: float
    trade_count: int
    trades: tuple[TradeRecord, ...]


@dataclass(frozen=True)
class PortfolioBacktest:
    """组合回测结果。"""
    total_return: float
    annual_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    avg_hold_days: float
    turnover_rate: float
    benchmark_return: float
    excess_return: float
    calmar_ratio: float
    trades: tuple[TradeRecord, ...]
    equity_curve: tuple[float, ...]


def score_stock_fast(code: str, name: str, kline: pd.DataFrame, ctx: dict | None = None) -> ScoredStock | None:
    """快速评分（不依赖 screener 模块，避免循环导入）。"""
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
    return ScoredStock(
        code=code, name=name, price=price,
        pct_change=pct, turnover=turnover,
        signals=tuple(signals),
    )


class BacktestEngine:
    """回测引擎 — 支持单股和组合回测。"""

    def __init__(
        self,
        hold_days: int = 5,
        max_positions: int = 10,
        commission: float = 0.0003,
        slippage: float = 0.001,
        stamp_tax: float = 0.0005,
        entry_threshold: float = 2.0,
    ) -> None:
        self.hold_days = hold_days
        self.max_positions = max_positions
        self.commission = commission
        self.slippage = slippage
        self.stamp_tax = stamp_tax
        self.entry_threshold = entry_threshold

    def _buy_cost(self) -> float:
        """买入成本率（佣金 + 滑点）。"""
        return self.commission + self.slippage

    def _sell_cost(self) -> float:
        """卖出成本率（佣金 + 印花税 + 滑点）。"""
        return self.commission + self.stamp_tax + self.slippage

    # ------------------------------------------------------------------
    # 单股回测（兼容旧接口）
    # ------------------------------------------------------------------

    def run_single(self, code: str, name: str, kline: pd.DataFrame) -> BacktestResult:
        """单股回测。"""
        min_window = 60
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
            scored = score_stock_fast(code, name, window)
            if scored is None or scored.total_score < self.entry_threshold:
                continue

            entry_price = float(kline["close"].iloc[i])
            entry_cost = entry_price * self._buy_cost()
            exit_i = min(i + self.hold_days, len(kline) - 1)
            exit_price = float(kline["close"].iloc[exit_i])
            exit_cost = exit_price * self._sell_cost()
            gross_ret = (exit_price - entry_price) / entry_price * 100
            net_ret = gross_ret - (entry_cost + exit_cost) / entry_price * 100
            cost_pct = (entry_cost + exit_cost) / entry_price * 100

            trades.append(TradeRecord(
                code=code, name=name,
                entry_date=str(dates[i].date()) if hasattr(dates[i], 'date') else str(dates[i]),
                exit_date=str(dates[exit_i].date()) if hasattr(dates[exit_i], 'date') else str(dates[exit_i]),
                entry_price=entry_price, exit_price=exit_price,
                return_pct=net_ret, cost_pct=cost_pct,
            ))
            in_trade = True
            exit_idx = exit_i + 1

        return self._single_metrics(code, trades, kline)

    # ------------------------------------------------------------------
    # 组合回测
    # ------------------------------------------------------------------

    def run_portfolio(
        self,
        universe_klines: dict[str, pd.DataFrame],
        universe_names: dict[str, str],
        ctx: dict | None = None,
    ) -> PortfolioBacktest:
        """组合回测：每 hold_days 天调仓一次，每次选 top N。"""
        min_window = 60
        if not universe_klines:
            return self._empty_portfolio()

        # 找共同日期
        common_dates = None
        for code, kline in universe_klines.items():
            dates = set(kline.index[min_window:])
            common_dates = dates if common_dates is None else common_dates & dates
        if not common_dates or len(common_dates) < self.hold_days:
            return self._empty_portfolio()

        sorted_dates = sorted(common_dates)
        trades: list[TradeRecord] = []
        equity = [100.0]
        daily_dates = [sorted_dates[0]]

        i = 0
        while i < len(sorted_dates) - self.hold_days:
            rebal_date = sorted_dates[i]
            exit_date_idx = min(i + self.hold_days, len(sorted_dates) - 1)
            exit_date = sorted_dates[exit_date_idx]

            # 评分
            scored_stocks: list[tuple[str, str, float]] = []  # (code, name, score)
            for code, kline in universe_klines.items():
                if rebal_date not in kline.index:
                    continue
                loc = kline.index.get_loc(rebal_date)
                if loc < min_window:
                    continue
                window = kline.iloc[:loc + 1]
                name = universe_names.get(code, code)
                scored = score_stock_fast(code, name, window, ctx)
                if scored and scored.total_score >= self.entry_threshold:
                    scored_stocks.append((code, name, scored.total_score))

            # 取 top N
            scored_stocks.sort(key=lambda x: x[2], reverse=True)
            selected = scored_stocks[:self.max_positions]

            # 计算组合收益
            portfolio_return = 0.0
            if selected:
                pos_size = 1.0 / len(selected)
                for code, name, _ in selected:
                    kline = universe_klines[code]
                    if rebal_date in kline.index and exit_date in kline.index:
                        entry_price = float(kline.loc[rebal_date, "close"])
                        exit_price = float(kline.loc[exit_date, "close"])
                        entry_cost = entry_price * self._buy_cost()
                        exit_cost = exit_price * self._sell_cost()
                        gross_ret = (exit_price - entry_price) / entry_price
                        net_ret = gross_ret - (entry_cost + exit_cost) / entry_price
                        cost_pct = (entry_cost + exit_cost) / entry_price * 100
                        portfolio_return += net_ret * pos_size

                        trades.append(TradeRecord(
                            code=code, name=name,
                            entry_date=str(rebal_date.date()) if hasattr(rebal_date, 'date') else str(rebal_date),
                            exit_date=str(exit_date.date()) if hasattr(exit_date, 'date') else str(exit_date),
                            entry_price=entry_price, exit_price=exit_price,
                            return_pct=net_ret * 100, cost_pct=cost_pct,
                        ))

            equity.append(equity[-1] * (1 + portfolio_return))
            daily_dates.append(exit_date)
            i = exit_date_idx

        return self._portfolio_metrics(trades, equity)

    # ------------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------------

    def _single_metrics(self, code: str, trades: list[TradeRecord], kline: pd.DataFrame) -> BacktestResult:
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

    def _portfolio_metrics(self, trades: list[TradeRecord], equity: list[float]) -> PortfolioBacktest:
        if not trades:
            return self._empty_portfolio()

        total_ret = (equity[-1] / equity[0] - 1) * 100
        n_periods = len(equity) - 1
        hold_days = self.hold_days

        # 年化收益
        total_days = n_periods * hold_days
        annual_ret = ((equity[-1] / equity[0]) ** (252 / max(total_days, 1)) - 1) * 100 if total_days > 0 else 0.0

        # 夏普比率（年化）
        returns = [(equity[i] / equity[i - 1] - 1) for i in range(1, len(equity))]
        if returns:
            mean_ret = np.mean(returns) * 252 / hold_days
            std_ret = np.std(returns) * np.sqrt(252 / hold_days)
            sharpe = mean_ret / std_ret if std_ret > 0 else 0.0
        else:
            sharpe = 0.0

        # 最大回撤
        peak = max_dd = 0.0
        for v in equity:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak if peak > 0 else 0)

        # 胜率
        win_rate = sum(1 for t in trades if t.return_pct > 0) / len(trades)

        # 换手率
        turnover = len(set(t.code for t in trades)) / max(n_periods, 1)

        # Calmar
        calmar = annual_ret / (max_dd * 100) if max_dd > 0 else 0.0

        return PortfolioBacktest(
            total_return=round(total_ret, 2),
            annual_return=round(annual_ret, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_dd * 100, 2),
            win_rate=round(win_rate, 4),
            trade_count=len(trades),
            avg_hold_days=hold_days,
            turnover_rate=round(turnover, 2),
            benchmark_return=0.0,
            excess_return=round(total_ret, 2),
            calmar_ratio=round(calmar, 2),
            trades=tuple(trades),
            equity_curve=tuple(equity),
        )

    def _empty_portfolio(self) -> PortfolioBacktest:
        return PortfolioBacktest(
            total_return=0.0, annual_return=0.0, sharpe_ratio=0.0,
            max_drawdown=0.0, win_rate=0.0, trade_count=0,
            avg_hold_days=0.0, turnover_rate=0.0,
            benchmark_return=0.0, excess_return=0.0, calmar_ratio=0.0,
            trades=(), equity_curve=(100.0,),
        )
