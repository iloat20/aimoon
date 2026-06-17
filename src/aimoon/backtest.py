"""精简版 ML-分数驱动回测引擎。

两阶段设计：
1. 预计算阶段 — 对整个回测区间 panel 一次性计算 11 因子 + ML 预测
2. 回测阶段 — 纯 Python 遍历预计算分数，无任何因子/ML 计算
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from aimoon.factors.ashare import build_panel, compute_ashare_factors
from aimoon.ml.feature_pipeline import extract_features

logger = logging.getLogger(__name__)

# ── 回测参数（沿用已调优值） ──────────────────────────────────────────

ENTRY_THRESHOLD = 60
STOP_LOSS_PCT = 0.04
TAKE_PROFIT_PCT = 0.14
TRAILING_BREAKEVEN = 0.03  # +3% 触发保本
TRAILING_LOCK = 0.06  # +6% 锁利
HOLD_DAYS = 12
MAX_POSITIONS = 4
BENCHMARK_CODE = "000300"


@dataclass
class BacktestPosition:
    """回测持仓（可变，回测过程中更新）。"""

    code: str
    name: str
    entry_price: float
    entry_date: pd.Timestamp
    quantity: float
    peak_price: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    breakeven_triggered: bool = False
    lock_triggered: bool = False


@dataclass
class BacktestTrade:
    """已平仓交易记录（不可变）。"""

    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    exit_reason: str
    hold_days: int


@dataclass
class BacktestResult:
    """回测结果。"""

    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    trade_count: int = 0
    avg_hold_days: float = 0.0
    benchmark_return: float = 0.0
    excess_return: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    trades: list[BacktestTrade] = field(default_factory=list)


def precompute_scores(
    klines: dict[str, pd.DataFrame],
    predictor: Any = None,
    sector_map: dict[str, str] | None = None,
    fundamentals: dict[str, pd.DataFrame] | None = None,
) -> dict[pd.Timestamp, dict[str, int]]:
    """预计算每日 ML 分数 — 逐日期提取特征 + 模型推理。

    Args:
        klines: {code: kline_df}
        predictor: MLPredictor 实例（含 model/feature_names/feature_medians）
        sector_map: {code: sector_name}
        fundamentals: {pe|pb|dividend: DataFrame(日期×股票)}

    Returns:
        dict[date, dict[code, 0-100 score]]
    """
    panel = build_panel(klines, min_rows=120)
    if panel is None:
        logger.error("precompute_scores: cannot build panel")
        return {}

    close = panel.get("close")
    if close is None or close.empty:
        return {}

    dates = close.index.tolist()
    scores_by_date: dict[pd.Timestamp, dict[str, int]] = {}

    for date in dates:
        if predictor is not None:
            # 逐日期提取特征
            features = extract_features(
                panel,
                target_date=date,
                sector_map=sector_map,
                fundamentals=fundamentals,
                feature_medians=(
                    pd.Series(predictor.feature_medians)
                    if predictor.feature_medians
                    else None
                ),
            )
            if features.empty:
                continue

            # 直接用底层模型预测（避免 predictor.predict 只能预测最新日期）
            X = features.reindex(columns=predictor.feature_names, fill_value=0.0).astype(float)
            try:
                raw = predictor.model.predict(X.values)
            except Exception as e:
                logger.warning("precompute_scores predict failed at %s: %s", date, e)
                continue

            pred_series = pd.Series(raw, index=X.index).dropna()
            if pred_series.empty:
                continue

            # 百分位排名
            ranked = pred_series.rank(pct=True)
            scores_by_date[date] = {
                str(code): int(round(ranked[code] * 100))
                for code in ranked.index
            }
        else:
            scores_by_date[date] = _fallback_scores(panel, date)

    return scores_by_date


def _fallback_scores(
    panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> dict[str, int]:
    """无 ML 模型时的回退：用前 5 日收益排名作为分数。"""
    close = panel.get("close")
    if close is None or date not in close.index:
        return {}
    ret_5d = close.pct_change(5).loc[date]
    if ret_5d.empty:
        return {}
    # Normalize to 0-100
    ranked = ret_5d.rank(pct=True)
    return {code: int(pct * 100) for code, pct in ranked.items() if pd.notna(pct)}


def run_backtest(
    klines: dict[str, pd.DataFrame],
    names: dict[str, str],
    scores_by_date: dict[pd.Timestamp, dict[str, int]],
    benchmark_code: str = BENCHMARK_CODE,
    entry_threshold: int = ENTRY_THRESHOLD,
    stop_loss_pct: float = STOP_LOSS_PCT,
    take_profit_pct: float = TAKE_PROFIT_PCT,
    trailing_breakeven: float = TRAILING_BREAKEVEN,
    trailing_lock: float = TRAILING_LOCK,
    hold_days: int = HOLD_DAYS,
    max_positions: int = MAX_POSITIONS,
) -> BacktestResult:
    """运行 ML 分数驱动回测。

    入场：每日检查分数≥threshold，等权建仓
    出场：止损/止盈/跟踪止损/最大持有天数
    """
    if not scores_by_date:
        logger.error("回测：无分数数据")
        return BacktestResult()

    dates = sorted(scores_by_date.keys())
    if len(dates) < 10:
        logger.error("回测：交易日不足 10")
        return BacktestResult()

    initial_cash = 1_000_000.0
    cash = initial_cash
    positions: dict[str, BacktestPosition] = {}
    trades: list[BacktestTrade] = []
    equity_curve: list[float] = []

    # 等权每仓金额
    position_value = initial_cash / max_positions

    # 提取每日 OHLC 数据
    _build_ohlc_lookup(klines, dates)

    for date in dates:
        day_scores = scores_by_date.get(date, {})
        ohlc = _get_day_ohlc(klines, date)

        # ---- 退出检查 ----
        for code in list(positions.keys()):
            pos = positions[code]
            o = ohlc.get(code, {})
            day_low = o.get("low", pos.entry_price)
            day_high = o.get("high", pos.entry_price)
            day_close = o.get("close", pos.entry_price)

            # 更新峰值
            pos.peak_price = max(pos.peak_price, day_close)

            # 跟踪止损
            profit_pct = (pos.peak_price - pos.entry_price) / pos.entry_price
            drawdown_pct = (pos.peak_price - day_close) / pos.entry_price

            # 止盈
            if day_high >= pos.entry_price * (1 + take_profit_pct):
                exit_price = pos.entry_price * (1 + take_profit_pct)
                trades.append(_close_position(pos, date, exit_price, "止盈"))
                cash += exit_price * pos.quantity
                del positions[code]
                continue

            # 止损
            if day_low <= pos.entry_price * (1 - stop_loss_pct):
                exit_price = pos.entry_price * (1 - stop_loss_pct)
                trades.append(_close_position(pos, date, exit_price, "止损"))
                cash += exit_price * pos.quantity
                del positions[code]
                continue

            # 保本触发（峰值利润 ≥3%）
            if profit_pct >= trailing_breakeven:
                if not pos.breakeven_triggered:
                    pos.breakeven_triggered = True
                    pos.stop_loss_price = pos.entry_price * 1.01  # +1% 保本

            if pos.breakeven_triggered and day_low <= pos.stop_loss_price:
                exit_price = pos.stop_loss_price
                trades.append(_close_position(pos, date, exit_price, "跟踪止损"))
                cash += exit_price * pos.quantity
                del positions[code]
                continue

            # 锁利触发（峰值利润 ≥6%）
            if profit_pct >= trailing_lock:
                if not pos.lock_triggered:
                    pos.lock_triggered = True
                    # 从峰值回落超过峰值利润的 40% 时锁利
                    lock_level = pos.peak_price * (1 - profit_pct * 0.4)
                    pos.stop_loss_price = max(pos.stop_loss_price, lock_level)

            if pos.lock_triggered and drawdown_pct >= profit_pct * 0.4:
                exit_price = pos.stop_loss_price
                trades.append(_close_position(pos, date, exit_price, "锁利卖出"))
                cash += exit_price * pos.quantity
                del positions[code]
                continue

            # 最大持有天数
            hold = (date - pos.entry_date).days
            if hold >= hold_days:
                exit_price = day_close
                trades.append(_close_position(pos, date, exit_price, "最大持有"))
                cash += exit_price * pos.quantity
                del positions[code]
                continue

        # ---- 入场检查 ----
        if len(positions) < max_positions:
            # 按分数降序排列（跳过已持仓）
            candidates = [
                (code, score)
                for code, score in sorted(day_scores.items(), key=lambda x: -x[1])
                if code not in positions
            ]

            for code, score in candidates:
                if len(positions) >= max_positions:
                    break
                if score < entry_threshold:
                    continue
                if code not in ohlc:
                    continue

                entry_price = ohlc[code].get("close", 0.0)
                if entry_price <= 0:
                    continue

                quantity = position_value / entry_price
                positions[code] = BacktestPosition(
                    code=code,
                    name=names.get(code, code),
                    entry_price=entry_price,
                    entry_date=date,
                    quantity=quantity,
                    peak_price=entry_price,
                )
                cash -= position_value

        # ---- 记录净值 ----
        portfolio_value = cash + sum(
            _get_day_ohlc(klines, date).get(p.code, {}).get("close", p.entry_price) * p.quantity
            for p in positions.values()
        )
        equity_curve.append(portfolio_value)

    # ---- 计算指标 ----
    return _compute_metrics(trades, equity_curve, initial_cash, klines, benchmark_code)


# ── 辅助函数 ────────────────────────────────────────────────────────

_ohlc_cache: dict[str, pd.DataFrame] = {}
_date_ohlc_cache: dict[pd.Timestamp, dict[str, dict[str, float]]] = {}


def _build_ohlc_lookup(
    klines: dict[str, pd.DataFrame],
    dates: list[pd.Timestamp],
) -> None:
    """构建 OHLC 查询缓存。"""
    _ohlc_cache.clear()
    _date_ohlc_cache.clear()

    for code, df in klines.items():
        if df is None or df.empty:
            continue
        ohlc_cols = {}
        for col in ("open", "high", "low", "close"):
            if col in df.columns:
                ohlc_cols[col] = df[col].astype(float)
        if ohlc_cols:
            _ohlc_cache[code] = pd.DataFrame(ohlc_cols, index=df.index)

    # 预缓存所有已知日期
    for date in dates:
        day_data: dict[str, dict[str, float]] = {}
        for code, ohlc_df in _ohlc_cache.items():
            if date in ohlc_df.index:
                row = ohlc_df.loc[date]
                day_data[code] = {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
        _date_ohlc_cache[date] = day_data


def _get_day_ohlc(
    klines: dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> dict[str, dict[str, float]]:
    """获取某日所有股票的 OHLC 数据。"""
    return _date_ohlc_cache.get(date, {})


def _close_position(
    pos: BacktestPosition,
    exit_date: pd.Timestamp,
    exit_price: float,
    reason: str,
) -> BacktestTrade:
    """创建已平仓交易记录。"""
    ret = (exit_price - pos.entry_price) / pos.entry_price * 100.0
    hold = (exit_date - pos.entry_date).days
    return BacktestTrade(
        code=pos.code,
        name=pos.name,
        entry_date=str(pos.entry_date.date()),
        exit_date=str(exit_date.date()),
        entry_price=pos.entry_price,
        exit_price=exit_price,
        return_pct=round(ret, 2),
        exit_reason=reason,
        hold_days=hold,
    )


def _compute_metrics(
    trades: list[BacktestTrade],
    equity_curve: list[float],
    initial_cash: float,
    klines: dict[str, pd.DataFrame],
    benchmark_code: str,
) -> BacktestResult:
    """计算回测指标。"""
    if not trades:
        return BacktestResult()

    # 总收益
    total_return = (
        (equity_curve[-1] - initial_cash) / initial_cash
        if len(equity_curve) > 1 else 0.0
    )

    # 年化收益（按 252 交易日）
    n_days = len(equity_curve)
    annual_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1 if n_days > 0 else 0.0

    # 日收益率序列
    if len(equity_curve) > 1:
        daily_returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
    else:
        daily_returns = np.array([0.0])

    # Sharpe
    rf = 0.02 / 252  # 日无风险利率
    excess_daily = daily_returns - rf
    sharpe = float(
        np.mean(excess_daily) / np.std(excess_daily) * np.sqrt(252)
    ) if np.std(excess_daily) > 1e-10 else 0.0

    # Sortino（只考虑下行波动）
    downside = daily_returns[daily_returns < 0]
    sortino = (
        float(np.mean(excess_daily) / np.std(downside) * np.sqrt(252))
        if len(downside) > 0 and np.std(downside) > 1e-10
        else 0.0
    )

    # 最大回撤
    peak = np.maximum.accumulate(equity_curve)
    drawdowns = (np.array(equity_curve) - peak) / peak
    max_drawdown = float(abs(np.min(drawdowns)))

    # 胜率 / 盈亏比
    wins = [t for t in trades if t.return_pct > 0]
    losses = [t for t in trades if t.return_pct <= 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    avg_win = np.mean([t.return_pct for t in wins]) if wins else 0.0
    avg_loss = abs(np.mean([t.return_pct for t in losses])) if losses else 0.0
    profit_factor = (
        sum(t.return_pct for t in wins) / abs(sum(t.return_pct for t in losses))
        if losses and sum(t.return_pct for t in losses) != 0
        else 0.0
    )

    avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0.0

    # 基准收益
    benchmark_return = 0.0
    if benchmark_code in klines:
        bm = klines[benchmark_code]
        if bm is not None and len(bm) > 1:
            bm_start = float(bm["close"].iloc[0])
            bm_end = float(bm["close"].iloc[-1])
            benchmark_return = (bm_end - bm_start) / bm_start if bm_start > 0 else 0.0

    excess_return = total_return - benchmark_return

    return BacktestResult(
        total_return=total_return,
        annual_return=annual_return,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        trade_count=len(trades),
        avg_hold_days=avg_hold,
        benchmark_return=benchmark_return,
        excess_return=excess_return,
        equity_curve=equity_curve,
        trades=trades,
    )
