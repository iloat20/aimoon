"""Tests for core data models — 100-point weighted scoring"""
from aimoon.models import ScoredStock, Signal
from aimoon.scoring import hybrid_score
from aimoon.scoring.hybrid_scorer import get_suggestion


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
    """ScoredStock is a frozen dataclass; total_score is externally injected."""

    def _stock(self, signals=(), total_score=0) -> ScoredStock:
        return ScoredStock(
            code="000001", name="Test", price=10.0,
            pct_change=1.0, turnover=5.0,
            signals=tuple(signals), total_score=total_score,
        )

    def test_total_score_is_injected_not_auto_computed(self) -> None:
        """total_score defaults to 0 unless explicitly set by scoring layer."""
        s = self._stock()
        assert s.total_score == 0

    def test_total_score_matches_hybrid_score(self) -> None:
        """total_score equals hybrid_score(signals) when properly injected."""
        sigs = [
            Signal("roc5_strong", "ROC5强势", 4, category="momentum"),
            Signal("ma_align_bull", "均线多头", 3, category="reversal"),
        ]
        expected = hybrid_score(sigs)
        s = self._stock(sigs, total_score=expected)
        assert s.total_score == expected
        assert 0 <= s.total_score <= 100

    def test_suggestion_is_get_suggestion_function(self) -> None:
        """suggestion is derived via get_suggestion(total_score)."""
        sug, conf = get_suggestion(75)
        assert sug in ("强烈买入", "买入", "建议买入", "观望", "谨慎", "建议卖出", "强烈卖出")
        assert conf in ("高", "中高", "中", "低")

    def test_hybrid_score_with_empty_signals(self) -> None:
        """hybrid_score([]) returns 50 (neutral)."""
        assert hybrid_score([]) == 50
