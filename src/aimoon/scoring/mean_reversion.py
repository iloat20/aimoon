"""均值回归策略 - 适合震荡市场"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeanReversionConfig:
    """均值回归策略配置"""

    # 超卖阈值
    rsi_oversold: int = 30
    bollinger_lower: float = 2.0

    # 超买阈值
    rsi_overbought: int = 70
    bollinger_upper: float = 2.0

    # 动量确认
    momentum_period: int = 20
    momentum_threshold: float = -0.05  # -5%

    # 量能确认
    volume_ratio_threshold: float = 1.5  # 成交量放大 1.5 倍

    # 置信度权重
    rsi_weight: float = 0.3
    bollinger_weight: float = 0.3
    momentum_weight: float = 0.2
    volume_weight: float = 0.2


def score_mean_reversion(
    ti: TechInd,
    *,
    code: str = "",
    ctx: dict | None = None,
    config: MeanReversionConfig | None = None,
) -> list[Signal]:
    """均值回归策略评分

    识别超卖反弹机会：
    1. RSI 超卖（<30）
    2. 价格触及布林带下轨
    3. 动量负向（下跌趋势）
    4. 成交量放大（恐慌抛售后）

    适合震荡市场和超跌反弹行情

    Args:
        ti: 技术指标对象
        code: 股票代码
        ctx: 市场上下文
        config: 策略配置

    Returns:
        信号列表
    """
    if config is None:
        config = MeanReversionConfig()

    signals: list[Signal] = []

    # 1. RSI 超卖检查
    rsi_series = ti.rsi(config.momentum_period)
    rsi_score = 0
    if rsi_series is not None and len(rsi_series) > 0:
        rsi = float(rsi_series.iloc[-1])
        if np.isnan(rsi):
            pass
        elif rsi < config.rsi_oversold:
            rsi_score = min(int((config.rsi_oversold - rsi) / config.rsi_oversold * 10), 5)
            signals.append(
                Signal(
                    name=f"mean_rev_rsi_oversold_{int(rsi)}",
                    label=f"RSI超卖({rsi:.1f})",
                    score=int(round(rsi_score * config.rsi_weight)),
                    category="reversal",
                )
            )
        elif rsi > config.rsi_overbought:
            rsi_score = -min(
                int((rsi - config.rsi_overbought) / (100 - config.rsi_overbought) * 10),
                5,
            )
            signals.append(
                Signal(
                    name=f"mean_rev_rsi_overbought_{int(rsi)}",
                    label=f"RSI超买({rsi:.1f})",
                    score=int(round(rsi_score * config.rsi_weight)),
                    category="reversal",
                )
            )

    # 2. 布林带检查
    upper, mid, lower = ti.bollinger()
    if upper is not None and len(upper) > 0:
        close_data = ti._close
        if close_data is not None and len(close_data) > 0:
            current_price = close_data.iloc[-1]
            lower_band = lower.iloc[-1]
            upper_band = upper.iloc[-1]

            # 价格接近下轨（超卖）
            if current_price <= lower_band * 1.02:  # 允许2%容差
                signals.append(
                    Signal(
                        name="mean_rev_bollinger_lower",
                        label="触及布林带下轨(超卖)",
                        score=int(round(4 * config.bollinger_weight)),
                        category="reversal",
                    )
                )
            # 价格接近上轨（超买）
            elif current_price >= upper_band * 0.98:
                signals.append(
                    Signal(
                        name="mean_rev_bollinger_upper",
                        label="触及布林带上轨(超买)",
                        score=int(round(-4 * config.bollinger_weight)),
                        category="reversal",
                    )
                )

    # 3. 动量确认（负向动量后反弹）
    roc20 = ti.roc_signal(20)
    if roc20 is not None and not np.isnan(roc20):
        if roc20 < config.momentum_threshold * 100:  # 转换为百分比
            # 负向动量，可能是超卖
            signals.append(
                Signal(
                    name=f"mean_rev_momentum_negative_{int(abs(roc20))}",
                    label=f"负向动量({roc20:+.1f}%)",
                    score=int(round(2 * config.momentum_weight)),
                    category="reversal",
                )
            )
        elif roc20 > -config.momentum_threshold * 100:
            # 正向动量，可能超买
            signals.append(
                Signal(
                    name=f"mean_rev_momentum_positive_{int(roc20)}",
                    label=f"正向动量({roc20:+.1f}%)",
                    score=int(round(-2 * config.momentum_weight)),
                    category="reversal",
                )
            )

    # 4. 成交量确认（恐慌抛售后反弹）
    volume_data = getattr(ti, "volume", None)
    if volume_data is not None:
        volume = volume_data
        if len(volume) >= 20:
            vol_ma = volume.rolling(window=20).mean()
            current_vol = volume.iloc[-1]
            avg_vol = vol_ma.iloc[-1]

            if avg_vol > 0:
                vol_ratio = current_vol / avg_vol
                if vol_ratio > config.volume_ratio_threshold:
                    signals.append(
                        Signal(
                            name=f"mean_rev_volume_spike_{vol_ratio:.1f}",
                            label=f"成交量放大({vol_ratio:.1f}x)",
                            score=int(round(3 * config.volume_weight)),
                            category="reversal",
                        )
                    )

    # 5. 综合评分
    if len(signals) >= 2:
        # 至少两个信号确认才有效
        total_score = sum(s.score for s in signals)
        if total_score > 0:
            logger.info(
                "Mean reversion signal for %s: %d signals, total score %d",
                code,
                len(signals),
                total_score,
            )

    return signals


def create_mean_reversion_strategy(config: MeanReversionConfig | None = None):
    """创建均值回归策略函数"""

    def strategy(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> list[Signal]:
        return score_mean_reversion(ti, code=code, ctx=ctx, config=config)

    return strategy


# 默认策略实例
mean_reversion_strategy = create_mean_reversion_strategy()
