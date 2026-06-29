"""财务报告元数据值对象。

FinancialReportData 是一个值对象，概念上不可变。
它描述年报/半年报/季报的元数据信息，没有独立的唯一标识，
作为 FinancialData 实体或 StockAnalysis 聚合根的组成部分存在。
"""

from pydantic import BaseModel


class FinancialReportData(BaseModel):
    """财务报告元数据（年报/半年报/季报）。"""

    model_config = {"frozen": True}

    year: str = ""
    title: str = ""
    pdf_url: str = ""
    content: str = ""
