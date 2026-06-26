"""Financial statement and report metadata models."""

from pydantic import BaseModel


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


class FinancialReportData(BaseModel):
    """Financial report metadata (annual/semi-annual/quarterly)."""

    year: str = ""
    title: str = ""
    pdf_url: str = ""
