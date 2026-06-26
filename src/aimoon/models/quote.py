"""Real-time stock quote model."""

from pydantic import BaseModel


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
