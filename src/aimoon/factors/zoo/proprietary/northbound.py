"""Proprietary Factor: Northbound Capital Flow

基于北向资金流向的私有因子。
"""

from __future__ import annotations

import pandas as pd

from aimoon.factors.base import rank


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """计算北向资金流向因子。

    基于成交量和价格变化推断北向资金流向。
    """
    close = panel.get("close")
    volume = panel.get("volume")
    panel.get("amount")

    if close is None or volume is None:
        return pd.DataFrame()

    # 因子 1: 资金流入强度 (Capital Inflow Intensity)
    # 基于成交量和价格方向的资金流入强度
    returns = close.pct_change(fill_method=None)
    positive_volume = volume.where(returns > 0, 0)
    negative_volume = volume.where(returns < 0, 0)
    inflow_intensity = (
        positive_volume.rolling(window=20).sum() - negative_volume.rolling(window=20).sum()
    ) / volume.rolling(window=20).sum()

    # 因子 2: 资金动量 (Capital Momentum)
    # 资金流入强度的变化率
    inflow_momentum = inflow_intensity.diff(periods=5)

    # 因子 3: 资金集中度 (Capital Concentration)
    # 资金流入的集中程度（高集中度表示机构资金）
    volume_std = volume.rolling(window=20).std()
    volume_mean = volume.rolling(window=20).mean()
    capital_concentration = volume_std / volume_mean

    # 因子 4: 资金反转 (Capital Reversal)
    # 资金流入后的反转信号
    capital_reversal = -inflow_intensity.shift(5)

    # 因子 5: 资金持续性 (Capital Persistence)
    # 资金流入的持续性
    capital_persistence = inflow_intensity.rolling(window=10).mean()

    # 组合因子
    factors = pd.DataFrame(
        {
            "inflow_intensity": (
                inflow_intensity.iloc[-1] if len(inflow_intensity) > 0 else pd.Series(dtype=float)
            ),
            "inflow_momentum": (
                inflow_momentum.iloc[-1] if len(inflow_momentum) > 0 else pd.Series(dtype=float)
            ),
            "capital_concentration": (
                capital_concentration.iloc[-1]
                if len(capital_concentration) > 0
                else pd.Series(dtype=float)
            ),
            "capital_reversal": (
                capital_reversal.iloc[-1] if len(capital_reversal) > 0 else pd.Series(dtype=float)
            ),
            "capital_persistence": (
                capital_persistence.iloc[-1]
                if len(capital_persistence) > 0
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
    "id": "proprietary_northbound",
    "nickname": "北向资金",
    "theme": ["northbound", "capital_flow", "institutional"],
    "columns_required": ["close", "volume"],
    "extras_required": [],
    "requires_sector": False,
    "universe": ["A股"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "基于北向资金流向的私有因子，包括资金流入强度、资金动量、资金集中度、资金反转、资金持续性",
}
