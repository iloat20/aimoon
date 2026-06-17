"""Proprietary Factors — 私有因子库。

包含基于市场微观结构、另类数据、高级技术指标的私有因子。
"""

from __future__ import annotations

# 导入私有因子模块
from aimoon.factors.zoo.proprietary import (
    advanced_tech,
    alternative,
    microstructure,
    northbound,
    sector_rotation,
)

__all__ = [
    "microstructure",
    "alternative",
    "advanced_tech",
    "northbound",
    "sector_rotation",
]
