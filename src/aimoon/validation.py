"""回测统计验证 — Monte Carlo、Bootstrap CI、Walk-Forward。

移植自 HKUDS/Vibe-Trading (MIT) backtest/validation.py，
适配 aimoon 的 TradeRecord（return_pct 字段，字符串日期）。

三种独立验证工具：
- Monte Carlo 排列检验：策略是否显著优于随机？
- Bootstrap Sharpe CI：风险调整收益有多稳定？
- Walk-Forward 分析：收益在时间窗口间是否一致？
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Monte Carlo 排列检验 ──


def monte_carlo_test(
    returns: list[float],
    n_simulations: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """打乱交易收益顺序，检验路径显著性。

    零假设：观察到的 Sharpe / 最大回撤不优于相同交易的随机排列。

    Parameters
    ----------
    returns : list[float]
        每笔交易的收益率（百分比），来自 TradeRecord.return_pct。
    n_simulations : int
        随机排列次数。
    seed : int
        随机种子。

    Returns
    -------
    dict
        actual_sharpe, p_value_sharpe, actual_max_dd, p_value_max_dd,
        simulated_sharpe 均值/标准差/百分位。
    """
    if len(returns) < 3:
        return {"error": "需要至少 3 笔交易", "p_value_sharpe": 1.0}

    rets = np.array(returns) / 100.0  # 百分比转小数
    # 过滤掉零收益
    rets = rets[rets != 0]
    if len(rets) < 3:
        return {"error": "有效交易不足 3 笔", "p_value_sharpe": 1.0}

    actual = _path_metrics(rets)

    rng = np.random.default_rng(seed)
    sharpe_count = 0
    dd_count = 0
    sim_sharpes: list[float] = []

    for _ in range(n_simulations):
        shuffled = rng.permutation(rets)
        sim = _path_metrics(shuffled)
        sim_sharpes.append(sim["sharpe"])
        if sim["sharpe"] >= actual["sharpe"]:
            sharpe_count += 1
        if sim["max_dd"] >= actual["max_dd"]:
            dd_count += 1

    sim_arr = np.array(sim_sharpes)
    return {
        "actual_sharpe": round(actual["sharpe"], 4),
        "actual_max_dd": round(actual["max_dd"], 4),
        "p_value_sharpe": round(sharpe_count / n_simulations, 4),
        "p_value_max_dd": round(dd_count / n_simulations, 4),
        "simulated_sharpe_mean": round(float(sim_arr.mean()), 4),
        "simulated_sharpe_std": round(float(sim_arr.std()), 4),
        "simulated_sharpe_p5": round(float(np.percentile(sim_arr, 5)), 4),
        "simulated_sharpe_p95": round(float(np.percentile(sim_arr, 95)), 4),
        "n_simulations": n_simulations,
        "n_trades": len(returns),
    }


def _path_metrics(returns: np.ndarray) -> dict[str, float]:
    """从收益序列计算 Sharpe 和最大回撤。"""
    equity = 100.0 * np.cumprod(1 + returns)
    equity = np.insert(equity, 0, 100.0)
    rets = np.diff(equity) / equity[:-1]
    std = rets.std()
    sharpe = float(rets.mean() / (std + 1e-10) * np.sqrt(252))
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak > 0, peak, 1.0)
    max_dd = float(dd.min())
    return {"sharpe": sharpe, "max_dd": max_dd}


# ── Bootstrap Sharpe 置信区间 ──


def bootstrap_sharpe_ci(
    equity_curve: pd.Series | tuple[float, ...],
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    bars_per_year: int = 252,
    seed: int = 42,
) -> dict[str, Any]:
    """重采样日收益率，估计 Sharpe 置信区间。

    Parameters
    ----------
    equity_curve : pd.Series 或 tuple[float, ...]
        权益时间序列。
    n_bootstrap : int
        Bootstrap 采样次数。
    confidence : float
        置信水平（如 0.95 表示 95% CI）。
    bars_per_year : int
        年化因子。
    seed : int
        随机种子。

    Returns
    -------
    dict
        observed_sharpe, ci_lower, ci_upper, median_sharpe, prob_positive。
    """
    if isinstance(equity_curve, tuple):
        eq = pd.Series(equity_curve)
    else:
        eq = equity_curve

    returns = eq.pct_change().dropna().values
    if len(returns) < 5:
        return {"error": "需要至少 5 个收益观测值"}

    observed = _sharpe(returns, bars_per_year)

    rng = np.random.default_rng(seed)
    boot_sharpes: list[float] = []
    for _ in range(n_bootstrap):
        sample = rng.choice(returns, size=len(returns), replace=True)
        boot_sharpes.append(_sharpe(sample, bars_per_year))

    arr = np.array(boot_sharpes)
    alpha = (1 - confidence) / 2
    lower = float(np.percentile(arr, alpha * 100))
    upper = float(np.percentile(arr, (1 - alpha) * 100))
    prob_pos = float(np.mean(arr > 0))

    return {
        "observed_sharpe": round(observed, 4),
        "ci_lower": round(lower, 4),
        "ci_upper": round(upper, 4),
        "median_sharpe": round(float(np.median(arr)), 4),
        "prob_positive": round(prob_pos, 4),
        "confidence": confidence,
        "n_bootstrap": n_bootstrap,
    }


def _sharpe(returns: np.ndarray, bars_per_year: int = 252) -> float:
    std = returns.std()
    return float(returns.mean() / (std + 1e-10) * np.sqrt(bars_per_year))


# ── Walk-Forward 分析 ──


def walk_forward_analysis(
    equity_curve: pd.Series | tuple[float, ...],
    entry_dates: list[str],
    exit_dates: list[str],
    returns: list[float],
    n_windows: int = 5,
    bars_per_year: int = 252,
) -> dict[str, Any]:
    """将回测分成连续窗口，检查收益一致性。

    Parameters
    ----------
    equity_curve : pd.Series 或 tuple[float, ...]
        权益时间序列。
    entry_dates : list[str]
        每笔交易的入场日期。
    exit_dates : list[str]
        每笔交易的出场日期。
    returns : list[float]
        每笔交易的收益率（百分比）。
    n_windows : int
        窗口数量。
    bars_per_year : int
        年化因子。

    Returns
    -------
    dict
        每个窗口的统计量、一致性指标。
    """
    if isinstance(equity_curve, tuple):
        eq = pd.Series(equity_curve)
    else:
        eq = equity_curve

    if len(eq) < n_windows * 2:
        return {"error": f"需要至少 {n_windows * 2} 个数据点"}

    window_size = len(eq) // n_windows
    windows: list[dict[str, Any]] = []

    for i in range(n_windows):
        start_idx = i * window_size
        end_idx = (i + 1) * window_size if i < n_windows - 1 else len(eq)
        win_eq = eq.iloc[start_idx:end_idx]

        # 窗口收益
        ret = float(win_eq.iloc[-1] / win_eq.iloc[0] - 1) if win_eq.iloc[0] > 0 else 0.0
        win_returns = win_eq.pct_change().dropna().values
        sharpe = _sharpe(win_returns, bars_per_year) if len(win_returns) > 1 else 0.0

        # 窗口最大回撤
        peak = win_eq.cummax()
        dd = (win_eq - peak) / peak.replace(0, 1)
        max_dd = float(dd.min())

        # 窗口内交易
        if hasattr(eq, 'index') and hasattr(eq.index[0], 'date'):
            win_start = eq.index[start_idx]
            win_end = eq.index[end_idx - 1]
            win_trades = sum(
                1 for ed, xd in zip(entry_dates, exit_dates)
                if str(win_start.date()) <= ed <= str(win_end.date())
            )
            win_rets = [
                r for ed, r in zip(entry_dates, returns)
                if str(win_start.date()) <= ed <= str(win_end.date())
            ]
        else:
            win_trades = 0
            win_rets = []

        win_rate = (
            sum(1 for r in win_rets if r > 0) / len(win_rets) if win_rets else 0.0
        )

        windows.append({
            "window": i + 1,
            "return": round(ret, 6),
            "sharpe": round(sharpe, 4),
            "max_dd": round(max_dd, 6),
            "trades": win_trades,
            "win_rate": round(win_rate, 4),
        })

    returns_list = [w["return"] for w in windows]
    sharpes_list = [w["sharpe"] for w in windows]
    profitable = sum(1 for r in returns_list if r > 0)

    return {
        "n_windows": n_windows,
        "windows": windows,
        "profitable_windows": profitable,
        "consistency_rate": round(profitable / n_windows, 4),
        "return_mean": round(float(np.mean(returns_list)), 6),
        "return_std": round(float(np.std(returns_list)), 6),
        "sharpe_mean": round(float(np.mean(sharpes_list)), 4),
        "sharpe_std": round(float(np.std(sharpes_list)), 4),
    }


# ── 统一入口 ──


def run_validation(
    equity_curve: pd.Series | tuple[float, ...],
    trade_returns: list[float],
    entry_dates: list[str] | None = None,
    exit_dates: list[str] | None = None,
    n_simulations: int = 1000,
    n_bootstrap: int = 1000,
    n_windows: int = 5,
    bars_per_year: int = 252,
    seed: int = 42,
) -> dict[str, Any]:
    """运行所有三项验证检查。

    Parameters
    ----------
    equity_curve : pd.Series 或 tuple[float, ...]
        权益时间序列。
    trade_returns : list[float]
        每笔交易的收益率（百分比）。
    entry_dates, exit_dates : list[str] | None
        交易日期列表（Walk-Forward 需要）。
    n_simulations : int
        Monte Carlo 模拟次数。
    n_bootstrap : int
        Bootstrap 采样次数。
    n_windows : int
        Walk-Forward 窗口数。
    bars_per_year : int
        年化因子。
    seed : int
        随机种子。

    Returns
    -------
    dict
        包含 monte_carlo、bootstrap、walk_forward 三个子结果。
    """
    results: dict[str, Any] = {}

    # Monte Carlo
    results["monte_carlo"] = monte_carlo_test(
        trade_returns, n_simulations=n_simulations, seed=seed,
    )

    # Bootstrap Sharpe CI
    results["bootstrap"] = bootstrap_sharpe_ci(
        equity_curve, n_bootstrap=n_bootstrap, bars_per_year=bars_per_year, seed=seed,
    )

    # Walk-Forward（需要日期信息）
    if entry_dates and exit_dates and len(entry_dates) == len(trade_returns):
        results["walk_forward"] = walk_forward_analysis(
            equity_curve, entry_dates, exit_dates, trade_returns,
            n_windows=n_windows, bars_per_year=bars_per_year,
        )
    else:
        results["walk_forward"] = {"skipped": "需要交易日期信息"}

    return results
