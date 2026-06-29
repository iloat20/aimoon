"""数据验证器接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis


class DataValidator(ABC):
    """数据验证器端口。

    负责对采集到的数据进行完整性、一致性校验，
    产出数据质量警告和置信度评估。
    """

    @abstractmethod
    def validate(self, stock_info: StockAnalysis) -> tuple[list[str], dict[str, str]]:
        """验证股票数据的完整性和一致性。

        Args:
            stock_info: 待验证的股票信息实体

        Returns:
            (warnings, confidence) - 警告列表和各维度置信度
            warnings: 数据质量警告文本列表
            confidence: 各维度置信度字典（维度名 → "高"/"中"/"低"）
        """
