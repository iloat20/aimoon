"""股票分析聚合根。

StockAnalysis 是股票分析聚合的根，负责维护单只股票
所有分析数据的一致性边界。外部只能通过聚合根访问内部实体，
不能直接修改内部状态。
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, model_validator

from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.financial import FinancialData, QuarterlyFinancialData
from aimoon.core.domain.entities.kline import KlineData
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.entities.research import ResearchReportData
from aimoon.core.domain.entities.social import SocialPost
from aimoon.core.domain.services.symbols import resolve_market
from aimoon.core.domain.value_objects.financial_report import FinancialReportData


class StockAnalysis(BaseModel):
    """聚合的股票分析信息（AI分析器和报告生成器的输入）。"""

    symbol: str
    name: str = ""
    market: str = ""
    quote: StockQuote = Field(default_factory=StockQuote)
    financial: FinancialData = Field(default_factory=FinancialData)
    quarterly_financial: QuarterlyFinancialData = Field(default_factory=QuarterlyFinancialData)
    kline: KlineData = Field(default_factory=KlineData)
    capital_flow: CapitalFlowData = Field(default_factory=CapitalFlowData)
    social_posts: list[SocialPost] = Field(default_factory=list)
    research: ResearchReportData = Field(default_factory=ResearchReportData)
    annual_report: FinancialReportData = Field(default_factory=FinancialReportData)
    semi_annual_report: FinancialReportData = Field(default_factory=FinancialReportData)
    quarterly_report: FinancialReportData = Field(default_factory=FinancialReportData)
    history_financial: list[FinancialData] = Field(default_factory=list)  # 近 3 年报
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)  # noqa: UP017
    )

    @model_validator(mode="before")
    @classmethod
    def _resolve_market_from_symbol(cls, data: object) -> object:
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
            market = data.get("market", "")
            if isinstance(symbol, str) and symbol and not market:
                data["market"] = resolve_market(symbol)
        return data
