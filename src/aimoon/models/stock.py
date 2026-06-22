"""Data models for stock information."""

from datetime import datetime

from pydantic import BaseModel, Field

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


class KlineBar(BaseModel):
    """A single K-line (OHLCV) bar."""

    date: str
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    turnover: float = 0.0
    pct_change: float = 0.0


class KlineData(BaseModel):
    """Historical K-line series for technical analysis."""

    symbol: str = ""
    bars: list[KlineBar] = Field(default_factory=list)
    source: str = ""
    period: str = "daily"


class CapitalFlowData(BaseModel):
    """Market capital flow (主力资金/北向/龙虎榜)."""

    symbol: str = ""

    # 主力资金（近5日累计 + 今日）
    main_net_5d: float = 0.0  # 近5日主力净流入（元）
    main_net_today: float = 0.0  # 今日主力净流入（元）

    # 分单净流入（今日，元）
    super_large_net: float = 0.0  # 超大单
    large_net: float = 0.0  # 大单
    medium_net: float = 0.0  # 中单
    small_net: float = 0.0  # 小单

    # 北向资金
    northbound_chg: float = 0.0  # 北向持股变化（元）

    # 龙虎榜（最近一次上榜，可空）
    lhb_date: str = ""
    lhb_reason: str = ""
    lhb_net_buy: float = 0.0  # 净买入（元）

    source: str = ""


class ResearchReport(BaseModel):
    """A single institutional research report."""

    title: str = ""
    institution: str = ""
    rating: str = ""
    industry: str = ""
    date: str = ""
    pdf_url: str = ""
    eps_this_yr: float = 0.0
    pe_this_yr: float = 0.0
    eps_next_yr: float = 0.0
    pe_next_yr: float = 0.0
    eps_future_yr: float = 0.0
    pe_future_yr: float = 0.0


class ResearchReportData(BaseModel):
    """Collection of institutional research reports."""

    symbol: str = ""
    reports: list[ResearchReport] = Field(default_factory=list)
    source: str = ""
    total_count: int = 0
    buy_count: int = 0
    hold_count: int = 0
    neutral_count: int = 0
    avg_eps_this_yr: float = 0.0
    avg_pe_this_yr: float = 0.0


class StockInfo(BaseModel):
    """Aggregated stock information (input to AI analyzer)."""

    symbol: str
    name: str = ""
    market: str = ""  # SH / SZ / BJ
    quote: StockQuote = Field(default_factory=StockQuote)
    financial: FinancialData = Field(default_factory=FinancialData)
    kline: KlineData = Field(default_factory=KlineData)
    capital_flow: CapitalFlowData = Field(default_factory=CapitalFlowData)
    social_posts: list[SocialPost] = Field(default_factory=list)
    research: ResearchReportData = Field(default_factory=ResearchReportData)
    collected_at: str = Field(default_factory=lambda: datetime.now().isoformat())
