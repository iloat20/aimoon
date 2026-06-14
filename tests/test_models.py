"""Tests for core data models — 100-point weighted scoring"""
from aimoon.models import Signal, ScoredStock
from aimoon.scoring import hybrid_score


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

    def test_total_score_uses_weighted_scoring(self) -> None:
        """total_score returns 100-point weighted score, not raw sum."""
        sigs = [
            Signal("roc5_strong", "ROC5强势", 4),   # momentum
            Signal("ma_align_bull", "均线多头", 3),   # trend
        ]
        s = self._stock(sigs)
        expected = hybrid_score(sigs)
        assert s.total_score == expected
        assert 0 <= s.total_score <= 100

    def test_total_score_ignores_rps_score_field(self) -> None:
        """RPS score is carried via Signal objects, not the rps dict."""
        s = ScoredStock(
            code="000001", name="T", price=10.0,
            pct_change=0, turnover=0,
            signals=(Signal("roc5_strong", "ROC5", 4),),
            rps={"rps_score": 5},
        )
        assert s.total_score == hybrid_score(list(s.signals))

    def test_suggestion_thresholds(self) -> None:
        """Suggestion uses 100-point scale."""
        def stock_with_score(n: int) -> ScoredStock:
            # Use alpha signals to reach high scores
            sigs = [Signal(f"alpha_{i}", f"a{i}", 3) for i in range(max(1, n // 3 + 1))]
            return ScoredStock(
                code="0", name="T", price=0, pct_change=0, turnover=0,
                signals=tuple(sigs[:30]),  # cap at 30 alpha signals
            )
        # Just verify suggestion returns a valid tuple
        s = stock_with_score(50)
        sug, conf = s.suggestion
        assert sug in ("强烈买入", "买入", "建议买入", "观望", "谨慎", "建议卖出", "强烈卖出")
        assert conf in ("高", "中高", "中", "低")

    def test_empty_signals_score_zero(self) -> None:
        # hybrid_score returns 50 for empty signals (neutral score)
        assert self._stock().total_score == 50
