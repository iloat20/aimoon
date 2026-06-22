"""Cross-source data validator for multi-source consistency checking."""

from __future__ import annotations

from typing import Any

from ..models.stock import StockQuote


class CrossValidator:
    """Cross-validate data from multiple sources to detect anomalies."""

    @staticmethod
    def validate_quote_sources(*quotes: StockQuote) -> dict[str, Any]:
        """Compare quotes from multiple sources.

        Returns dict with:
        - consensus: bool (whether sources agree within tolerance)
        - price_range: (min, max)
        - price_diff_pct: max percentage difference
        - warnings: list of warning strings
        - source_count: number of valid sources
        """
        valid = [q for q in quotes if q.price > 0]
        result: dict[str, Any] = {
            "consensus": True,
            "price_range": (0.0, 0.0),
            "price_diff_pct": 0.0,
            "warnings": [],
            "source_count": len(valid),
        }

        if len(valid) < 2:
            result["warnings"].append(f"仅有{len(valid)}个有效数据源，无法交叉验证")
            return result

        prices = [q.price for q in valid]
        min_p, max_p = min(prices), max(prices)
        result["price_range"] = (min_p, max_p)

        avg = sum(prices) / len(prices)
        max_diff = max(abs(p - avg) for p in prices)
        pct_diff = (max_diff / avg * 100) if avg else 0
        result["price_diff_pct"] = round(pct_diff, 2)

        if pct_diff > 5:
            result["consensus"] = False
            result["warnings"].append(
                f"跨源价格差异过大({pct_diff:.1f}%)，可能存在数据延迟"
            )

        # Check individual sources
        for q in valid:
            diff = abs(q.price - avg) / avg * 100 if avg else 0
            if diff > 3:
                result["warnings"].append(
                    f"{q.source}价格偏离均值{diff:.1f}%（可能延迟）"
                )

        return result

    @staticmethod
    def pick_best_quote(*quotes: StockQuote) -> StockQuote:
        """Pick the most reliable quote from multiple sources.

        Priority: Has PE data > Has turnover > Most recent > Highest price
        """
        valid = [q for q in quotes if q.price > 0]
        if not valid:
            return StockQuote()

        if len(valid) == 1:
            return valid[0]

        # Score each quote
        def score(q: StockQuote) -> float:
            s = 0.0
            if q.pe and q.pe > 0:
                s += 10
            if q.turnover and q.turnover > 0:
                s += 5
            if q.volume > 0:
                s += 3
            return s

        return max(valid, key=score)
