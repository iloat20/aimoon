"""回测引擎 — 单股 + 组合级回测（含 RPS、regime、分类上限评分）"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from aimoon.indicators.technical import TechInd
from aimoon.models import ScoredStock, Signal
from aimoon.scoring import collect_signals, category_capped_score
from aimoon.scoring.reversal import score_reversal

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
    # Vibe-Trading 移植指标
    profit_factor: float = 0.0
    max_consecutive_loss: int = 0
    information_ratio: float = 0.0


def score_stock_fast(
    code: str, name: str, kline: pd.DataFrame,
    ctx: dict | None = None, use_reversal: bool = True,
) -> ScoredStock | None:
    """快速评分（不依赖 screener 模块，避免循环导入）。"""
    if kline is None or len(kline) < 60:
        return None
    try:
        ti = TechInd(kline)
    except Exception:
        return None
    signals = collect_signals(ti, code=code, ctx=ctx, use_reversal=use_reversal)
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
        benchmark_kline: pd.DataFrame | None = None,
    ) -> PortfolioBacktest:
        """组合回测：每 hold_days 天调仓一次，每次选 top N。

        增强：RPS 截面排名、category_capped_score、regime 检测、交易频率限制。
        """
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

        # Trade frequency tracking: code -> rebalance_idx when last exited
        recent_exits: dict[str, int] = {}
        stop_loss_count: dict[str, int] = {}
        rebal_idx = 0

        i = 0
        while i < len(sorted_dates) - self.hold_days:
            rebal_date = sorted_dates[i]
            exit_date_idx = min(i + self.hold_days, len(sorted_dates) - 1)
            exit_date = sorted_dates[exit_date_idx]

            # ── Regime detection from benchmark ──
            effective_positions = self.max_positions
            effective_threshold = self.entry_threshold
            if benchmark_kline is not None:
                regime = _detect_regime_safe(benchmark_kline, rebal_date)
                if regime is not None:
                    if regime.state == "bear":
                        effective_positions = max(1, self.max_positions // 2)
                        effective_threshold = self.entry_threshold + 3
                    elif regime.state == "high_volatility":
                        effective_positions = max(1, self.max_positions * 2 // 3)
                        effective_threshold = self.entry_threshold + 2

            # ── Score all stocks ──
            scored_stocks: list[tuple[str, str, ScoredStock]] = []
            for code, kline in universe_klines.items():
                if rebal_date not in kline.index:
                    continue
                loc = kline.index.get_loc(rebal_date)
                if loc < min_window:
                    continue
                # Skip stocks in trade cooldown
                # Stop-loss exits get 6-period cooldown, regular exits get 2
                if code in recent_exits and (rebal_idx - recent_exits[code]) < 2:
                    continue
                # Blacklist: permanently skip stocks stopped out >= 2 times
                if stop_loss_count.get(code, 0) >= 2:
                    continue
                window = kline.iloc[:loc + 1]
                name = universe_names.get(code, code)
                sector_map = (ctx or {}).get("sector_map", {})
                sector_ctx = {"sector_map": sector_map} if sector_map else None
                scored = score_stock_fast(code, name, window, ctx=sector_ctx)
                if scored:
                    scored_stocks.append((code, name, scored))

            # ── Compute cross-sectional RPS ranks ──
            rps_signals = _compute_rps_rank_signals(scored_stocks)
            enhanced: list[tuple[str, str, float]] = []
            for code, name, scored in scored_stocks:
                extra = rps_signals.get(code, [])
                all_signals = list(scored.signals) + extra
                capped = category_capped_score(all_signals)
                if capped >= effective_threshold:
                    enhanced.append((code, name, capped))

            # ── Take top N ──
            enhanced.sort(key=lambda x: x[2], reverse=True)
            selected = enhanced[:effective_positions]

            # ── Compute portfolio return ──
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
                        # Track exit for trade frequency limit
                        recent_exits[code] = rebal_idx

            equity.append(equity[-1] * (1 + portfolio_return))
            daily_dates.append(exit_date)
            i = exit_date_idx
            rebal_idx += 1

        return self._portfolio_metrics(trades, equity, benchmark_kline)

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
            if trade_idx < len(trades):
                idx_date = kline.index[i]
                idx_str = str(idx_date.date()) if hasattr(idx_date, "date") else str(idx_date)
                if idx_str == trades[trade_idx].exit_date:
                    equity.append(equity[-1] * (1 + trades[trade_idx].return_pct / 100))
                    trade_idx += 1
                    continue
            equity.append(equity[-1])
        peak = max_dd = 0.0
        for v in equity:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak if peak > 0 else 0)
        return BacktestResult(code, total_ret, win_rate, max_dd, len(trades), tuple(trades))

    def _portfolio_metrics(
        self,
        trades: list[TradeRecord],
        equity: list[float],
        benchmark_kline: pd.DataFrame | None = None,
    ) -> PortfolioBacktest:
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

        # 平均持仓天数（从实际交易日期计算）
        avg_hold = _compute_avg_hold_days(trades, hold_days)

        # 盈亏因子（Vibe-Trading 移植）
        profit_factor = _compute_profit_factor(trades)

        # 最大连续亏损次数（Vibe-Trading 移植）
        max_consec_loss = _compute_max_consecutive_loss(trades)

        # 基准比较与信息比率（Vibe-Trading 移植）
        benchmark_return = 0.0
        excess_return = total_ret
        information_ratio = 0.0
        if benchmark_kline is not None and len(benchmark_kline) > 1:
            bench_close = benchmark_kline["close"]
            bench_ret_series = bench_close.pct_change().fillna(0.0)
            benchmark_return = round(float((1 + bench_ret_series).prod() - 1) * 100, 2)
            excess_return = round(total_ret - benchmark_return, 2)
            # 信息比率：超额收益 / 跟踪误差
            port_ret_series = pd.Series(returns)
            n = min(len(port_ret_series), len(bench_ret_series))
            if n > 1:
                active = port_ret_series.iloc[-n:].values - bench_ret_series.iloc[-n:].values
                active_std = float(np.std(active))
                information_ratio = round(
                    float(np.mean(active) / (active_std + 1e-10) * np.sqrt(252 / hold_days)), 4
                )

        return PortfolioBacktest(
            total_return=round(total_ret, 2),
            annual_return=round(annual_ret, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_dd * 100, 2),
            win_rate=round(win_rate, 4),
            trade_count=len(trades),
            avg_hold_days=avg_hold,
            turnover_rate=round(turnover, 2),
            benchmark_return=benchmark_return,
            excess_return=excess_return,
            calmar_ratio=round(calmar, 2),
            trades=tuple(trades),
            equity_curve=tuple(equity),
            profit_factor=profit_factor,
            max_consecutive_loss=max_consec_loss,
            information_ratio=information_ratio,
        )

    def _empty_portfolio(self) -> PortfolioBacktest:
        return PortfolioBacktest(
            total_return=0.0, annual_return=0.0, sharpe_ratio=0.0,
            max_drawdown=0.0, win_rate=0.0, trade_count=0,
            avg_hold_days=0.0, turnover_rate=0.0,
            benchmark_return=0.0, excess_return=0.0, calmar_ratio=0.0,
            trades=(), equity_curve=(100.0,),
            profit_factor=0.0, max_consecutive_loss=0, information_ratio=0.0,
        )


# ── 模块级辅助函数（Vibe-Trading 移植） ──


def _compute_avg_hold_days(trades: list[TradeRecord], fallback: float) -> float:
    """从实际交易日期计算平均持仓天数。"""
    days: list[int] = []
    for t in trades:
        try:
            d1 = datetime.fromisoformat(t.entry_date)
            d2 = datetime.fromisoformat(t.exit_date)
            days.append((d2 - d1).days)
        except (ValueError, TypeError):
            pass
    return round(float(np.mean(days)), 1) if days else fallback


def _compute_profit_factor(trades: list[TradeRecord]) -> float:
    """盈亏因子 = 总盈利 / 总亏损（Vibe-Trading metrics.py 移植）。"""
    gross_profit = sum(t.return_pct for t in trades if t.return_pct > 0)
    gross_loss = abs(sum(t.return_pct for t in trades if t.return_pct < 0))
    if gross_loss < 1e-10:
        return 0.0
    return round(gross_profit / gross_loss, 4)


def _compute_max_consecutive_loss(trades: list[TradeRecord]) -> int:
    """最大连续亏损次数（Vibe-Trading metrics.py 移植）。"""
    max_consec = 0
    cur_consec = 0
    for t in trades:
        if t.return_pct < 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0
    return max_consec


def _compute_rps_rank_signals(
    scored_stocks: list[tuple[str, str, ScoredStock]],
) -> dict[str, list[Signal]]:
    """计算跨截面 RPS 排名信号，返回 code -> [Signal, ...] 映射。

    用 ROC5/ROC10/ROC20 作为 RPS 代理，跨所有候选股票做百分位排名。
    """
    if len(scored_stocks) < 5:
        return {}

    rocs: dict[str, dict[int, float]] = {}
    for code, name, scored in scored_stocks:
        roc_vals: dict[int, float] = {}
        for s in scored.signals:
            # Parse ROC values from signal names
            if "roc5_" in s.name or "roc3_" in s.name:
                try:
                    pct = float(s.label.split("(")[1].rstrip(")%"))
                    roc_vals[5] = pct
                except (IndexError, ValueError):
                    pass
            if "roc10_" in s.name:
                try:
                    pct = float(s.label.split("(")[1].rstrip(")%"))
                    roc_vals[10] = pct
                except (IndexError, ValueError):
                    pass
            if "roc20_" in s.name:
                try:
                    pct = float(s.label.split("(")[1].rstrip(")%"))
                    roc_vals[20] = pct
                except (IndexError, ValueError):
                    pass
        if roc_vals:
            rocs[code] = roc_vals

    if len(rocs) < 5:
        return {}

    result: dict[str, list[Signal]] = {}
    for period in [5, 10, 20]:
        period_rocs = {c: v[period] for c, v in rocs.items() if period in v}
        if len(period_rocs) < 5:
            continue
        sorted_codes = sorted(period_rocs, key=lambda c: period_rocs[c])
        total = len(sorted_codes)
        for rank, code in enumerate(sorted_codes):
            pct_rank = (rank + 1) / total * 100
            if pct_rank >= 90:
                result.setdefault(code, []).append(
                    Signal(f"rps{period}_top10", f"RPS{period} Top10%", +2))
            elif pct_rank >= 75:
                result.setdefault(code, []).append(
                    Signal(f"rps{period}_top25", f"RPS{period} Top25%", +1))
            elif pct_rank <= 10:
                result.setdefault(code, []).append(
                    Signal(f"rps{period}_bot10", f"RPS{period} Bottom10%", -2))
            elif pct_rank <= 25:
                result.setdefault(code, []).append(
                    Signal(f"rps{period}_bot25", f"RPS{period} Bottom25%", -1))

    return result


def _detect_regime_safe(benchmark_kline: pd.DataFrame, as_of_date) -> object | None:
    """安全检测市场状态。返回 MarketRegime 或 None（数据不足时）。"""
    try:
        from aimoon.regime import detect_regime
        if as_of_date not in benchmark_kline.index:
            return None
        loc = benchmark_kline.index.get_loc(as_of_date)
        if loc < 120:
            return None
        window = benchmark_kline.iloc[:loc + 1]
        return detect_regime(window, lookback=120)
    except Exception:
        return None
