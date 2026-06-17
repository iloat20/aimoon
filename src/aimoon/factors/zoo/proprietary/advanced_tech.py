"""Proprietary Factor: Advanced Technical Alpha

基于高级技术指标的私有因子，包括：
1. 分形维度指标
2. 熵指标
3. 自相关指标
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.factors.base import rank


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """计算高级技术因子。

    基于分形维度、熵、自相关等高级技术指标。
    """
    close = panel.get("close")
    panel.get("volume")
    panel.get("high")
    panel.get("low")

    if close is None:
        return pd.DataFrame()

    # 因子 1: 分形维度指标 (Fractal Dimension)
    # 基于价格序列的分形维度，反映市场复杂度
    def fractal_dimension(series, window=20):
        """计算分形维度（简化版）。"""
        if len(series) < window:
            return pd.Series(np.nan, index=series.index)

        fd_values = []
        for i in range(window, len(series)):
            segment = series.iloc[i - window : i]
            # 使用价格范围和标准差的比率作为分形维度的代理
            price_range = segment.max() - segment.min()
            price_std = segment.std()
            if price_std > 0:
                fd = price_range / price_std
            else:
                fd = 1.0
            fd_values.append(fd)

        # 填充前面的 NaN
        result = pd.Series(np.nan, index=series.index)
        result.iloc[window:] = fd_values
        return result

    fractal = fractal_dimension(close)

    # 因子 2: 熵指标 (Entropy)
    # 基于价格变化的熵，反映市场不确定性
    def entropy(series, window=20):
        """计算价格变化的熵。"""
        if len(series) < window:
            return pd.Series(np.nan, index=series.index)

        entropy_values = []
        for i in range(window, len(series)):
            segment = series.iloc[i - window : i]
            # 计算价格变化的方向分布
            changes = segment.diff().dropna()
            if len(changes) < 10:
                entropy_values.append(0.0)
                continue

            # 计算正向和负向变化的比例
            positive = (changes > 0).sum() / len(changes)
            negative = (changes < 0).sum() / len(changes)
            neutral = (changes == 0).sum() / len(changes)

            # 计算熵
            probs = [positive, negative, neutral]
            probs = [p for p in probs if p > 0]
            entropy_val = -sum(p * np.log2(p) for p in probs)
            entropy_values.append(entropy_val)

        result = pd.Series(np.nan, index=series.index)
        result.iloc[window:] = entropy_values
        return result

    entropy_signal = entropy(close)

    # 因子 3: 自相关指标 (Autocorrelation)
    # 基于价格变化的自相关，反映趋势持续性
    def autocorrelation(series, window=20, lag=5):
        """计算价格变化的自相关。"""
        if len(series) < window:
            return pd.Series(np.nan, index=series.index)

        autocorr_values = []
        for i in range(window, len(series)):
            segment = series.iloc[i - window : i]
            changes = segment.diff().dropna()
            if len(changes) < lag + 5:
                autocorr_values.append(0.0)
                continue

            # 计算 lag 阶自相关
            autocorr = changes.autocorr(lag=lag)
            autocorr_values.append(autocorr if not np.isnan(autocorr) else 0.0)

        result = pd.Series(np.nan, index=series.index)
        result.iloc[window:] = autocorr_values
        return result

    autocorr_signal = autocorrelation(close)

    # 因子 4: 波动率聚类 (Volatility Clustering)
    # 基于 GARCH 效应的波动率聚类指标
    returns = close.pct_change(fill_method=None)
    volatility = returns.rolling(window=20).std()
    volatility_ma = volatility.rolling(window=60).mean()
    volatility_clustering = volatility / volatility_ma

    # 因子 5: 价格动量加速 (Momentum Acceleration)
    # 基于动量变化的加速度指标
    momentum_5d = close / close.shift(5) - 1
    momentum_10d = close / close.shift(10) - 1
    momentum_acceleration = momentum_5d - momentum_10d / 2  # 加速度指标

    # 组合因子
    factors = pd.DataFrame(
        {
            "fractal": fractal.iloc[-1] if len(fractal) > 0 else pd.Series(dtype=float),
            "entropy": (
                entropy_signal.iloc[-1] if len(entropy_signal) > 0 else pd.Series(dtype=float)
            ),
            "autocorr": (
                autocorr_signal.iloc[-1] if len(autocorr_signal) > 0 else pd.Series(dtype=float)
            ),
            "vol_clustering": (
                volatility_clustering.iloc[-1]
                if len(volatility_clustering) > 0
                else pd.Series(dtype=float)
            ),
            "momentum_accel": (
                momentum_acceleration.iloc[-1]
                if len(momentum_acceleration) > 0
                else pd.Series(dtype=float)
            ),
        }
    )

    # 对每个因子进行截面排名
    for col in factors.columns:
        factors[col] = rank(factors[col].to_frame().T).iloc[0]

    return factors


# 注册因子元数据
__alpha_meta__ = {
    "id": "proprietary_advanced_tech",
    "nickname": "高级技术",
    "theme": ["fractal", "entropy", "autocorrelation", "volatility", "momentum"],
    "columns_required": ["close"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["A股"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 60,
    "notes": "基于高级技术指标的私有因子，包括分形维度、熵指标、自相关指标、波动率聚类、动量加速度",
}
