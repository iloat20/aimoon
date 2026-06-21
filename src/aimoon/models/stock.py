"""Data models for stock information."""

import sys
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .social import SocialPost


class StockQuote(BaseModel):
    """Real-time stock quote."""

    symbol: str = ""
    name: str = ""
    price: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    amount: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    prev_close: float = 0.0
    turnover: float = 0.0
    pe: float = 0.0
    source: str = ""
    updated_at: str = ""


class FinancialData(BaseModel):
    """Company financial statement data."""

    symbol: str = ""
    report_period: str = ""

    # 利润表
    revenue: float = 0.0
    revenue_yoy: float = 0.0
    net_profit: float = 0.0
    net_profit_yoy: float = 0.0

    # 资产负债表
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    equity: float = 0.0

    # 现金流表
    operating_cf: float = 0.0
    investing_cf: float = 0.0
    financing_cf: float = 0.0

    # 核心指标
    roe: float = 0.0
    eps: float = 0.0
    bvps: float = 0.0

    source: str = ""


class StockInfo(BaseModel):
    """Aggregated stock information (input to AI analyzer)."""

    symbol: str
    name: str = ""
    market: str = ""  # SH / SZ / BJ
    quote: StockQuote = Field(default_factory=StockQuote)
    financial: FinancialData = Field(default_factory=FinancialData)
    social_posts: list[Any] = Field(default_factory=list)
    collected_at: str = Field(default_factory=lambda: datetime.now().isoformat())
