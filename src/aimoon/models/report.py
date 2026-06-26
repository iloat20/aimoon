"""Data models for the analysis report."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DimensionScore(BaseModel):
    """A single analysis dimension with score and explanation."""

    name: str = ""
    score: int = 0  # 1-5
    max_score: int = 5
    weight: float = 0.0
    analysis: str = ""


class AnalysisReport(BaseModel):
    """Complete AI analysis report."""

    symbol: str = ""
    name: str = ""
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    # Summary
    summary: str = ""

    # Dimension scores (1-5)
    fundamental: DimensionScore = Field(default_factory=DimensionScore)
    capital_flow: DimensionScore = Field(default_factory=DimensionScore)
    news: DimensionScore = Field(default_factory=DimensionScore)

    # Detailed analysis
    fundamental_detail: str = ""
    capital_flow_detail: str = ""
    news_detail: str = ""

    # Key metrics
    main_force: str = ""

    # Data quality
    data_warnings: list[str] = Field(default_factory=list)
    data_confidence: dict[str, str] = Field(
        default_factory=dict
    )  # dimension → 高/中/低

    investment_advice: str = ""

    # Full AI markdown report (for rendered display)
    report_text: str = ""
