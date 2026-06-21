"""Data models for the analysis report."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

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
    sentiment: DimensionScore = Field(default_factory=DimensionScore)
    technical: DimensionScore = Field(default_factory=DimensionScore)
    fundamental: DimensionScore = Field(default_factory=DimensionScore)
    capital_flow: DimensionScore = Field(default_factory=DimensionScore)
    news: DimensionScore = Field(default_factory=DimensionScore)
    overall_rating: int = 0  # 1-5

    # Detailed analysis
    sentiment_detail: str = ""
    technical_detail: str = ""
    fundamental_detail: str = ""
    capital_flow_detail: str = ""
    news_detail: str = ""

    # Key metrics
    bullish_ratio: float = 0.0
    trend: str = ""
    support_price: float = 0.0
    resistance_price: float = 0.0
    main_force: str = ""
    news_sentiment: str = ""

    # Issues
    key_topics: list[str] = Field(default_factory=list)
    key_events: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)
    investment_advice: str = ""

    # Source traceability
    data_sources: list[str] = Field(default_factory=list)

    # Full AI markdown report (for rendered display)
    report_text: str = ""
