"""Pipeline v2 integration tests (Task 16).

CLI flag tests + kwarg forwarding are deterministic (no network). The real
600519 e2e smoke requires DEEPSEEK_API_KEY + network and is marked
``@pytest.mark.integration`` so the default unit gate skips it.
"""

import pytest

from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer

# ---------------------------------------------------------------------------
# CLI flag parsing
# ---------------------------------------------------------------------------


def _parse_args(argv):
    """Parse argv through the real argparse setup in cli/main.py."""
    from aimoon.adapters.driving.cli import main as main_mod

    return main_mod.build_parser().parse_args(argv)


def test_cli_flags_registered():
    """--two-phase / --legacy are accepted by the argument parser."""
    args = _parse_args(["000001", "--two-phase"])
    assert args.two_phase is True
    assert args.legacy is False

    args = _parse_args(["000001", "--legacy"])
    assert args.legacy is True
    assert args.two_phase is False

    args = _parse_args(["000001"])
    assert args.two_phase is False
    assert args.legacy is False


def test_cli_use_v2_and_legacy_mutually_override():
    """--legacy and --two-phase are mutually exclusive: parser errors if both given."""
    import pytest

    with pytest.raises(SystemExit):
        _parse_args(["000001", "--two-phase", "--legacy"])


def test_cli_v2_flag_triggers_pipeline(monkeypatch):
    """--two-phase flows through main() to the orchestrator (use_single_call=False)."""
    import sys

    from aimoon.adapters.driving.cli import main as main_mod

    observed: list[bool] = []

    def capture_orchestrator(output_dir=None, mock_mode=None, use_v2=False,
                             use_fast=False, use_single_call=True, use_ultra_fast=False):
        observed.append(use_single_call)
        raise SystemExit(0)

    monkeypatch.setattr(main_mod, "PipelineOrchestrator", capture_orchestrator)

    from aimoon.adapters.driven.config.settings import (
        Settings,
        inject_settings,
        reset_settings,
    )

    reset_settings()
    inject_settings(Settings(deepseek_api_key="test-key-int", mock_mode=False))

    old_argv = sys.argv
    try:
        sys.argv = ["aimoon", "000001", "--two-phase"]
        try:
            main_mod.main()
        except SystemExit as se:
            assert se.code in (0, None)
    finally:
        sys.argv = old_argv

    assert observed and observed[-1] is False, observed


def test_cli_default_off_by_default(monkeypatch):
    """No flag -> orchestrator constructed with v2 single-call defaults
    (use_v2=True, use_single_call=True)."""
    import sys

    from aimoon.adapters.driving.cli import main as main_mod

    observed: list[dict] = []

    def capture_orchestrator(output_dir=None, mock_mode=None, use_v2=False,
                             use_fast=False, use_single_call=True, use_ultra_fast=False):
        observed.append({"use_v2": use_v2, "use_single_call": use_single_call})
        raise SystemExit(0)

    monkeypatch.setattr(main_mod, "PipelineOrchestrator", capture_orchestrator)

    from aimoon.adapters.driven.config.settings import (
        Settings,
        inject_settings,
        reset_settings,
    )

    reset_settings()
    inject_settings(Settings(deepseek_api_key="test-key-int", mock_mode=False))

    old_argv = sys.argv
    try:
        sys.argv = ["aimoon", "000001"]
        try:
            main_mod.main()
        except SystemExit as se:
            assert se.code in (0, None)
    finally:
        sys.argv = old_argv

    assert observed and observed[-1] == {"use_v2": True, "use_single_call": True}, observed


# ---------------------------------------------------------------------------
# kwarg forwarding through the application service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_ai_analysis_forwards_use_pipeline_v2_true(monkeypatch):
    import aimoon.adapters.driven.ai.analyzer as analyzer_mod

    captured: dict = {}

    async def fake_analyze(self, stock_info, reports=None, financial_md_path=None, **kwargs):
        captured.update(kwargs)
        from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

        return AnalysisReport(symbol=stock_info.symbol, name=stock_info.name)

    monkeypatch.setattr(analyzer_mod.DeepSeekAIAnalyzer, "analyze", fake_analyze)

    from aimoon.core.application.services.stock_analysis_service import _run_ai_analysis

    analyzer = analyzer_mod.DeepSeekAIAnalyzer(mock=True)
    si = monkeypatch_stock()
    await _run_ai_analysis(si, analyzer, use_pipeline_v2=True)

    assert captured.get("use_pipeline_v2") is True


@pytest.mark.asyncio
async def test_run_ai_analysis_forwards_use_pipeline_v2_false(monkeypatch):
    import aimoon.adapters.driven.ai.analyzer as analyzer_mod

    captured: dict = {}

    async def fake_analyze(self, stock_info, reports=None, financial_md_path=None, **kwargs):
        captured.update(kwargs)
        from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

        return AnalysisReport(symbol=stock_info.symbol, name=stock_info.name)

    monkeypatch.setattr(analyzer_mod.DeepSeekAIAnalyzer, "analyze", fake_analyze)

    from aimoon.core.application.services.stock_analysis_service import _run_ai_analysis

    analyzer = analyzer_mod.DeepSeekAIAnalyzer(mock=True)
    si = monkeypatch_stock()
    await _run_ai_analysis(si, analyzer, use_pipeline_v2=False)

    assert captured.get("use_pipeline_v2") is False


def monkeypatch_stock():
    from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

    return StockAnalysis(symbol="600519", name="贵州茅台")


# ---------------------------------------------------------------------------
# Real e2e smoke (requires DEEPSEEK_API_KEY + network)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pipeline_v2_smoke_runs_without_cracking():
    """REAL 600519 run via CompositeStockAnalysisRepository + 5-phase pipeline."""
    from aimoon.adapters.driven.collectors.composite_repo import (
        CompositeStockAnalysisRepository,
    )
    from aimoon.adapters.driven.financial.akshare_adapter import AkshareFinancialAdapter

    repo = CompositeStockAnalysisRepository(
        financial_collector=AkshareFinancialAdapter(),
    )
    si = await repo.collect_all("600519")
    az = DeepSeekAIAnalyzer(mock=False)
    rep = await az.analyze(si, use_pipeline_v2=True)
    assert rep.symbol == "600519"
    assert len(rep.report_text) > 500, f"output too short: {len(rep.report_text)} chars"
    markers = ["看空", "估值", "FCFE", "目标价"]
    assert any(m in rep.report_text for m in markers), "missing key sections"
