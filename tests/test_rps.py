"""Tests for RPS calculation"""
import numpy as np
import pandas as pd
from aimoon.models import Signal, ScoredStock
from aimoon.scoring.rps import compute_rps


def _tail(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": prices})


class TestComputeRps:
    def test_empty_results(self) -> None:
        assert compute_rps([], {}) == []

    def test_strong_stock_gets_high_rps(self) -> None:
        strong = ScoredStock(code="A", name="A", price=20, pct_change=0, turnover=5)
        weak = ScoredStock(code="B", name="B", price=10, pct_change=0, turnover=5)
        tails = {
            "A": _tail([10] * 17 + [10, 12, 14, 16, 20]),
            "B": _tail([20] * 22),
        }
        results = compute_rps([strong, weak], tails)
        a = next(r for r in results if r.code == "A")
        assert a.rps.get("rps5", 0) > 90

    def test_short_tail_passed_through(self) -> None:
        s = ScoredStock(code="A", name="A", price=10, pct_change=0, turnover=5)
        results = compute_rps([s], {"A": _tail([10] * 10)})
        assert len(results) == 1
        assert results[0].code == "A"
        assert results[0].signals == ()
