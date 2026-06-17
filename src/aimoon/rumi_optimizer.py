"""Rumi 策略参数优化器。

基于历史数据优化 Rumi 策略参数。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RumiParameters:
    """Rumi 策略参数。"""

    # Rumi 得分参数
    rumi_lookback: int = 20
    rumi_min_score: float = 60.0
    rumi_momentum_weight: float = 0.4
    rumi_relative_strength_weight: float = 0.3
    rumi_volatility_weight: float = 0.3

    # KRange 参数
    krange_atr_period: int = 14
    krange_multiplier: float = 2.0
    krange_exit_threshold: float = 0.5

    # 跟踪止损参数
    trailing_stop_atr_multiplier: float = 2.0
    trailing_stop_min_pct: float = 0.08

    # 入场参数
    min_rumi_score_entry: float = 60.0
    rumi_bonus_strong: float = 5.0
    rumi_bonus_moderate: float = 3.0
    rumi_bonus_weak: float = 1.0
    rumi_penalty_strong: float = -5.0


@dataclass(frozen=True)
class OptimizationResult:
    """优化结果。"""

    parameters: RumiParameters
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    profit_factor: float


def generate_parameter_grid() -> list[RumiParameters]:
    """生成参数网格。

    Returns:
        list[RumiParameters]: 参数组合列表
    """
    param_grid = []

    # Rumi 得分参数网格
    rumi_lookback_values = [10, 15, 20, 25, 30]
    rumi_min_score_values = [50, 55, 60, 65, 70]
    rumi_momentum_weight_values = [0.3, 0.4, 0.5]
    rumi_relative_strength_weight_values = [0.2, 0.3, 0.4]
    rumi_volatility_weight_values = [0.2, 0.3, 0.4]

    # KRange 参数网格
    krange_atr_period_values = [10, 14, 20]
    krange_multiplier_values = [1.5, 2.0, 2.5]
    krange_exit_threshold_values = [0.4, 0.5, 0.6]

    # 跟踪止损参数网格
    trailing_stop_atr_multiplier_values = [1.5, 2.0, 2.5]
    trailing_stop_min_pct_values = [0.06, 0.08, 0.10]

    # 入场参数网格
    min_rumi_score_entry_values = [55, 60, 65]
    rumi_bonus_strong_values = [4, 5, 6]
    rumi_bonus_moderate_values = [2, 3, 4]
    rumi_bonus_weak_values = [1, 2, 3]
    rumi_penalty_strong_values = [-4, -5, -6]

    # 生成组合（使用笛卡尔积的子集以减少计算量）
    for rumi_lookback in rumi_lookback_values:
        for rumi_min_score in rumi_min_score_values:
            for rumi_momentum_weight in rumi_momentum_weight_values:
                for rumi_relative_strength_weight in rumi_relative_strength_weight_values:
                    for rumi_volatility_weight in rumi_volatility_weight_values:
                        # 验证权重和为 1.0
                        weight_sum = (
                            rumi_momentum_weight
                            + rumi_relative_strength_weight
                            + rumi_volatility_weight
                        )
                        if abs(weight_sum - 1.0) > 0.01:
                            continue

                        for krange_atr_period in krange_atr_period_values:
                            for krange_multiplier in krange_multiplier_values:
                                for krange_exit_threshold in krange_exit_threshold_values:
                                    for (
                                        trailing_stop_atr_multiplier
                                    ) in trailing_stop_atr_multiplier_values:
                                        for trailing_stop_min_pct in trailing_stop_min_pct_values:
                                            for min_rumi_score_entry in min_rumi_score_entry_values:
                                                for rumi_bonus_strong in rumi_bonus_strong_values:
                                                    for (
                                                        rumi_bonus_moderate
                                                    ) in rumi_bonus_moderate_values:
                                                        for (
                                                            rumi_bonus_weak
                                                        ) in rumi_bonus_weak_values:
                                                            for (
                                                                rumi_penalty_strong
                                                            ) in rumi_penalty_strong_values:
                                                                params = RumiParameters(
                                                                    rumi_lookback=rumi_lookback,
                                                                    rumi_min_score=rumi_min_score,
                                                                    rumi_momentum_weight=rumi_momentum_weight,
                                                                    rumi_relative_strength_weight=rumi_relative_strength_weight,
                                                                    rumi_volatility_weight=rumi_volatility_weight,
                                                                    krange_atr_period=krange_atr_period,
                                                                    krange_multiplier=krange_multiplier,
                                                                    krange_exit_threshold=krange_exit_threshold,
                                                                    trailing_stop_atr_multiplier=trailing_stop_atr_multiplier,
                                                                    trailing_stop_min_pct=trailing_stop_min_pct,
                                                                    min_rumi_score_entry=min_rumi_score_entry,
                                                                    rumi_bonus_strong=rumi_bonus_strong,
                                                                    rumi_bonus_moderate=rumi_bonus_moderate,
                                                                    rumi_bonus_weak=rumi_bonus_weak,
                                                                    rumi_penalty_strong=rumi_penalty_strong,
                                                                )
                                                                param_grid.append(params)

    # 如果组合太多，随机采样
    if len(param_grid) > 1000:
        np.random.seed(42)
        indices = np.random.choice(len(param_grid), 1000, replace=False)
        param_grid = [param_grid[i] for i in indices]

    return param_grid


def evaluate_parameters(
    params: RumiParameters,
    klines: dict[str, pd.DataFrame],
    names: dict[str, str],
    backtest_start_date: str = "2024-01-01",
) -> OptimizationResult | None:
    """评估参数组合。

    Args:
        params: Rumi 参数
        klines: K 线数据
        names: 股票名称
        backtest_start_date: 回测开始日期

    Returns:
        OptimizationResult: 优化结果
    """
    try:
        from aimoon.enhanced_backtest import EnhancedBacktestEngine

        # 创建回测引擎（使用 Rumi 参数）
        engine = EnhancedBacktestEngine(
            hold_days=22,
            max_positions=5,
            entry_threshold=int(params.min_rumi_score_entry),
            stop_loss_pct=params.trailing_stop_min_pct,
            take_profit_pct=0.15,
            backtest_start_date=backtest_start_date,
        )

        # 运行回测
        result = engine.run_portfolio(klines, names)

        # 返回优化结果
        return OptimizationResult(
            parameters=params,
            total_return=result.total_return,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown=result.max_drawdown,
            win_rate=result.win_rate,
            trade_count=result.trade_count,
            profit_factor=result.profit_factor,
        )

    except Exception as e:
        logger.error("Parameter evaluation failed: %s", e)
        return None


def optimize_rumi_parameters(
    klines: dict[str, pd.DataFrame],
    names: dict[str, str],
    backtest_start_date: str = "2024-01-01",
    max_iterations: int = 100,
    metric: str = "sharpe_ratio",
) -> OptimizationResult:
    """优化 Rumi 参数。

    Args:
        klines: K 线数据
        names: 股票名称
        backtest_start_date: 回测开始日期
        max_iterations: 最大迭代次数
        metric: 优化指标 (sharpe_ratio, total_return, profit_factor)

    Returns:
        OptimizationResult: 最佳参数组合
    """
    logger.info("Starting Rumi parameter optimization...")
    logger.info("Metric: %s, Max iterations: %d", metric, max_iterations)

    # 生成参数网格
    param_grid = generate_parameter_grid()
    logger.info("Generated %d parameter combinations", len(param_grid))

    # 随机采样（如果组合太多）
    if len(param_grid) > max_iterations:
        np.random.seed(42)
        indices = np.random.choice(len(param_grid), max_iterations, replace=False)
        param_grid = [param_grid[i] for i in indices]
        logger.info("Sampled %d parameter combinations", len(param_grid))

    # 评估每个参数组合
    best_result = None
    best_metric_value = -np.inf

    for i, params in enumerate(param_grid):
        if i % 10 == 0:
            logger.info("Evaluating parameter combination %d/%d...", i + 1, len(param_grid))

        result = evaluate_parameters(params, klines, names, backtest_start_date)

        if result is None:
            continue

        # 获取优化指标值
        if metric == "sharpe_ratio":
            metric_value = result.sharpe_ratio
        elif metric == "total_return":
            metric_value = result.total_return
        elif metric == "profit_factor":
            metric_value = result.profit_factor
        else:
            metric_value = result.sharpe_ratio

        # 更新最佳结果
        if metric_value > best_metric_value:
            best_metric_value = metric_value
            best_result = result
            logger.info(
                "New best result: %s=%.4f (Total Return=%.2f%%, Sharpe=%.2f, Max DD=%.2f%%)",
                metric,
                metric_value,
                result.total_return,
                result.sharpe_ratio,
                result.max_drawdown,
            )

    if best_result is None:
        logger.error("No valid parameter combination found")
        return OptimizationResult(
            parameters=RumiParameters(),
            total_return=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            trade_count=0,
            profit_factor=0.0,
        )

    logger.info(
        "Optimization complete!\n"
        "Best parameters:\n"
        "  Rumi Lookback: %d\n"
        "  Rumi Min Score: %.1f\n"
        "  Momentum Weight: %.2f\n"
        "  Relative Strength Weight: %.2f\n"
        "  Volatility Weight: %.2f\n"
        "  KRange ATR Period: %d\n"
        "  KRange Multiplier: %.2f\n"
        "  KRange Exit Threshold: %.2f\n"
        "  Trailing Stop ATR Multiplier: %.2f\n"
        "  Trailing Stop Min Pct: %.2f\n"
        "  Min Rumi Score Entry: %.1f\n"
        "Performance:\n"
        "  Total Return: %.2f%%\n"
        "  Sharpe Ratio: %.2f\n"
        "  Max Drawdown: %.2f%%\n"
        "  Win Rate: %.1f%%\n"
        "  Trade Count: %d\n"
        "  Profit Factor: %.2f",
        best_result.parameters.rumi_lookback,
        best_result.parameters.rumi_min_score,
        best_result.parameters.rumi_momentum_weight,
        best_result.parameters.rumi_relative_strength_weight,
        best_result.parameters.rumi_volatility_weight,
        best_result.parameters.krange_atr_period,
        best_result.parameters.krange_multiplier,
        best_result.parameters.krange_exit_threshold,
        best_result.parameters.trailing_stop_atr_multiplier,
        best_result.parameters.trailing_stop_min_pct,
        best_result.parameters.min_rumi_score_entry,
        best_result.total_return,
        best_result.sharpe_ratio,
        best_result.max_drawdown,
        best_result.win_rate,
        best_result.trade_count,
        best_result.profit_factor,
    )

    return best_result


def log_optimization_result(result: OptimizationResult) -> None:
    """记录优化结果到日志。"""
    logger.info(
        "Rumi Optimization Result:\n"
        "  Parameters:\n"
        "    Rumi Lookback: %d\n"
        "    Rumi Min Score: %.1f\n"
        "    Momentum Weight: %.2f\n"
        "    Relative Strength Weight: %.2f\n"
        "    Volatility Weight: %.2f\n"
        "    KRange ATR Period: %d\n"
        "    KRange Multiplier: %.2f\n"
        "    KRange Exit Threshold: %.2f\n"
        "  Performance:\n"
        "    Total Return: %.2f%%\n"
        "    Sharpe Ratio: %.2f\n"
        "    Max Drawdown: %.2f%%\n"
        "    Win Rate: %.1f%%\n"
        "    Trade Count: %d\n"
        "    Profit Factor: %.2f",
        result.parameters.rumi_lookback,
        result.parameters.rumi_min_score,
        result.parameters.rumi_momentum_weight,
        result.parameters.rumi_relative_strength_weight,
        result.parameters.rumi_volatility_weight,
        result.parameters.krange_atr_period,
        result.parameters.krange_multiplier,
        result.parameters.krange_exit_threshold,
        result.total_return,
        result.sharpe_ratio,
        result.max_drawdown,
        result.win_rate,
        result.trade_count,
        result.profit_factor,
    )
