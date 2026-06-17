"""智能滑点模型 - 考虑市场冲击和流动性

实现基于 Almgren-Chriss 模型的市场冲击计算，
并结合实际流动性情况估算真实滑点。

核心公式：
- 临时冲击 = 0.1 × σ × (Q/ADV)
- 永久冲击 = 0.01 × σ × √(Q/ADV)
- 总滑点 = 基础滑点 + 临时冲击 + 永久冲击

参考文献：
- Almgren & Chriss (2000) "Optimal execution of portfolio transactions"
- Kissell & Glantz (2003) "Optimal Trading Strategies"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SlippageConfig:
    """滑点配置"""

    # 基础滑点
    base_slippage: float = 0.001  # 0.1%

    # 市场冲击参数
    temp_impact_coeff: float = 0.1  # 临时冲击系数
    perm_impact_coeff: float = 0.01  # 永久冲击系数

    # 流动性参数
    min_participation: float = 0.001  # 最小参与率
    max_participation: float = 0.10  # 最大参与率

    # 滑点限制
    min_slippage: float = 0.0005  # 最小滑点 0.05%
    max_slippage: float = 0.01  # 最大滑点 1%

    # 小盘股惩罚
    small_cap_threshold: float = 50e8  # 50亿市值
    small_cap_penalty: float = 0.002  # 小盘股额外滑点 0.2%


class MarketImpactModel:
    """市场冲击模型

    实现 Almgren-Chriss 模型的简化版本，
    用于估算订单对市场价格的影响。
    """

    def __init__(self, config: SlippageConfig | None = None):
        self.config = config or SlippageConfig()

    def calculate_participation_rate(
        self,
        order_amount: float,
        daily_volume: float,
    ) -> float:
        """计算参与率

        Args:
            order_amount: 订单金额
            daily_volume: 日均成交额

        Returns:
            float: 参与率 (0-1)
        """
        if daily_volume <= 0:
            return self.config.max_participation

        participation = order_amount / daily_volume

        # 限制在合理范围
        return max(
            self.config.min_participation,
            min(participation, self.config.max_participation),
        )

    def calculate_temporary_impact(
        self,
        volatility: float,
        participation: float,
    ) -> float:
        """计算临时冲击

        临时冲击：订单执行期间对价格的临时影响

        Args:
            volatility: 年化波动率
            participation: 参与率

        Returns:
            float: 临时冲击成本（百分比）
        """
        return self.config.temp_impact_coeff * volatility * participation

    def calculate_permanent_impact(
        self,
        volatility: float,
        participation: float,
    ) -> float:
        """计算永久冲击

        永久冲击：订单执行后对价格的永久影响

        Args:
            volatility: 年化波动率
            participation: 参与率

        Returns:
            float: 永久冲击成本（百分比）
        """
        return self.config.perm_impact_coeff * volatility * np.sqrt(participation)

    def calculate_market_impact(
        self,
        order_amount: float,
        daily_volume: float,
        volatility: float,
    ) -> float:
        """计算市场冲击

        Args:
            order_amount: 订单金额
            daily_volume: 日均成交额
            volatility: 年化波动率

        Returns:
            float: 市场冲击成本（百分比）
        """
        participation = self.calculate_participation_rate(order_amount, daily_volume)

        temp_impact = self.calculate_temporary_impact(volatility, participation)
        perm_impact = self.calculate_permanent_impact(volatility, participation)

        total_impact = temp_impact + perm_impact

        logger.debug(
            "Market impact: participation=%.4f, temp=%.4f, perm=%.4f, total=%.4f",
            participation,
            temp_impact,
            perm_impact,
            total_impact,
        )

        return total_impact


class SlippageModel:
    """智能滑点模型

    综合考虑：
    - 基础滑点
    - 市场冲击
    - 流动性
    - 小盘股惩罚
    """

    def __init__(self, config: SlippageConfig | None = None):
        self.config = config or SlippageConfig()
        self.impact_model = MarketImpactModel(config)

    def calculate_slippage(
        self,
        order_amount: float,
        daily_volume: float,
        volatility: float,
        market_cap: float = 0.0,
        is_buy: bool = True,
    ) -> float:
        """计算智能滑点

        Args:
            order_amount: 订单金额
            daily_volume: 日均成交额
            volatility: 年化波动率
            market_cap: 市值（可选）
            is_buy: 是否是买入

        Returns:
            float: 滑点成本（百分比）
        """
        # 基础滑点
        base_slippage = self.config.base_slippage

        # 市场冲击
        market_impact = self.impact_model.calculate_market_impact(
            order_amount, daily_volume, volatility
        )

        # 小盘股惩罚
        small_cap_penalty = 0.0
        if market_cap > 0 and market_cap < self.config.small_cap_threshold:
            small_cap_penalty = self.config.small_cap_penalty
            logger.debug(
                "Small cap penalty: market_cap=%.2f亿, penalty=%.4f",
                market_cap / 1e8,
                small_cap_penalty,
            )

        # 总滑点
        total_slippage = base_slippage + market_impact + small_cap_penalty

        # 限制在合理范围
        total_slippage = max(self.config.min_slippage, total_slippage)
        total_slippage = min(self.config.max_slippage, total_slippage)

        logger.debug(
            "Slippage calculation: base=%.4f, impact=%.4f, small_cap=%.4f, total=%.4f",
            base_slippage,
            market_impact,
            small_cap_penalty,
            total_slippage,
        )

        return total_slippage


def calculate_smart_slippage(
    order_amount: float,
    daily_volume: float,
    volatility: float,
    market_cap: float = 0.0,
    is_buy: bool = True,
    config: SlippageConfig | None = None,
) -> float:
    """计算智能滑点（便捷函数）

    Args:
        order_amount: 订单金额
        daily_volume: 日均成交额
        volatility: 年化波动率
        market_cap: 市值（可选）
        is_buy: 是否是买入
        config: 滑点配置（可选）

    Returns:
        float: 滑点成本（百分比）
    """
    model = SlippageModel(config)
    return model.calculate_slippage(order_amount, daily_volume, volatility, market_cap, is_buy)


def calculate_slippage_from_kline(
    order_amount: float,
    kline_data: pd.DataFrame,
    market_cap: float = 0.0,
    lookback_days: int = 20,
) -> float:
    """基于K线数据计算滑点

    Args:
        order_amount: 订单金额
        kline_data: K线数据
        market_cap: 市值（可选）
        lookback_days: 回溯天数

    Returns:
        float: 滑点成本（百分比）
    """
    if kline_data is None or kline_data.empty:
        return 0.002  # 默认 0.2%

    # 计算日均成交额
    if "amount" in kline_data.columns:
        daily_volume = kline_data["amount"].iloc[-lookback_days:].mean()
    elif "volume" in kline_data.columns and "close" in kline_data.columns:
        daily_volume = (
            kline_data["volume"].iloc[-lookback_days:] * kline_data["close"].iloc[-lookback_days:]
        ).mean()
    else:
        daily_volume = 1e8  # 默认 1亿

    # 计算波动率
    if "close" in kline_data.columns:
        returns = kline_data["close"].pct_change().iloc[-lookback_days:]
        volatility = returns.std() * np.sqrt(252)
    else:
        volatility = 0.3  # 默认 30%

    # 计算滑点
    return calculate_smart_slippage(
        order_amount=order_amount,
        daily_volume=daily_volume,
        volatility=volatility,
        market_cap=market_cap,
    )


class AdaptiveSlippageModel:
    """自适应滑点模型

    根据市场环境动态调整滑点估计。
    """

    def __init__(self, base_config: SlippageConfig | None = None):
        self.base_config = base_config or SlippageConfig()
        self.regime_adjustments = {
            "bull": 0.8,  # 牛市滑点较小
            "bear": 1.5,  # 熊市滑点较大
            "sideways": 1.0,  # 震荡市正常
            "high_volatility": 2.0,  # 高波动滑点很大
        }

    def calculate_adaptive_slippage(
        self,
        order_amount: float,
        daily_volume: float,
        volatility: float,
        market_regime: str = "sideways",
        market_cap: float = 0.0,
    ) -> float:
        """计算自适应滑点

        Args:
            order_amount: 订单金额
            daily_volume: 日均成交额
            volatility: 年化波动率
            market_regime: 市场环境
            market_cap: 市值

        Returns:
            float: 调整后的滑点成本（百分比）
        """
        # 基础滑点
        base_slippage = calculate_smart_slippage(order_amount, daily_volume, volatility, market_cap)

        # 市场环境调整
        adjustment = self.regime_adjustments.get(market_regime, 1.0)
        adjusted_slippage = base_slippage * adjustment

        logger.debug(
            "Adaptive slippage: regime=%s, base=%.4f, adjustment=%.2f, final=%.4f",
            market_regime,
            base_slippage,
            adjustment,
            adjusted_slippage,
        )

        return adjusted_slippage


# 便捷函数
def get_slippage_estimate(
    price: float,
    shares: int,
    daily_volume: float,
    volatility: float,
    market_cap: float = 0.0,
) -> float:
    """获取滑点估计（便捷函数）

    Args:
        price: 价格
        shares: 股数
        daily_volume: 日均成交额
        volatility: 年化波动率
        market_cap: 市值

    Returns:
        float: 滑点成本（百分比）
    """
    order_amount = price * shares
    return calculate_smart_slippage(
        order_amount=order_amount,
        daily_volume=daily_volume,
        volatility=volatility,
        market_cap=market_cap,
    )


def validate_slippage_estimate(
    slippage: float,
    min_slippage: float = 0.0005,
    max_slippage: float = 0.01,
) -> bool:
    """验证滑点估计是否合理

    Args:
        slippage: 滑点估计
        min_slippage: 最小滑点
        max_slippage: 最大滑点

    Returns:
        bool: 是否合理
    """
    return min_slippage <= slippage <= max_slippage
