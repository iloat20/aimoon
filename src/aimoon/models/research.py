"""Institutional research report models."""

from pydantic import BaseModel, Field


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
