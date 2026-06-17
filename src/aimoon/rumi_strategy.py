"""Rumi Strategy with KRange Adaptive Exit Mechanism.

Rumi (Relative Strength + Momentum) 策略实现。
结合 KRange 自适应离场机制，实现智能跟踪止损。

核心逻辑：
1. Rumi 策略：基于动量和相对强度的入场信号
2. KRange 机制：基于波动率的自适应离场
3. 智能跟踪止损：根据市场状态动态调整止损位置
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RumiSignal:
    """Rumi 信号。"""

    code: str
    name: str
    rumi_score: float  # Rumi 综合得分 (0-100)
    momentum_score: float  # 动量得分
    relative_strength: float  # 相对强度
    volatility: float  # 波动率
    signal_type: str  # "buy", "sell", or "hold"


@dataclass(frozen=True)
class KRangeExit:
    """KRange 离场信号。"""

    code: str
    current_price: float
    krange_upper: float  # KRange 上轨
    krange_lower: float  # KRange 下轨
    atr_value: float  # ATR 值
    exit_type: Literal["stop_loss", "take_profit", "trailing_stop", "none"]
    exit_price: float
    exit_reason: str


@dataclass(frozen=True)
class RumiPosition:
    """Rumi 持仓。"""

    code: str
    name: str
    entry_price: float
    entry_date: pd.Timestamp
    current_price: float
    highest_price: float
    lowest_price: float
    rumi_score: float
    atr_at_entry: float
    krange_upper: float
    krange_lower: float
    trailing_stop: float
    pnl: float
    hold_days: int


def compute_rumi_score(
    kline: pd.DataFrame,
    lookback: int = 20,
    momentum_weight: float = 0.4,
    relative_strength_weight: float = 0.3,
    volatility_weight: float = 0.3,
) -> tuple[float, float, float, float]:
    """计算 Rumi 综合得分。

    Args:
        kline: K 线数据
        lookback: 回看周期
        momentum_weight: 动量权重
        relative_strength_weight: 相对强度权重
        volatility_weight: 波动率权重

    Returns:
        tuple: (rumi_score, momentum_score, relative_strength, volatility)
    """
    if len(kline) < lookback:
        return 0.0, 0.0, 0.0, 0.0

    close = kline["close"]
    high = kline["high"]
    low = kline["low"]
    volume = kline["volume"]

    # 1. 动量得分 (Momentum Score)
    # 基于价格动量和成交量动量
    returns = close.pct_change().dropna()
    price_momentum = float(returns.iloc[-lookback:].mean()) if len(returns) >= lookback else 0.0
    volume_momentum = (
        float(volume.iloc[-1] / volume.iloc[-lookback:].mean() - 1)
        if len(volume) >= lookback
        else 0.0
    )

    # 归一化动量得分 (0-100)
    momentum_raw = price_momentum * 100 + volume_momentum * 50
    momentum_score = np.clip(momentum_raw, -100, 100)
    momentum_score = (momentum_score + 100) / 2  # 转换到 0-100

    # 2. 相对强度 (Relative Strength)
    # 基于价格相对于移动平均线的强度
    ma20 = close.rolling(window=20).mean()
    ma60 = close.rolling(window=60).mean()

    if not pd.isna(ma20.iloc[-1]) and not pd.isna(ma60.iloc[-1]):
        price_vs_ma20 = float(close.iloc[-1] / ma20.iloc[-1] - 1)
        price_vs_ma60 = float(close.iloc[-1] / ma60.iloc[-1] - 1)
        relative_strength = (price_vs_ma20 * 0.6 + price_vs_ma60 * 0.4) * 100
    else:
        relative_strength = 0.0

    # 归一化相对强度 (0-100)
    relative_strength = np.clip(relative_strength, -100, 100)
    relative_strength = (relative_strength + 100) / 2

    # 3. 波动率得分 (Volatility Score)
    # 基于 ATR 和价格波动范围
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(window=14).mean()

    if not pd.isna(atr.iloc[-1]):
        atr_ratio = float(atr.iloc[-1] / close.iloc[-1])
        # 低波动率得分高（稳定），高波动率得分低（风险高）
        volatility = max(0, 100 - atr_ratio * 1000)
    else:
        volatility = 50.0

    # 4. 综合 Rumi 得分
    rumi_score = (
        momentum_score * momentum_weight
        + relative_strength * relative_strength_weight
        + volatility * volatility_weight
    )

    return rumi_score, momentum_score, relative_strength, volatility


def compute_krange(
    kline: pd.DataFrame,
    atr_period: int = 14,
    krange_multiplier: float = 2.0,
) -> tuple[float, float, float]:
    """计算 KRange 上下轨和 ATR。

    Args:
        kline: K 线数据
        atr_period: ATR 周期
        krange_multiplier: KRange 乘数

    Returns:
        tuple: (krange_upper, krange_lower, atr_value)
    """
    if len(kline) < atr_period:
        return 0.0, 0.0, 0.0

    close = kline["close"]
    high = kline["high"]
    low = kline["low"]

    # 计算 ATR
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(window=atr_period).mean()

    if pd.isna(atr.iloc[-1]):
        return 0.0, 0.0, 0.0

    atr_value = float(atr.iloc[-1])
    current_price = float(close.iloc[-1])

    # 计算 KRange 上下轨
    krange_upper = current_price + krange_multiplier * atr_value
    krange_lower = current_price - krange_multiplier * atr_value

    return krange_upper, krange_lower, atr_value


def compute_adaptive_trailing_stop(
    position: RumiPosition,
    current_price: float,
    atr_value: float,
    rumi_score: float,
    regime: str = "sideways",
) -> float:
    """计算自适应跟踪止损。

    Args:
        position: 持仓信息
        current_price: 当前价格
        atr_value: ATR 值
        rumi_score: Rumi 得分
        regime: 市场状态

    Returns:
        float: 跟踪止损价格
    """
    entry_price = position.entry_price
    highest_price = max(position.highest_price, current_price)
    pnl = (current_price - entry_price) / entry_price

    # 基础止损：基于 ATR
    base_stop = highest_price - 2.0 * atr_value

    # 根据 Rumi 得分调整止损
    # 高 Rumi 得分：更宽松的止损（给更多空间）
    # 低 Rumi 得分：更紧的止损（快速止损）
    rumi_factor = rumi_score / 100.0  # 0-1
    rumi_adjusted_stop = highest_price - (2.0 - rumi_factor * 0.5) * atr_value

    # 根据市场状态调整止损
    regime_factors = {
        "bull": 1.2,  # 牛市：更宽松
        "bear": 0.8,  # 熊市：更紧
        "sideways": 1.0,  # 震荡：标准
        "high_volatility": 0.9,  # 高波动：更紧
        "crisis": 0.7,  # 危机：最紧
    }
    regime_factor = regime_factors.get(regime, 1.0)
    regime_adjusted_stop = highest_price - (2.0 * regime_factor) * atr_value

    # 根据盈利调整止损（阶梯式移动止损）
    if pnl >= 0.10:  # 盈利 10% 以上
        # 保本保护
        profit_stop = max(entry_price, highest_price - 1.5 * atr_value)
    elif pnl >= 0.05:  # 盈利 5% 以上
        # 锁定 50% 利润
        profit_stop = entry_price + (highest_price - entry_price) * 0.5
    elif pnl >= 0.02:  # 盈利 2% 以上
        # 保本
        profit_stop = entry_price
    else:
        profit_stop = 0.0

    # 取最高止损
    trailing_stop = max(base_stop, rumi_adjusted_stop, regime_adjusted_stop, profit_stop)

    # 确保止损不低于入场价格的 8%
    min_stop = entry_price * 0.92
    trailing_stop = max(trailing_stop, min_stop)

    return trailing_stop


def generate_rumi_signals(
    klines: dict[str, pd.DataFrame],
    names: dict[str, str],
    lookback: int = 20,
    min_rumi_score: float = 60.0,
) -> list[RumiSignal]:
    """生成 Rumi 入场信号。

    Args:
        klines: K 线数据字典
        names: 股票名称字典
        lookback: 回看周期
        min_rumi_score: 最小 Rumi 得分阈值

    Returns:
        list[RumiSignal]: Rumi 信号列表
    """
    signals = []

    for code, kline in klines.items():
        if len(kline) < lookback:
            continue

        rumi_score, momentum_score, relative_strength, volatility = compute_rumi_score(
            kline, lookback
        )

        # 确定信号类型
        if rumi_score >= min_rumi_score:
            signal_type = "buy"
        elif rumi_score <= 20:
            signal_type = "sell"
        else:
            signal_type = "hold"

        signals.append(
            RumiSignal(
                code=code,
                name=names.get(code, code),
                rumi_score=rumi_score,
                momentum_score=momentum_score,
                relative_strength=relative_strength,
                volatility=volatility,
                signal_type=signal_type,
            )
        )

    # 按 Rumi 得分排序
    signals.sort(key=lambda x: x.rumi_score, reverse=True)

    return signals


def check_krange_exit(
    position: RumiPosition,
    kline: pd.DataFrame,
    current_date: pd.Timestamp,
    rumi_score: float,
    regime: str = "sideways",
    exit_threshold: float = 0.5,
) -> KRangeExit | None:
    """检查 KRange 离场信号。

    Args:
        position: 持仓信息
        kline: K 线数据
        current_date: 当前日期
        rumi_score: Rumi 得分
        regime: 市场状态
        exit_threshold: 离场阈值

    Returns:
        KRangeExit: 离场信号（如果没有离场信号则返回 None）
    """
    if current_date not in kline.index:
        return None

    current_price = float(kline.loc[current_date, "close"])
    krange_upper, krange_lower, atr_value = compute_krange(kline)

    if krange_upper == 0.0 or krange_lower == 0.0:
        return None

    # 计算自适应跟踪止损
    trailing_stop = compute_adaptive_trailing_stop(
        position, current_price, atr_value, rumi_score, regime
    )

    # 检查离场条件
    pnl = (current_price - position.entry_price) / position.entry_price

    # 1. 止损：价格跌破跟踪止损
    if current_price <= trailing_stop:
        return KRangeExit(
            code=position.code,
            current_price=current_price,
            krange_upper=krange_upper,
            krange_lower=krange_lower,
            atr_value=atr_value,
            exit_type="trailing_stop",
            exit_price=trailing_stop,
            exit_reason=f"跟踪止损触发 (止损价: {trailing_stop:.2f})",
        )

    # 2. 止盈：价格突破 KRange 上轨
    if current_price >= krange_upper:
        return KRangeExit(
            code=position.code,
            current_price=current_price,
            krange_upper=krange_upper,
            krange_lower=krange_lower,
            atr_value=atr_value,
            exit_type="take_profit",
            exit_price=krange_upper,
            exit_reason=f"KRange 上轨突破 (上轨: {krange_upper:.2f})",
        )

    # 3. Rumi 得分下降：动量减弱
    if rumi_score < exit_threshold * 100:
        return KRangeExit(
            code=position.code,
            current_price=current_price,
            krange_upper=krange_upper,
            krange_lower=krange_lower,
            atr_value=atr_value,
            exit_type="stop_loss",
            exit_price=current_price,
            exit_reason=f"Rumi 得分下降 (得分: {rumi_score:.1f})",
        )

    # 4. 时间止损：持仓超过 20 天
    if position.hold_days > 20 and pnl < 0.02:
        return KRangeExit(
            code=position.code,
            current_price=current_price,
            krange_upper=krange_upper,
            krange_lower=krange_lower,
            atr_value=atr_value,
            exit_type="stop_loss",
            exit_price=current_price,
            exit_reason=f"时间止损 (持仓: {position.hold_days} 天, PnL: {pnl:.2%})",
        )

    return None


def log_rumi_signal(signal: RumiSignal) -> None:
    """记录 Rumi 信号到日志。"""
    logger.info(
        "Rumi Signal - %s (%s)\n"
        "  Rumi Score: %.1f\n"
        "  Momentum Score: %.1f\n"
        "  Relative Strength: %.1f\n"
        "  Volatility: %.1f\n"
        "  Signal Type: %s",
        signal.code,
        signal.name,
        signal.rumi_score,
        signal.momentum_score,
        signal.relative_strength,
        signal.volatility,
        signal.signal_type,
    )


def log_krange_exit(exit_signal: KRangeExit) -> None:
    """记录 KRange 离场信号到日志。"""
    logger.info(
        "KRange Exit - %s\n"
        "  Current Price: %.2f\n"
        "  KRange Upper: %.2f\n"
        "  KRange Lower: %.2f\n"
        "  ATR: %.2f\n"
        "  Exit Type: %s\n"
        "  Exit Price: %.2f\n"
        "  Reason: %s",
        exit_signal.code,
        exit_signal.current_price,
        exit_signal.krange_upper,
        exit_signal.krange_lower,
        exit_signal.atr_value,
        exit_signal.exit_type,
        exit_signal.exit_price,
        exit_signal.exit_reason,
    )
