"""K-line (OHLCV) bar and series models."""

from pydantic import BaseModel, Field


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
    """Historical K-line series for charting."""

    symbol: str = ""
    bars: list[KlineBar] = Field(default_factory=list)
    source: str = ""
    period: str = "daily"
