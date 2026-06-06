"""自适应权重系统 - 根据市场环境自动调整评分权重

功能：
1. 检测市场环境（牛市、熊市、震荡市）
2. 根据市场环境自动调整权重
3. 引入历史数据优化权重
4. 实时监控和调整
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from aimoon.scoring.hybrid_scorer import HybridScoreConfig

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """市场环境类型"""
    BULL = "bull"        # 牛市
    BEAR = "bear"        # 熊市
    SIDEWAYS = "sideways"  # 震荡市
    HIGH_VOL = "high_vol"  # 高波动


@dataclass
class RegimeWeights:
    """不同市场环境的权重配置"""
    bull: HybridScoreConfig
    bear: HybridScoreConfig
    sideways: HybridScoreConfig
    high_vol: HybridScoreConfig


@dataclass
class AdaptiveWeightConfig:
    """自适应权重配置"""
    # 检测参数
    lookback_period: int = 60  # 回溯期（天）
    volatility_threshold: float = 0.20  # 波动率阈值
    trend_threshold: float = 0.05  # 趋势阈值

    # 权重调整参数
    adjustment_speed: float = 0.1  # 调整速度（0-1）
    min_confidence: float = 0.6  # 最小置信度

    # 默认权重
    default_ml_weight: float = 0.40
    default_alpha_weight: float = 0.40
    default_momentum_weight: float = 0.20


class AdaptiveWeightSystem:
    """自适应权重系统"""

    def __init__(self, config: AdaptiveWeightConfig | None = None):
        self.config = config or AdaptiveWeightConfig()
        self.current_regime = MarketRegime.SIDEWAYS
        self.regime_history: list[MarketRegime] = []
        self.weight_history: list[HybridScoreConfig] = []
        self.confidence = 0.5

        # 定义不同市场环境的权重
        self.regime_weights = RegimeWeights(
            bull=HybridScoreConfig(
                ml_weight=0.45,      # 牛市：ML 表现好，增加权重
                alpha_weight=0.35,
                momentum_weight=0.20,
            ),
            bear=HybridScoreConfig(
                ml_weight=0.30,      # 熊市：Alpha 更重要
                alpha_weight=0.50,
                momentum_weight=0.20,
            ),
            sideways=HybridScoreConfig(
                ml_weight=0.35,      # 震荡：平衡配置
                alpha_weight=0.45,
                momentum_weight=0.20,
            ),
            high_vol=HybridScoreConfig(
                ml_weight=0.30,      # 高波动：Alpha 和动量更重要
                alpha_weight=0.40,
                momentum_weight=0.30,
            ),
        )

    def detect_regime(self, market_data: pd.DataFrame) -> MarketRegime:
        """检测市场环境

        Args:
            market_data: 市场数据（包含 close, volume 等）

        Returns:
            MarketRegime: 市场环境类型
        """
        if market_data is None or len(market_data) < self.config.lookback_period:
            logger.warning("Insufficient data for regime detection, using sideways")
            return MarketRegime.SIDEWAYS

        # 计算关键指标
        close = market_data['close'].tail(self.config.lookback_period)
        returns = close.pct_change().dropna()

        # 1. 计算波动率
        volatility = returns.std() * np.sqrt(252)  # 年化波动率

        # 2. 计算趋势（使用线性回归斜率）
        x = np.arange(len(close))
        slope = np.polyfit(x, close.values, 1)[0]
        trend = slope / close.mean()  # 归一化趋势

        # 3. 计算动量
        momentum = (close.iloc[-1] / close.iloc[0] - 1)

        # 4. 判断市场环境
        regime = self._classify_regime(volatility, trend, momentum)

        logger.info(
            "Regime detection: volatility=%.2f, trend=%.4f, momentum=%.2f -> %s",
            volatility, trend, momentum, regime.value
        )

        return regime

    def _classify_regime(
        self,
        volatility: float,
        trend: float,
        momentum: float,
    ) -> MarketRegime:
        """根据指标分类市场环境"""

        # 高波动环境
        if volatility > self.config.volatility_threshold:
            return MarketRegime.HIGH_VOL

        # 牛市：正趋势 + 正动量
        if trend > self.config.trend_threshold and momentum > 0:
            return MarketRegime.BULL

        # 熊市：负趋势 + 负动量
        if trend < -self.config.trend_threshold and momentum < 0:
            return MarketRegime.BEAR

        # 其他情况：震荡市
        return MarketRegime.SIDEWAYS

    def get_adaptive_weights(
        self,
        market_data: pd.DataFrame | None = None,
    ) -> HybridScoreConfig:
        """获取自适应权重

        Args:
            market_data: 市场数据（可选）

        Returns:
            HybridScoreConfig: 自适应权重配置
        """
        # 检测市场环境
        if market_data is not None:
            regime = self.detect_regime(market_data)
            self._update_regime(regime)

        # 获取基础权重
        base_weights = self._get_regime_weights(self.current_regime)

        # 应用平滑调整
        smoothed_weights = self._smooth_weights(base_weights)

        # 记录历史
        self.weight_history.append(smoothed_weights)

        logger.info(
            "Adaptive weights: regime=%s, ml=%.2f, alpha=%.2f, mom=%.2f",
            self.current_regime.value,
            smoothed_weights.ml_weight,
            smoothed_weights.alpha_weight,
            smoothed_weights.momentum_weight,
        )

        return smoothed_weights

    def _update_regime(self, new_regime: MarketRegime) -> None:
        """更新市场环境（带平滑）"""
        # 记录历史
        self.regime_history.append(new_regime)

        # 计算置信度（基于历史一致性）
        if len(self.regime_history) >= 5:
            recent = self.regime_history[-5:]
            same_count = sum(1 for r in recent if r == new_regime)
            self.confidence = same_count / 5.0
        else:
            self.confidence = 0.5

        # 只有在置信度足够高时才更新
        if self.confidence >= self.config.min_confidence:
            if self.current_regime != new_regime:
                logger.info(
                    "Regime change: %s -> %s (confidence=%.2f)",
                    self.current_regime.value,
                    new_regime.value,
                    self.confidence,
                )
                self.current_regime = new_regime

    def _get_regime_weights(self, regime: MarketRegime) -> HybridScoreConfig:
        """获取指定市场环境的权重"""
        return {
            MarketRegime.BULL: self.regime_weights.bull,
            MarketRegime.BEAR: self.regime_weights.bear,
            MarketRegime.SIDEWAYS: self.regime_weights.sideways,
            MarketRegime.HIGH_VOL: self.regime_weights.high_vol,
        }.get(regime, self.regime_weights.sideways)

    def _smooth_weights(self, target: HybridScoreConfig) -> HybridScoreConfig:
        """平滑权重调整（避免剧烈变化）"""
        if not self.weight_history:
            return target

        # 获取上一次的权重
        last = self.weight_history[-1]

        # 平滑调整
        speed = self.config.adjustment_speed
        ml = last.ml_weight + (target.ml_weight - last.ml_weight) * speed
        alpha = last.alpha_weight + (target.alpha_weight - last.alpha_weight) * speed
        mom = last.momentum_weight + (target.momentum_weight - last.momentum_weight) * speed

        # 归一化（确保总和为 1.0）
        total = ml + alpha + mom
        if total > 0:
            ml /= total
            alpha /= total
            mom /= total

        return HybridScoreConfig(
            ml_weight=ml,
            alpha_weight=alpha,
            momentum_weight=mom,
        )

    def get_regime_info(self) -> dict:
        """获取当前市场环境信息"""
        return {
            'current_regime': self.current_regime.value,
            'confidence': self.confidence,
            'history_length': len(self.regime_history),
            'weights': {
                'ml': self.weight_history[-1].ml_weight if self.weight_history else self.config.default_ml_weight,
                'alpha': self.weight_history[-1].alpha_weight if self.weight_history else self.config.default_alpha_weight,
                'momentum': self.weight_history[-1].momentum_weight if self.weight_history else self.config.default_momentum_weight,
            },
        }

    def reset(self) -> None:
        """重置系统"""
        self.current_regime = MarketRegime.SIDEWAYS
        self.regime_history.clear()
        self.weight_history.clear()
        self.confidence = 0.5


class AutoFactorSelector:
    """因子自动选择系统

    功能：
    1. 计算因子 IC（信息系数）
    2. 自动选择高 IC 因子
    3. 动态调整因子权重
    4. 持续监控因子效果
    """

    def __init__(self, min_ic: float = 0.05, max_factors: int = 100):
        self.min_ic = min_ic  # 最小 IC 阈值
        self.max_factors = max_factors  # 最大因子数
        self.factor_ic: dict[str, float] = {}
        self.factor_history: dict[str, list[float]] = {}
        self.selected_factors: list[str] = []

    def compute_factor_ic(
        self,
        factor_values: pd.Series,
        returns: pd.Series,
    ) -> float:
        """计算因子 IC（信息系数）

        Args:
            factor_values: 因子值
            returns: 未来收益

        Returns:
            float: IC 值（-1 到 1）
        """
        # 对齐数据
        common_idx = factor_values.index.intersection(returns.index)
        if len(common_idx) < 10:
            return 0.0

        fv = factor_values[common_idx]
        ret = returns[common_idx]

        # 计算 Spearman 秩相关系数
        from scipy.stats import spearmanr
        ic, _ = spearmanr(fv, ret)

        return ic if not np.isnan(ic) else 0.0

    def update_factor_ic(
        self,
        factor_name: str,
        factor_values: pd.Series,
        returns: pd.Series,
    ) -> None:
        """更新因子 IC

        Args:
            factor_name: 因子名称
            factor_values: 因子值
            returns: 未来收益
        """
        ic = self.compute_factor_ic(factor_values, returns)

        # 更新历史
        if factor_name not in self.factor_history:
            self.factor_history[factor_name] = []
        self.factor_history[factor_name].append(ic)

        # 计算滚动平均 IC（最近 10 次）
        history = self.factor_history[factor_name][-10:]
        avg_ic = np.mean(history) if history else ic

        # 更新 IC
        self.factor_ic[factor_name] = avg_ic

        logger.debug("Factor %s IC: %.4f (avg: %.4f)", factor_name, ic, avg_ic)

    def select_factors(self) -> list[str]:
        """选择高 IC 因子

        Returns:
            list[str]: 选中的因子列表
        """
        # 按 IC 排序
        sorted_factors = sorted(
            self.factor_ic.items(),
            key=lambda x: abs(x[1]),  # 使用绝对值
            reverse=True,
        )

        # 选择 IC >= min_ic 的因子
        selected = [
            name for name, ic in sorted_factors
            if abs(ic) >= self.min_ic
        ]

        # 限制最大因子数
        selected = selected[:self.max_factors]

        self.selected_factors = selected

        logger.info(
            "Selected %d factors (from %d total, min_ic=%.4f)",
            len(selected),
            len(self.factor_ic),
            self.min_ic,
        )

        return selected

    def get_factor_weights(self) -> dict[str, float]:
        """获取因子权重（基于 IC）

        Returns:
            dict[str, float]: 因子权重字典
        """
        if not self.selected_factors:
            return {}

        # 使用 IC 的绝对值作为权重
        weights = {}
        for name in self.selected_factors:
            ic = abs(self.factor_ic.get(name, 0))
            weights[name] = ic

        # 归一化
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights

    def get_factor_stats(self) -> dict:
        """获取因子统计信息"""
        if not self.factor_ic:
            return {}

        sorted_factors = sorted(
            self.factor_ic.items(),
            key=lambda x: abs(x[1]),
            reverse=True,
        )

        return {
            'total_factors': len(self.factor_ic),
            'selected_factors': len(self.selected_factors),
            'top_10': sorted_factors[:10],
            'avg_ic': np.mean(list(self.factor_ic.values())),
            'max_ic': max(self.factor_ic.values()),
            'min_ic': min(self.factor_ic.values()),
        }

    def reset(self) -> None:
        """重置系统"""
        self.factor_ic.clear()
        self.factor_history.clear()
        self.selected_factors.clear()


class ContinuousOptimizer:
    """持续优化系统

    功能：
    1. 监控策略表现
    2. 定期优化参数
    3. 记录优化历史
    4. 自动回滚差参数
    """

    def __init__(self, lookback_days: int = 30):
        self.lookback_days = lookback_days
        self.performance_history: list[dict] = []
        self.parameter_history: list[dict] = []
        self.best_parameters: dict | None = None
        self.best_performance: float = -np.inf

    def record_performance(
        self,
        returns: pd.Series,
        parameters: dict,
    ) -> None:
        """记录策略表现

        Args:
            returns: 策略收益序列
            parameters: 当前参数
        """
        # 计算关键指标
        total_return = (1 + returns).prod() - 1
        annual_return = (1 + total_return) ** (252 / len(returns)) - 1
        volatility = returns.std() * np.sqrt(252)
        sharpe = annual_return / volatility if volatility > 0 else 0
        max_drawdown = self._compute_max_drawdown(returns)

        performance = {
            'timestamp': pd.Timestamp.now(),
            'total_return': total_return,
            'annual_return': annual_return,
            'volatility': volatility,
            'sharpe': sharpe,
            'max_drawdown': max_drawdown,
        }

        self.performance_history.append(performance)
        self.parameter_history.append(parameters)

        # 更新最佳参数
        if sharpe > self.best_performance:
            self.best_performance = sharpe
            self.best_parameters = parameters.copy()

        logger.info(
            "Recorded performance: return=%.2f%%, sharpe=%.2f, max_dd=%.2f%%",
            total_return * 100,
            sharpe,
            max_drawdown * 100,
        )

    def _compute_max_drawdown(self, returns: pd.Series) -> float:
        """计算最大回撤"""
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        return abs(drawdown.min())

    def should_optimize(self) -> bool:
        """判断是否需要优化"""
        if len(self.performance_history) < 5:
            return False

        # 检查最近表现
        recent = self.performance_history[-5:]
        avg_sharpe = np.mean([p['sharpe'] for p in recent])

        # 如果表现下降，需要优化
        if avg_sharpe < self.best_performance * 0.8:
            logger.info("Performance declined, should optimize")
            return True

        return False

    def suggest_parameters(self) -> dict | None:
        """建议新参数

        Returns:
            dict: 建议的参数，或 None 如果不需要优化
        """
        if not self.should_optimize():
            return None

        # 返回最佳历史参数
        return self.best_parameters

    def get_optimization_report(self) -> dict:
        """获取优化报告"""
        if not self.performance_history:
            return {}

        recent = self.performance_history[-self.lookback_days:]

        return {
            'total_records': len(self.performance_history),
            'recent_performance': {
                'avg_return': np.mean([p['total_return'] for p in recent]),
                'avg_sharpe': np.mean([p['sharpe'] for p in recent]),
                'avg_max_dd': np.mean([p['max_drawdown'] for p in recent]),
            },
            'best_performance': self.best_performance,
            'best_parameters': self.best_parameters,
        }

    def reset(self) -> None:
        """重置系统"""
        self.performance_history.clear()
        self.parameter_history.clear()
        self.best_parameters = None
        self.best_performance = -np.inf


# 便捷函数
def create_adaptive_system(
    config: AdaptiveWeightConfig | None = None,
) -> AdaptiveWeightSystem:
    """创建自适应权重系统"""
    return AdaptiveWeightSystem(config)


def create_factor_selector(
    min_ic: float = 0.05,
    max_factors: int = 100,
) -> AutoFactorSelector:
    """创建因子自动选择系统"""
    return AutoFactorSelector(min_ic, max_factors)


def create_continuous_optimizer(
    lookback_days: int = 30,
) -> ContinuousOptimizer:
    """创建持续优化系统"""
    return ContinuousOptimizer(lookback_days)
