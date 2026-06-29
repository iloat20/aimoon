"""机构研报数据实体。

ResearchReportData 是一个实体，以股票代码 symbol 作为唯一标识。
每只股票的机构研报集合通过 symbol 进行区分。
ResearchReport 没有独立标识，从属于 ResearchReportData 聚合。
"""

from pydantic import BaseModel, Field, model_validator


class ResearchReport(BaseModel):
    """单篇机构研究报告。"""

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
    """机构研究报告集合。"""

    symbol: str = ""
    reports: list[ResearchReport] = Field(default_factory=list)
    source: str = ""
    total_count: int = 0
    buy_count: int = 0
    hold_count: int = 0
    neutral_count: int = 0
    avg_eps_this_yr: float = 0.0
    avg_pe_this_yr: float = 0.0

    @model_validator(mode="after")
    def _sync_counts_with_reports(self) -> "ResearchReportData":
        actual_total = len(self.reports)
        actual_buy = actual_hold = actual_neutral = 0
        for r in self.reports:
            rating = r.rating
            if "买入" in rating or "推荐" in rating:
                actual_buy += 1
            elif "增持" in rating:
                actual_hold += 1
            elif "中性" in rating or "持有" in rating:
                actual_neutral += 1

        all_zero = (
            self.total_count == 0
            and self.buy_count == 0
            and self.hold_count == 0
            and self.neutral_count == 0
        )
        if all_zero:
            self.total_count = actual_total
            self.buy_count = actual_buy
            self.hold_count = actual_hold
            self.neutral_count = actual_neutral
        else:
            if self.total_count != actual_total:
                import logging
                logging.getLogger(__name__).warning(
                    "total_count (%d) 与 reports 列表长度 (%d) 不一致，使用实际值",
                    self.total_count,
                    actual_total,
                )
                self.total_count = actual_total
                self.buy_count = actual_buy
                self.hold_count = actual_hold
                self.neutral_count = actual_neutral

        return self
