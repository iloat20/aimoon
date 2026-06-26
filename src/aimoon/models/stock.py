"""Stock aggregate model — assembles all data dimensions for analysis.

Re-exports individual models for backward compatibility.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from .capital_flow import CapitalFlowData
from .financial import FinancialData, FinancialReportData
from .kline import KlineBar, KlineData
from .quote import StockQuote
from .research import ResearchReport, ResearchReportData
from .social import SocialPost

# Re-export for backward compatibility
__all__ = [
    "StockQuote",
    "FinancialData",
    "KlineBar",
    "KlineData",
    "CapitalFlowData",
    "ResearchReport",
    "ResearchReportData",
    "FinancialReportData",
    "StockInfo",
]


class StockInfo(BaseModel):
    """Aggregated stock information (input to AI analyzer and report generator)."""

    symbol: str
    name: str = ""
    market: str = ""  # SH / SZ / BJ
    quote: StockQuote = Field(default_factory=StockQuote)
    financial: FinancialData = Field(default_factory=FinancialData)
    kline: KlineData = Field(default_factory=KlineData)
    capital_flow: CapitalFlowData = Field(default_factory=CapitalFlowData)
    social_posts: list[SocialPost] = Field(default_factory=list)
    research: ResearchReportData = Field(default_factory=ResearchReportData)
    annual_report: FinancialReportData = Field(default_factory=FinancialReportData)
    semi_annual_report: FinancialReportData = Field(
        default_factory=FinancialReportData
    )
    quarterly_report: FinancialReportData = Field(
        default_factory=FinancialReportData
    )
    collected_at: str = Field(default_factory=lambda: datetime.now().isoformat())
