"""Enhanced backtest engine with event-driven simulation and risk management."""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from dataclasses import dataclass
from aimoon.indicators.technical import TechInd
from aimoon.risk import RiskLimits, kelly_criterion, volatility_position_size
from aimoon.scoring import collect_signals, category_capped_score
from aimoon.backtest import _detect_regime_safe

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnhancedTrade:
    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    cost_pct: float
    exit_reason: str
    hold_days: int


@dataclass(frozen=True)
class EnhancedPortfolioResult:
    total_return: float
    annual_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    avg_hold_days: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    benchmark_return: float
    excess_return: float
    calmar_ratio: float
    trades: tuple
    equity_curve: tuple
    drawdown_curve: tuple
    # Vibe-Trading 移植指标
    profit_loss_ratio: float = 0.0
    max_consecutive_loss: int = 0
    information_ratio: float = 0.0


class EnhancedBacktestEngine:
    """Event-driven backtest with stop-loss, take-profit, position sizing."""

    def __init__(self, hold_days=10, max_positions=5, commission=0.0003,
                 slippage=0.001, stamp_tax=0.0005, entry_threshold=18,
                 stop_loss_pct=0.05, take_profit_pct=0.20,
                 risk_limits=None, rebalance_freq=3, benchmark_code=None,
                 max_sector_pct=0.30, use_reversal=False, use_alpha=False,
                 use_kelly=False, ic_weights=None, backtest_start_date=None,
                 exit_ratio=0.5):
        self.hold_days = hold_days
        self.max_positions = max_positions
        self.commission = commission
        self.slippage = slippage
        self.stamp_tax = stamp_tax
        self.entry_threshold = entry_threshold
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.risk_limits = risk_limits or RiskLimits()
        self.rebalance_freq = rebalance_freq
        self.benchmark_code = benchmark_code
        self.max_sector_pct = max_sector_pct
        self.use_reversal = use_reversal
        self.use_alpha = use_alpha
        self.use_kelly = use_kelly
        self.ic_weights = ic_weights
        self.backtest_start_date = backtest_start_date
        self.exit_threshold = int(entry_threshold * exit_ratio)

    def _buy_cost(self):
        return self.commission + self.slippage

    def _sell_cost(self):
        return self.commission + self.stamp_tax + self.slippage

    def _empty_result(self):
        return EnhancedPortfolioResult(
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0, (), (100.0,), (0.0,),
            profit_loss_ratio=0.0, max_consecutive_loss=0, information_ratio=0.0)

    def _score_stock(self, code, name, kline, ctx=None, alpha_signals=None, ic_weights=None):
        if len(kline) < 60:
            return None
        try:
            ti = TechInd(kline)
            signals = collect_signals(ti, code=code, ctx=ctx,
                                      use_reversal=self.use_reversal)
            if alpha_signals and code in alpha_signals:
                signals = signals + alpha_signals[code]
            if not signals:
                return None
            return category_capped_score(signals)
        except Exception:
            return None

    def _compute_alpha_signals(self, klines):
        """预计算 Alpha Zoo 面板和注册表（不计算信号，信号在每轮调仓时按日期计算）。"""
        try:
            from aimoon.factors.panel import build_panel
            from aimoon.factors.registry import get_default_registry

            panel = build_panel(klines)
            if panel is None:
                return None
            registry = get_default_registry()
            logger.info("Alpha Zoo 面板构建完成: %d 只股票, %d 因子",
                        len(panel["close"].columns), len(registry.list()))
            return {"panel": panel, "registry": registry}
        except Exception as e:
            logger.warning("Alpha Zoo 面板构建失败: %s", e)
            return None

    def _get_alpha_signals_for_date(self, alpha_ctx, target_date):
        """获取指定日期的 Alpha Zoo 截面信号。"""
        try:
            from aimoon.factors.scorer import compute_alpha_signals
            panel = alpha_ctx["panel"]
            registry = alpha_ctx["registry"]
            signals = compute_alpha_signals(registry, panel, target_date=target_date)
            n = sum(1 for v in signals.values() if v)
            if n > 0:
                logger.debug("Alpha Zoo @ %s: %d 只股票获得信号", target_date, n)
            return signals if signals else None
        except Exception as e:
            logger.debug("Alpha Zoo @ %s 失败: %s", target_date, e)
            return None

    def run_portfolio(self, klines, names=None, sectors=None, ctx=None):
        """Momentum-driven portfolio: rebalance when signals change, not on fixed schedule."""
        if not klines:
            return self._empty_result()
        names = names or {c: c for c in klines}
        all_dates = set()
        for df in klines.values():
            all_dates.update(df.index)
        sorted_dates = sorted(all_dates)
        if len(sorted_dates) < 60 + self.hold_days:
            return self._empty_result()

        alpha_signals = self._compute_alpha_signals(klines) if self.use_alpha else None
        equity = [100.0]
        dd_curve = [0.0]
        trades = []
        positions = {}
        benchmark_equity = [100.0]
        has_benchmark = self.benchmark_code in klines
        benchmark_kline = klines.get(self.benchmark_code) if has_benchmark else None
        prev_bench_price = None
        peak = 100.0
        sector_map = (ctx or {}).get("sector_map", {})
        recent_exits: dict[str, int] = {}
        stop_loss_count: dict[str, int] = {}
        bar_count = 0
        check_interval = 3
        max_hold_bars = 20
        sector_ctx = {"sector_map": sector_map} if sector_map else None

        for bar_date in sorted_dates[60:]:
            if self.backtest_start_date is not None and bar_date < pd.Timestamp(self.backtest_start_date):
                bar_count += 1
                continue

            effective_positions = self.max_positions
            effective_threshold = self.entry_threshold
            if benchmark_kline is not None:
                regime = _detect_regime_safe(benchmark_kline, bar_date)
                if regime is not None:
                    if regime.state == "bear":
                        effective_positions = max(1, self.max_positions // 2)
                        effective_threshold = self.entry_threshold + 3
                    elif regime.state == "high_volatility":
                        effective_positions = max(1, self.max_positions * 2 // 3)
                        effective_threshold = self.entry_threshold + 2

            # ── Phase 1: stop-loss / take-profit / max hold (every bar) ──
            to_close = []
            for code, pos in list(positions.items()):
                if code not in klines:
                    continue
                df = klines[code]
                effective_sl = pos.get("stop_loss", self.stop_loss_pct)
                if bar_date not in df.index:
                    last_price = float(df["close"].iloc[-1])
                    elapsed = (bar_date - pos["entry_date"]).days
                    to_close.append((code, last_price, "data_gap", elapsed))
                    continue
                current_price = float(df.loc[bar_date, "close"])
                pnl = (current_price - pos["entry_price"]) / pos["entry_price"]
                elapsed_days = (bar_date - pos["entry_date"]).days
                if pnl <= -effective_sl:
                    to_close.append((code, current_price, "stop_loss", elapsed_days))
                elif pnl >= self.take_profit_pct:
                    to_close.append((code, current_price, "take_profit", elapsed_days))
                elif elapsed_days >= max_hold_bars:
                    to_close.append((code, current_price, "hold_period", elapsed_days))

            closed_return = 0.0
            for code, exit_price, reason, hdays in to_close:
                pos = positions.pop(code)
                gross_ret = (exit_price - pos["entry_price"]) / pos["entry_price"]
                cost = self._buy_cost() + self._sell_cost()
                net_ret = gross_ret - cost
                trades.append(EnhancedTrade(
                    code=code, name=pos["name"],
                    entry_date=str(pos["entry_date"]),
                    exit_date=str(bar_date),
                    entry_price=pos["entry_price"],
                    exit_price=exit_price,
                    return_pct=net_ret * 100,
                    cost_pct=cost * 100,
                    exit_reason=reason,
                    hold_days=hdays,
                ))
                closed_return += net_ret * pos["weight"]
                recent_exits[code] = bar_count
                if reason == "stop_loss":
                    stop_loss_count[code] = stop_loss_count.get(code, 0) + 1

            # ── Phase 2: momentum check (every 3 bars) ──
            if bar_count % check_interval == 0 and positions:
                alpha_sigs = self._get_alpha_signals_for_date(alpha_signals, bar_date) if alpha_signals else None
                weak_codes = []
                for code, pos in list(positions.items()):
                    if code not in klines:
                        continue
                    df = klines[code]
                    if bar_date not in df.index:
                        continue
                    idx = df.index.get_loc(bar_date)
                    if idx < 60:
                        continue
                    window = df.iloc[:idx]
                    score = self._score_stock(code, pos["name"], window, ctx=sector_ctx,
                                               alpha_signals=alpha_sigs, ic_weights=self.ic_weights)
                    if score is not None and score < self.exit_threshold:
                        weak_codes.append(code)

                for code in weak_codes:
                    if code not in positions:
                        continue
                    if code not in klines or bar_date not in klines[code].index:
                        continue
                    pos = positions.pop(code)
                    exit_price = float(klines[code].loc[bar_date, "close"])
                    gross_ret = (exit_price - pos["entry_price"]) / pos["entry_price"]
                    cost = self._buy_cost() + self._sell_cost()
                    net_ret = gross_ret - cost
                    elapsed = (bar_date - pos["entry_date"]).days
                    trades.append(EnhancedTrade(
                        code=code, name=pos["name"],
                        entry_date=str(pos["entry_date"]),
                        exit_date=str(bar_date),
                        entry_price=pos["entry_price"],
                        exit_price=exit_price,
                        return_pct=net_ret * 100,
                        cost_pct=cost * 100,
                        exit_reason="momentum_exit",
                        hold_days=elapsed,
                    ))
                    closed_return += net_ret * pos["weight"]
                    recent_exits[code] = bar_count

            # ── Phase 3: mark-to-market ──
            unrealized_return = 0.0
            for code, pos in positions.items():
                df = klines.get(code)
                if df is None or bar_date not in df.index:
                    continue
                current_price = float(df.loc[bar_date, "close"])
                ret = (current_price - pos["entry_price"]) / pos["entry_price"]
                unrealized_return += ret * pos["weight"]

            period_return = closed_return + unrealized_return
            equity.append(equity[-1] * (1 + period_return))
            current_val = equity[-1]
            peak = max(peak, current_val)
            dd = (peak - current_val) / peak if peak > 0 else 0.0
            dd_curve.append(dd)

            if has_benchmark and benchmark_kline is not None and bar_date in benchmark_kline.index:
                bench_price_now = float(benchmark_kline.loc[bar_date, 'close'])
                if prev_bench_price is not None:
                    bench_ret = (bench_price_now - prev_bench_price) / prev_bench_price
                    benchmark_equity.append(benchmark_equity[-1] * (1 + bench_ret))
                prev_bench_price = bench_price_now

            # ── Phase 4: open replacements when slots available ──
            if len(positions) < effective_positions and bar_count % check_interval == 0:
                alpha_sigs = self._get_alpha_signals_for_date(alpha_signals, bar_date) if alpha_signals else None
                sector_exposure: dict[str, float] = {}
                for pos in positions.values():
                    sec = pos.get("sector", "")
                    if sec:
                        sector_exposure[sec] = sector_exposure.get(sec, 0.0) + pos["weight"]

                scored_candidates: list[tuple[str, str, int]] = []
                for code, df in klines.items():
                    if code == self.benchmark_code or code in positions:
                        continue
                    if code in recent_exits and (bar_count - recent_exits[code]) < 2:
                        continue
                    if stop_loss_count.get(code, 0) >= 1:
                        continue
                    if bar_date not in df.index:
                        continue
                    idx = df.index.get_loc(bar_date)
                    if idx < 60:
                        continue
                    window = df.iloc[:idx]
                    if len(window) < 60:
                        continue
                    score = self._score_stock(code, names.get(code, code), window, ctx=sector_ctx,
                                               alpha_signals=alpha_sigs, ic_weights=self.ic_weights)
                    if score is not None and score >= effective_threshold:
                        scored_candidates.append((code, names.get(code, code), score))

                # RPS cross-sectional ranks
                rps_bonus: dict[str, float] = {}
                if len(scored_candidates) >= 5:
                    roc_data: dict[str, dict[int, float]] = {}
                    for code, _, _ in scored_candidates:
                        if code not in klines or bar_date not in klines[code].index:
                            continue
                        df = klines[code]
                        loc = df.index.get_loc(bar_date)
                        if loc < 20:
                            continue
                        close = pd.to_numeric(df["close"].iloc[:loc + 1], errors="coerce")
                        rocs: dict[int, float] = {}
                        for period in [5, 10, 20]:
                            if len(close) > period and close.iloc[-period - 1] > 0:
                                rocs[period] = float((close.iloc[-1] - close.iloc[-period - 1]) / close.iloc[-period - 1] * 100)
                        if rocs:
                            roc_data[code] = rocs
                    for period in [5, 10, 20]:
                        pvals = {c: v[period] for c, v in roc_data.items() if period in v}
                        if len(pvals) < 5:
                            continue
                        scodes = sorted(pvals, key=lambda c: pvals[c])
                        total = len(scodes)
                        for rank, code in enumerate(scodes):
                            pct = (rank + 1) / total * 100
                            if pct >= 90:
                                rps_bonus[code] = rps_bonus.get(code, 0) + 2
                            elif pct >= 75:
                                rps_bonus[code] = rps_bonus.get(code, 0) + 1
                            elif pct <= 10:
                                rps_bonus[code] = rps_bonus.get(code, 0) - 2
                            elif pct <= 25:
                                rps_bonus[code] = rps_bonus.get(code, 0) - 1

                scores = {c: s + rps_bonus.get(c, 0) for c, _, s in scored_candidates}
                if scores:
                    weights = self._compute_position_weights(trades, effective_positions, klines, scores)
                    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                    slots = effective_positions - len(positions)
                    for code, score in ranked:
                        if slots <= 0:
                            break
                        sector = sector_map.get(code, "")
                        weight = weights.get(code, 1.0 / effective_positions)
                        if sector:
                            cur_sec = sector_exposure.get(sector, 0.0)
                            if cur_sec + weight > self.max_sector_pct:
                                continue
                            sector_exposure[sector] = cur_sec + weight
                        df = klines[code]
                        entry_loc = df.index.get_loc(bar_date)
                        entry_price = float(df.loc[bar_date, "close"])
                        entry_window = df.iloc[:entry_loc + 1]
                        dynamic_sl = _compute_dynamic_stop_loss(entry_window, self.stop_loss_pct)
                        positions[code] = {
                            "name": names.get(code, code),
                            "entry_price": entry_price,
                            "entry_date": bar_date,
                            "weight": weight,
                            "sector": sector,
                            "stop_loss": dynamic_sl,
                        }
                        slots -= 1

            bar_count += 1

        return self._compute_metrics(trades, equity, dd_curve, benchmark_equity)

    def _compute_metrics(self, trades, equity, dd_curve, benchmark_equity):
        if not trades:
            return self._empty_result()
        total_ret = (equity[-1] / equity[0] - 1) * 100
        n_periods = len(equity) - 1
        total_days = n_periods * self.rebalance_freq
        annual_ret = ((equity[-1] / equity[0]) ** (252 / max(total_days, 1)) - 1) * 100
        returns = [(equity[i] / equity[i - 1] - 1) for i in range(1, len(equity))]
        if returns:
            mean_ret = np.mean(returns) * 252 / self.rebalance_freq
            std_ret = np.std(returns) * np.sqrt(252 / self.rebalance_freq)
            sharpe = mean_ret / std_ret if std_ret > 0 else 0.0
            downside = [r for r in returns if r < 0]
            downside_std = np.std(downside) * np.sqrt(252 / self.rebalance_freq) if downside else 0.0
            sortino = mean_ret / downside_std if downside_std > 0 else 0.0
        else:
            sharpe = sortino = 0.0
        max_dd = max(dd_curve) if dd_curve else 0.0
        win_rate = sum(1 for t in trades if t.return_pct > 0) / len(trades)
        wins = [t.return_pct for t in trades if t.return_pct > 0]
        losses = [t.return_pct for t in trades if t.return_pct <= 0]
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0.0)
        avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0.0
        bench_ret = (benchmark_equity[-1] / benchmark_equity[0] - 1) * 100 if len(benchmark_equity) > 1 else 0.0
        calmar = annual_ret / (max_dd * 100) if max_dd > 0 else 0.0

        # 盈亏比（Vibe-Trading 移植）
        avg_w = float(avg_win)
        avg_l = abs(float(avg_loss))
        profit_loss_ratio = round(avg_w / avg_l, 4) if avg_l > 1e-10 else 0.0

        # 最大连续亏损次数（Vibe-Trading 移植）
        max_consec = 0
        cur_consec = 0
        for t in trades:
            if t.return_pct < 0:
                cur_consec += 1
                max_consec = max(max_consec, cur_consec)
            else:
                cur_consec = 0

        # 信息比率（Vibe-Trading 移植）
        information_ratio = 0.0
        if len(benchmark_equity) > 1 and len(returns) > 1:
            bench_returns = [(benchmark_equity[i] / benchmark_equity[i - 1] - 1)
                             for i in range(1, len(benchmark_equity))]
            n = min(len(returns), len(bench_returns))
            if n > 1:
                active = np.array(returns[-n:]) - np.array(bench_returns[-n:])
                active_std = float(np.std(active))
                information_ratio = round(
                    float(np.mean(active) / (active_std + 1e-10) * np.sqrt(252 / self.rebalance_freq)), 4
                )

        return EnhancedPortfolioResult(
            total_return=round(total_ret, 2),
            annual_return=round(annual_ret, 2),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            max_drawdown=round(max_dd * 100, 2),
            win_rate=round(win_rate, 4),
            trade_count=len(trades),
            avg_hold_days=round(avg_hold, 1),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            benchmark_return=round(bench_ret, 2),
            excess_return=round(total_ret - bench_ret, 2),
            calmar_ratio=round(calmar, 2),
            trades=tuple(trades),
            equity_curve=tuple(equity),
            drawdown_curve=tuple(dd_curve),
            profit_loss_ratio=profit_loss_ratio,
            max_consecutive_loss=max_consec,
            information_ratio=information_ratio,
        )

    def _compute_position_weights(
        self, trades: list, max_positions: int,
        klines: dict, scores: dict[str, float],
    ) -> dict[str, float]:
        """Compute position weights using Kelly criterion + volatility targeting.

        Falls back to equal weighting when insufficient trade history.
        """
        equal_weight = 1.0 / max_positions
        if not self.use_kelly or len(trades) < 10:
            return {code: equal_weight for code in scores}

        win_rate = sum(1 for t in trades if t.return_pct > 0) / len(trades)
        wins = [t.return_pct for t in trades if t.return_pct > 0]
        losses = [abs(t.return_pct) for t in trades if t.return_pct < 0]
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 1.0

        kelly = kelly_criterion(win_rate, avg_win, avg_loss)
        if kelly <= 0:
            return {code: equal_weight for code in scores}

        weights: dict[str, float] = {}
        for code in scores:
            df = klines.get(code)
            if df is not None and len(df) >= 20:
                vol = float(df["close"].pct_change().iloc[-20:].std() * np.sqrt(252))
                w = volatility_position_size(kelly, vol, max_pct=0.25)
            else:
                w = kelly * 0.5
            weights[code] = max(w, 0.02)

        # Normalize so weights sum to 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {c: v / total for c, v in weights.items()}
        return weights


def _compute_dynamic_stop_loss(kline: pd.DataFrame, fallback: float = 0.05) -> float:
    """Compute ATR-based dynamic stop-loss: 1.5x ATR_pct, clamped to [3%, 5%]."""
    try:
        if len(kline) < 20:
            return fallback
        ti = TechInd(kline)
        atr_pct = ti.atr_pct(14)
        if atr_pct <= 0:
            return fallback
        return max(0.03, min(0.05, atr_pct * 1.5 / 100))
    except Exception:
        return fallback
