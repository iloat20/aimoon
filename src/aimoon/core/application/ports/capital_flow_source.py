"""Port: 资金流数据源。

供 ``CapitalFlowCollector`` 经构造函数注入,从而避免 collectors 层直接 import
具体的财务适配器实现(如 ``AkshareFinancialAdapter``)。具体适配器只需在结构上
提供 ``fetch_capital_flow`` 即可(鸭子类型 / Protocol 结构匹配),无需显式继承。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CapitalFlowSource(Protocol):
    """提供个股资金流数据的端口。"""

    async def fetch_capital_flow(self, symbol: str, **kwargs: object) -> dict:
        """返回个股资金流字典(主力 N 日净流入等)。"""
        ...
