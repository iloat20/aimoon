"""基于 Regime 的自适应策略引擎。

根据市场状态动态调整策略参数，包括：
1. 因子权重调整
2. 仓位管理优化
3. 止损止盈调整
4. 交易频率控制
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from aimoon.regime_enhanced import EnhancedMarketRegime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdaptiveStrategyConfig:
    """自适应策略配置。"""

    # 因子权重调整
    momentum_weight_scale: float = 1.0  # 动量因子权重缩放
    value_weight_scale: float = 1.0  # 价值因子权重缩放
    quality_weight_scale: float = 1.0  # 质量因子权重缩放

    # 仓位管理
    max_position_scale: float = 1.0  # 最大仓位比例
    min_position_scale: float = 0.1  # 最小仓位比例
    position_step: float = 0.1  # 仓位调整步长

    # 止损止盈
    stop_loss_scale: float = 1.0  # 止损比例缩放
    take_profit_scale: float = 1.0  # 止盈比例缩放

    # 交易频率
    rebalance_frequency: int = 3  # 调仓频率（天）
    min_hold_days: int = 5  # 最小持仓天数


@dataclass(frozen=True)
class AdaptiveStrategy:
    """自适应策略实例。"""

    regime: EnhancedMarketRegime
    config: AdaptiveStrategyConfig
    weights: dict[str, float]  # 因子权重
    position_scale: float  # 仓位比例
    stop_loss_pct: float  # 止损比例
    take_profit_pct: float  # 止盈比例
    rebalance_freq: int  # 调仓频率
    min_hold_days: int  # 最小持仓天数


def _get_regime_factor_weights(regime: EnhancedMarketRegime) -> dict[str, float]:
    """根据 regime 调整因子权重。

    Returns:
        dict[str, float]: 因子权重字典
    """
    state = regime.state
    confidence = regime.confidence

    # 基础权重
    base_weights = {
        "momentum": 0.3,
        "value": 0.3,
        "quality": 0.2,
        "size": 0.1,
        "volatility": 0.1,
    }

    # 根据 regime 调整权重
    if state == "bull":
        # 牛市：增加动量因子权重
        return {
            "momentum": 0.4 * confidence,
            "value": 0.2,
            "quality": 0.2,
            "size": 0.1,
            "volatility": 0.1,
        }
    elif state == "bear":
        # 熊市：增加价值和质量因子权重
        return {
            "momentum": 0.1,
            "value": 0.4 * confidence,
            "quality": 0.3 * confidence,
            "size": 0.1,
            "volatility": 0.1,
        }
    elif state == "high_volatility":
        # 高波动：增加波动率因子权重
        return {
            "momentum": 0.2,
            "value": 0.2,
            "quality": 0.2,
            "size": 0.1,
            "volatility": 0.3 * confidence,
        }
    elif state == "crisis":
        # 危机：大幅降低动量，增加防御性因子
        return {
            "momentum": 0.05,
            "value": 0.3,
            "quality": 0.4 * confidence,
            "size": 0.1,
            "volatility": 0.15,
        }
    else:  # sideways
        # 震荡：均衡配置
        return base_weights


def _get_regime_position_scale(regime: EnhancedMarketRegime) -> float:
    """根据 regime 调整仓位比例。

    Returns:
        float: 仓位比例（0.0-1.0）
    """
    # 使用 regime 的 position_scale 属性
    return regime.position_scale


def _get_regime_stop_loss(regime: EnhancedMarketRegime, base_stop_loss: float = 0.05) -> float:
    """根据 regime 调整止损比例。

    Returns:
        float: 止损比例
    """
    state = regime.state

    # 高波动和危机模式使用更宽松的止损
    if state == "high_volatility":
        return base_stop_loss * 1.5  # 增加 50%
    elif state == "crisis":
        return base_stop_loss * 2.0  # 增加 100%
    elif state == "bear":
        return base_stop_loss * 1.2  # 增加 20%
    else:
        return base_stop_loss


def _get_regime_take_profit(regime: EnhancedMarketRegime, base_take_profit: float = 0.15) -> float:
    """根据 regime 调整止盈比例。

    Returns:
        float: 止盈比例
    """
    state = regime.state

    # 牛市使用更宽松的止盈
    if state == "bull":
        return base_take_profit * 1.5  # 增加 50%
    elif state == "bear":
        return base_take_profit * 0.7  # 降低 30%
    elif state == "high_volatility":
        return base_take_profit * 1.2  # 增加 20%
    else:
        return base_take_profit


def _get_regime_rebalance_freq(regime: EnhancedMarketRegime, base_freq: int = 3) -> int:
    """根据 regime 调整调仓频率。

    Returns:
        int: 调仓频率（天）
    """
    state = regime.state

    # 高波动和危机模式增加调仓频率
    if state == "high_volatility":
        return max(1, base_freq // 2)  # 减半
    elif state == "crisis":
        return 1  # 每天调仓
    elif state == "bull":
        return base_freq * 2  # 增加调仓间隔
    else:
        return base_freq


def create_adaptive_strategy(
    regime: EnhancedMarketRegime,
    base_stop_loss: float = 0.05,
    base_take_profit: float = 0.15,
    base_rebalance_freq: int = 3,
) -> AdaptiveStrategy:
    """创建基于 regime 的自适应策略。

    Args:
        regime: 市场状态
        base_stop_loss: 基础止损比例
        base_take_profit: 基础止盈比例
        base_rebalance_freq: 基础调仓频率

    Returns:
        AdaptiveStrategy: 自适应策略实例
    """
    # 计算因子权重
    weights = _get_regime_factor_weights(regime)

    # 计算仓位比例
    position_scale = _get_regime_position_scale(regime)

    # 计算止损止盈
    stop_loss_pct = _get_regime_stop_loss(regime, base_stop_loss)
    take_profit_pct = _get_regime_take_profit(regime, base_take_profit)

    # 计算调仓频率
    rebalance_freq = _get_regime_rebalance_freq(regime, base_rebalance_freq)

    # 计算最小持仓天数
    min_hold_days = max(3, rebalance_freq * 2)

    # 创建配置
    config = AdaptiveStrategyConfig(
        momentum_weight_scale=weights.get("momentum", 0.3),
        value_weight_scale=weights.get("value", 0.3),
        quality_weight_scale=weights.get("quality", 0.2),
        max_position_scale=position_scale,
        min_position_scale=0.1,
        position_step=0.1,
        stop_loss_scale=stop_loss_pct / base_stop_loss if base_stop_loss > 0 else 1.0,
        take_profit_scale=(take_profit_pct / base_take_profit if base_take_profit > 0 else 1.0),
        rebalance_frequency=rebalance_freq,
        min_hold_days=min_hold_days,
    )

    return AdaptiveStrategy(
        regime=regime,
        config=config,
        weights=weights,
        position_scale=position_scale,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        rebalance_freq=rebalance_freq,
        min_hold_days=min_hold_days,
    )


def apply_adaptive_strategy(
    strategy: AdaptiveStrategy,
    positions: dict[str, dict],
    klines: dict[str, pd.DataFrame],
    bar_date: pd.Timestamp,
) -> dict[str, dict]:
    """应用自适应策略到持仓。

    Args:
        strategy: 自适应策略
        positions: 当前持仓
        klines: K 线数据
        bar_date: 当前日期

    Returns:
        dict[str, dict]: 更新后的持仓
    """
    updated_positions = {}

    for code, pos in positions.items():
        if code not in klines or bar_date not in klines[code].index:
            continue

        # 计算当前 PnL
        current_price = float(klines[code].loc[bar_date, "close"])
        entry_price = pos.get("entry_price", current_price)
        pnl = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0

        # 根据 regime 调整止损
        adjusted_stop_loss = strategy.stop_loss_pct

        # 根据 PnL 调整止损（阶梯式移动止损）
        if pnl >= 0.05:  # 盈利 5% 以上
            adjusted_stop_loss = max(adjusted_stop_loss, 0.0)  # 保本保护
        if pnl >= 0.10:  # 盈利 10% 以上
            adjusted_stop_loss = max(adjusted_stop_loss, pnl * 0.5)  # 锁定 50% 利润

        # 更新持仓
        updated_pos = {
            **pos,
            "stop_loss": adjusted_stop_loss,
            "take_profit": strategy.take_profit_pct,
            "regime_state": strategy.regime.state,
            "regime_confidence": strategy.regime.confidence,
        }

        updated_positions[code] = updated_pos

    return updated_positions


def log_adaptive_strategy(strategy: AdaptiveStrategy) -> None:
    """记录自适应策略到日志。"""
    logger.info(
        "Adaptive Strategy - Regime: %s (confidence: %.2f)\n"
        "  Factor weights: momentum=%.2f, value=%.2f, quality=%.2f\n"
        "  Position scale: %.2f\n"
        "  Stop loss: %.2f%%\n"
        "  Take profit: %.2f%%\n"
        "  Rebalance freq: %d days\n"
        "  Min hold days: %d days",
        strategy.regime.state,
        strategy.regime.confidence,
        strategy.weights.get("momentum", 0),
        strategy.weights.get("value", 0),
        strategy.weights.get("quality", 0),
        strategy.position_scale,
        strategy.stop_loss_pct * 100,
        strategy.take_profit_pct * 100,
        strategy.rebalance_freq,
        strategy.min_hold_days,
    )
