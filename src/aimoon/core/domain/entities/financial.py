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

    # 资产负债表扩展字段(渠道压货/库存减值/分红可持续性诊断)
    accounts_receivable: float = 0.0  # 应收账款(元)
    inventory: float = 0.0  # 存货(元)
    dividend_paid: float = 0.0  # 分配股利、利润或偿付利息支付的现金(元)

    # 资产负债表/现金流扩展字段(确定性采集,消 8.1 缺失清单 A/B 组)
    monetary_funds: float = 0.0  # 货币资金(元) — 短期分红能力/真实财务弹性
    construction_in_progress: float = 0.0  # 在建工程(元) — 战略性资本开支信号
    capex: float = 0.0  # 购建固定资产/无形资产/长期资产支付的现金(元) — 真实资本开支(区别于理财)

    # 分业务营收(按产品分类,最新年报)— 确定性采集,消 8.1 缺失清单 #3
    segment_revenue: list[dict] = []  # 每项: {name, revenue_yi, ratio, gross_margin}

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
