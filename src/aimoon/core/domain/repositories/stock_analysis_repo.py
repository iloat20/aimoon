"""股票分析资源库接口 — 领域层端口。

定义数据获取的契约，由基础设施层实现。
负责获取单只股票的所有分析数据，返回 StockAnalysis 聚合根。
"""

from abc import ABC, abstractmethod

from ..aggregates.stock_analysis import StockAnalysis
from ..value_objects.collect_result import CollectResult


class StockAnalysisRepository(ABC):
    """股票分析资源库接口。

    统一封装所有数据采集能力，返回完整的 StockAnalysis 聚合。
    适配器层（collectors）实现此接口。

    设计决策：
    - 所有采集方法均为 async：采集器均为 IO 密集型（HTTP 请求、浏览器自动化），
      统一使用 async 便于并发调度和事件循环集成。
    """

    @abstractmethod
    async def collect_all(self, symbol: str, name: str = "") -> StockAnalysis:
        """采集指定股票的所有维度数据，返回完整聚合。

        Args:
            symbol: 6位股票代码
            name: 股票名称，可选

        Returns:
            StockAnalysis 聚合根实例
        """

    # TODO(tech-debt): get_collect_results 属于采集过程状态，不属于资源库职责。
    # 应考虑将采集结果作为 collect_all 返回值的一部分，或移到应用层端口。
    @abstractmethod
    async def get_collect_results(self) -> list[CollectResult]:
        """获取各数据源的采集结果详情。

        Returns:
            各平台采集结果列表
        """

    async def close(self) -> None:
        """释放底层资源（浏览器、连接池等）。

        默认空操作；实现类（如 CompositeStockAnalysisRepository）应在
        **同一事件循环内**关闭 Playwright 浏览器等资源，避免进程退出时
        asyncio transport 收尾噪声（Windows ProactorEventLoop 的已知坑）。
        """
        return None
