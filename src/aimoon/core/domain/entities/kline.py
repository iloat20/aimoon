"""K线数据实体。

KlineData 是一个实体，以股票代码 symbol 作为唯一标识。
每只股票的K线序列数据通过 symbol 进行区分。
"""

from pydantic import BaseModel, Field

from aimoon.core.domain.value_objects.kline_bar import KlineBar


class KlineData(BaseModel):
    """历史K线序列数据，用于图表展示。"""

    symbol: str = ""
    bars: list[KlineBar] = Field(default_factory=list)
    source: str = ""
    period: str = "daily"

    @property
    def summary(self) -> dict[str, object]:
        """从K线数据中提取汇总统计信息。

        注意：latest_close 和 latest_date 取自 bars 序列的最后一根K线，
        如果是当日数据，该K线可能是未完成的（仍在交易中）。
        """
        if not self.bars:
            return {}
        bars = self.bars
        latest = bars[-1]
        highs = [b.high for b in bars if b.high > 0]
        lows = [b.low for b in bars if b.low > 0]
        return {
            "latest_close": latest.close,
            "latest_date": latest.date,
            "period_high": max(highs) if highs else 0,
            "period_low": min(lows) if lows else 0,
            "bar_count": len(bars),
            "source": self.source,
        }
