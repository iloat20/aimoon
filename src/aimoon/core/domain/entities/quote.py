"""股票行情实体。

StockQuote 是一个实体，以股票代码 symbol 作为唯一标识。
每只股票在同一时刻的行情数据是唯一的，通过 symbol 进行区分。
"""

from pydantic import BaseModel


class StockQuote(BaseModel):
    """实时股票行情数据。"""

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
    pb: float = 0.0
    market_cap: float = 0.0
    source: str = ""
    updated_at: str = ""
