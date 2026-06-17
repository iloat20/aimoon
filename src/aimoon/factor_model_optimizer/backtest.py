"""回测引擎 — 多空组合构建与绩效评估。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class BacktestMetrics:
    """回测绩效指标。"""

    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    turnover: float = 0.0
    n_trading_days: int = 0


@dataclass
class BacktestResult:
    """回测结果。"""

    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    daily_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    monthly_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


class BacktestEngine:
    """多空组合回测引擎。"""

    def __init__(
        self,
        top_quantile: float = 0.20,
        bottom_quantile: float = 0.20,
        transaction_cost_bps: float = 10.0,
        rebalance_freq: int = 5,
    ) -> None:
        self.top_quantile = top_quantile
        self.bottom_quantile = bottom_quantile
        self.transaction_cost_bps = transaction_cost_bps
        self.rebalance_freq = rebalance_freq

    def run(
        self,
        predictions: pd.DataFrame,
        forward_returns: pd.DataFrame,
        close: pd.DataFrame,
    ) -> BacktestResult:
        """运行回测。

        Parameters
        ----------
        predictions : pd.DataFrame
            模型预测值 (date x symbol)。
        forward_returns : pd.DataFrame
            前向收益率 (date x symbol)，与预测对齐。
        close : pd.DataFrame
            收盘价 (date x symbol)。

        Returns
        -------
        BacktestResult
        """
        dates = sorted(predictions.index)
        returns_list: list[float] = []
        position_records: list[dict[str, Any]] = []
        prev_positions: dict[str, float] = {}

        for i, date in enumerate(dates):
            if i % self.rebalance_freq != 0 and i > 0:
                # 不再平衡日：持仓不变，计算收益
                if prev_positions:
                    day_ret = sum(
                        prev_positions[sym] * forward_returns.loc[date, sym]
                        for sym in prev_positions
                        if sym in forward_returns.columns
                        and not pd.isna(forward_returns.loc[date, sym])
                    )
                    returns_list.append(day_ret)
                else:
                    returns_list.append(0.0)
                continue

            # 再平衡日
            preds = predictions.loc[date].dropna()
            if len(preds) < 10:
                returns_list.append(0.0)
                continue

            # 排序
            n = len(preds)
            n_top = max(1, int(n * self.top_quantile))
            n_bottom = max(1, int(n * self.bottom_quantile))

            top_symbols = preds.nlargest(n_top).index.tolist()
            bottom_symbols = preds.nsmallest(n_bottom).index.tolist()

            # 等权多空
            new_positions: dict[str, float] = {}
            for sym in top_symbols:
                new_positions[sym] = 1.0 / n_top
            for sym in bottom_symbols:
                new_positions[sym] = -1.0 / n_bottom

            # 计算换手率
            turnover = self._compute_turnover(prev_positions, new_positions)

            # 计算当日收益（用前向收益率）
            day_ret = sum(
                new_positions[sym] * forward_returns.loc[date, sym]
                for sym in new_positions
                if sym in forward_returns.columns and not pd.isna(forward_returns.loc[date, sym])
            )

            # 扣除交易成本
            cost = turnover * self.transaction_cost_bps / 10000.0
            day_ret -= cost

            returns_list.append(day_ret)
            position_records.append(
                {
                    "date": date,
                    "n_long": n_top,
                    "n_short": n_bottom,
                    "turnover": turnover,
                    "cost": cost,
                }
            )
            prev_positions = new_positions

        # 构建结果
        daily_returns = pd.Series(returns_list, index=dates[: len(returns_list)])
        equity = (1.0 + daily_returns).cumprod()

        metrics = self._compute_metrics(daily_returns)
        positions_df = pd.DataFrame(position_records)
        if not positions_df.empty:
            positions_df = positions_df.set_index("date")

        # 月度收益
        monthly = daily_returns.resample("ME").apply(lambda x: (1.0 + x).prod() - 1.0)

        return BacktestResult(
            metrics=metrics,
            equity_curve=equity,
            daily_returns=daily_returns,
            positions=positions_df,
            monthly_returns=monthly,
        )

    @staticmethod
    def _compute_turnover(prev: dict[str, float], new: dict[str, float]) -> float:
        """计算换手率（买卖总量 / 2）。"""
        all_syms = set(prev.keys()) | set(new.keys())
        turnover = sum(abs(new.get(sym, 0.0) - prev.get(sym, 0.0)) for sym in all_syms)
        return turnover / 2.0

    def _compute_metrics(self, returns: pd.Series) -> BacktestMetrics:
        """计算全部绩效指标。"""
        if len(returns) < 10:
            return BacktestMetrics()

        n_days = len(returns)
        total_return = (1.0 + returns).prod() - 1.0
        ann_factor = 252.0 / n_days
        annual_return = (1.0 + total_return) ** ann_factor - 1.0
        annual_vol = returns.std(ddof=1) * np.sqrt(252)
        sharpe = (
            returns.mean() / returns.std(ddof=1) * np.sqrt(252) if returns.std(ddof=1) > 0 else 0.0
        )

        # 最大回撤
        cum = (1.0 + returns).cumprod()
        running_max = cum.cummax()
        drawdown = cum / running_max - 1.0
        max_drawdown = drawdown.min()

        # Calmar
        calmar = annual_return / abs(max_drawdown) if abs(max_drawdown) > 1e-8 else 0.0

        # 胜率
        win_rate = (returns > 0).sum() / (returns != 0).sum() if (returns != 0).any() else 0.0

        # 盈亏比
        avg_win = returns[returns > 0].mean() if (returns > 0).any() else 0.0
        avg_loss = abs(returns[returns < 0].mean()) if (returns < 0).any() else 1.0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

        return BacktestMetrics(
            total_return=float(total_return),
            annual_return=float(annual_return),
            annual_volatility=float(annual_vol),
            sharpe_ratio=float(sharpe),
            max_drawdown=float(max_drawdown),
            calmar_ratio=float(calmar),
            win_rate=float(win_rate),
            profit_loss_ratio=float(profit_loss_ratio),
            turnover=0.0,  # 在外部计算
            n_trading_days=n_days,
        )
