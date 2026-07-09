"""股票分析聚合根。

StockAnalysis 是股票分析聚合的根，负责维护单只股票
所有分析数据的一致性边界。外部只能通过聚合根访问内部实体，
不能直接修改内部状态。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel, Field, model_validator

from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.financial import FinancialData, QuarterlyFinancialData
from aimoon.core.domain.entities.kline import KlineData
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.entities.research import ResearchReportData
from aimoon.core.domain.entities.social import SocialPost
from aimoon.core.domain.services.symbols import resolve_market
from aimoon.core.domain.value_objects.financial_report import FinancialReportData

T = TypeVar("T", bound="BaseModel")


class StockAnalysis(BaseModel):
    """聚合的股票分析信息（AI分析器和报告生成器的输入）。"""

    symbol: str
    name: str = ""
    market: str = ""
    quote: StockQuote | None = None
    financial: FinancialData | None = None
    quarterly_financial: QuarterlyFinancialData | None = None
    kline: KlineData | None = None
    capital_flow: CapitalFlowData | None = None
    social_posts: tuple[SocialPost, ...] = ()
    research: ResearchReportData | None = None
    annual_report: FinancialReportData | None = None
    semi_annual_report: FinancialReportData | None = None
    quarterly_report: FinancialReportData | None = None
    history_financial: list[FinancialData] | None = None
    extensions: dict[str, BaseModel] = Field(default_factory=dict)
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)  # noqa: UP017
    )

    def get_extension(self, key: str, cls: type[T]) -> T | None:
        """Safely retrieve an optional future dimension stored in ``extensions``.

        Returns the stored value only when it is an instance of ``cls``,
        otherwise ``None`` (no exception on missing or mistyped entries).
        """
        raw = self.extensions.get(key)
        if isinstance(raw, cls):
            return raw
        return None

    @model_validator(mode="before")
    @classmethod
    def _resolve_market_from_symbol(cls, data: object) -> object:
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
            market = data.get("market", "")
            if isinstance(symbol, str) and symbol and not market:
                data["market"] = resolve_market(symbol)
        return data
