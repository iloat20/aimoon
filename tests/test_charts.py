"""Tests for chart generation."""
import os

import pytest


class TestCharts:
    def test_plot_equity_curve_creates_file(self, tmp_path):
        try:
            import matplotlib
        except ImportError:
            pytest.skip("matplotlib not installed")

        from aimoon.charts import plot_equity_curve
        filepath = str(tmp_path / "eq.png")
        result = plot_equity_curve(
            (100.0, 105.0, 103.0, 110.0),
            (100.0, 102.0, 101.0, 108.0),
            "Test", filepath,
        )
        assert os.path.exists(result)
        assert os.path.getsize(result) > 0

    def test_plot_drawdown_creates_file(self, tmp_path):
        try:
            import matplotlib
        except ImportError:
            pytest.skip("matplotlib not installed")

        from aimoon.charts import plot_drawdown
        filepath = str(tmp_path / "dd.png")
        result = plot_drawdown((0.0, 0.02, 0.05, 0.03, 0.01), filepath)
        assert os.path.exists(result)

    def test_plot_monthly_returns_creates_file(self, tmp_path):
        try:
            import matplotlib
        except ImportError:
            pytest.skip("matplotlib not installed")

        from aimoon.charts import plot_monthly_returns
        from aimoon.enhanced_backtest import EnhancedTrade

        trades = (
            EnhancedTrade("A", "StockA", "2024-01-05", "2024-01-20", 10.0, 11.0, 10.0, 0.1, "hold_period", 15),
            EnhancedTrade("B", "StockB", "2024-01-10", "2024-02-01", 20.0, 19.0, -5.0, 0.1, "stop_loss", 22),
        )
        filepath = str(tmp_path / "mr.png")
        result = plot_monthly_returns(trades, filepath)
        assert os.path.exists(result)

    def test_no_matplotlib_raises(self):
        from aimoon import charts
        original = charts.HAS_MATPLOTLIB
        charts.HAS_MATPLOTLIB = False
        try:
            with pytest.raises(ImportError, match="matplotlib"):
                charts.plot_equity_curve((100.0,), filepath="x.png")
        finally:
            charts.HAS_MATPLOTLIB = original
