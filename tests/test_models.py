"""Tests for core data models"""
from aimoon.models import Signal, ScoredStock


class TestSignal:
    def test_frozen(self) -> None:
        s = Signal("rsi_strong", "RSI强势", 2)
        assert s.name == "rsi_strong"
        assert s.score == 2

    def test_immutable(self) -> None:
        s = Signal("test", "test", 0)
        try:
            s.score = 1  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestScoredStock:
    def _stock(self, signals=()) -> ScoredStock:
        return ScoredStock(
            code="000001", name="Test", price=10.0,
            pct_change=1.0, turnover=5.0, signals=tuple(signals),
        )

    def test_total_score_sums_signals(self) -> None:
        s = self._stock([Signal("a", "A", 3), Signal("b", "B", -1)])
        assert s.total_score == 2

    def test_total_score_includes_rps(self) -> None:
        s = ScoredStock(
            code="000001", name="T", price=10.0,
            pct_change=0, turnover=0,
            signals=(Signal("a", "A", 2),),
            rps={"rps_score": 5},
        )
        assert s.total_score == 7

    def test_suggestion_thresholds(self) -> None:
        def stock_with_score(n: int) -> ScoredStock:
            return ScoredStock(
                code="0", name="T", price=0, pct_change=0, turnover=0,
                signals=(Signal("x", "X", n),),
            )
        assert stock_with_score(10).suggestion == ("强烈买入", "高")
        assert stock_with_score(6).suggestion == ("买入", "中高")
        assert stock_with_score(3).suggestion == ("建议买入", "中")
        assert stock_with_score(0).suggestion == ("观望", "低")
        assert stock_with_score(-2).suggestion == ("谨慎", "中")
        assert stock_with_score(-5).suggestion == ("建议卖出", "中高")
        assert stock_with_score(-8).suggestion == ("强烈卖出", "高")

    def test_empty_signals_score_zero(self) -> None:
        assert self._stock().total_score == 0
