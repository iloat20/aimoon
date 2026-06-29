"""Tests for collectors."""


class TestStockQuoteModel:
    """Test StockQuote domain model construction."""

    def test_parse_quote_data(self):
        from aimoon.core.domain.entities.quote import StockQuote

        quote = StockQuote(
            symbol="600519",
            name="贵州茅台",
            price=1800.0,
            change=10.0,
            change_pct=0.56,
            source="雪球",
        )
        assert quote.symbol == "600519"
        assert quote.source == "雪球"
