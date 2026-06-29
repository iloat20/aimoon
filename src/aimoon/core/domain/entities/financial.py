"""财务数据实体。

FinancialData 是一个实体，以股票代码 symbol 作为唯一标识。
每只股票的财务报表数据通过 symbol 进行区分。
"""

from __future__ import annotations

from pydantic import BaseModel


class FinancialData(BaseModel):
    """公司财务报表数据。"""

    symbol: str = ""
    report_period: str = ""

    revenue: float = 0.0
    revenue_yoy: float = 0.0
    net_profit: float = 0.0
    net_profit_yoy: float = 0.0

    total_assets: float = 0.0
    total_liabilities: float = 0.0
    equity: float = 0.0

    operating_cf: float = 0.0
    investing_cf: float = 0.0
    financing_cf: float = 0.0

    roe: float = 0.0
    eps: float = 0.0
    bvps: float = 0.0

    source: str = ""


class QuarterlyFinancialData(BaseModel):
    """季度/中期财务数据 — 用于补充最近一期季报/中报数据。"""

    symbol: str = ""
    report_period: str = ""
    report_type: str = ""  # "一季报" / "中报" / "三季报"

    revenue: float = 0.0
    revenue_yoy: float = 0.0
    net_profit: float = 0.0
    net_profit_yoy: float = 0.0

    operating_cf: float = 0.0

    source: str = ""
