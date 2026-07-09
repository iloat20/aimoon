"""Tests for the stock_analysis_service application entry points.

Audit P3.1 priority 4 — `collect_and_analyze` / `analyze_stock` were
only partially covered. These exercise the real wiring (validator + report
generator) with mocked repo / AI analyzer.
"""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from aimoon.adapters.driven.validation.integrity_checker import IntegrityDataValidator
from aimoon.core.application.services.stock_analysis_service import (
    analyze_stock,
    collect_and_analyze,
)
from aimoon.core.domain import AnalysisReport, StockAnalysis
from aimoon.core.domain.entities.quote import StockQuote


def _make_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.collect_all = AsyncMock(return_value=StockAnalysis(symbol="600519", name="贵州茅台"))
    repo.get_collect_results = AsyncMock(return_value={})
    return repo


def _make_report_gen() -> AsyncMock:
    gen = AsyncMock()
    gen.generate = Mock(return_value=Path("/tmp/report_600519.html"))
    return gen


def _make_analyzer(raises: bool = False) -> AsyncMock:
    az = AsyncMock()
    if raises:
        az.analyze = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        rep = AnalysisReport(symbol="600519", name="贵州茅台", report_text="ok")
        az.analyze = AsyncMock(return_value=rep)
    return az


@pytest.mark.asyncio
async def test_collect_and_analyze_skip_ai_returns_path():
    repo = _make_repo()
    gen = _make_report_gen()
    az = _make_analyzer()
    path = await collect_and_analyze(
        "600519", "贵州茅台", repo, az, IntegrityDataValidator(), gen, skip_ai=True
    )
    assert path == Path("/tmp/report_600519.html")
    repo.collect_all.assert_awaited_once()
    gen.generate.assert_called_once()
    # skip_ai=True -> AI analyzer must NOT be called
    az.analyze.assert_not_awaited()


@pytest.mark.asyncio
async def test_collect_and_analyze_with_ai_calls_analyzer():
    repo = _make_repo()
    gen = _make_report_gen()
    az = _make_analyzer()
    path = await collect_and_analyze(
        "600519", "贵州茅台", repo, az, IntegrityDataValidator(), gen, skip_ai=False
    )
    assert path == Path("/tmp/report_600519.html")
    az.analyze.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_and_analyze_ai_failure_falls_back():
    repo = _make_repo()
    gen = _make_report_gen()
    az = _make_analyzer(raises=True)
    path = await collect_and_analyze(
        "600519", "贵州茅台", repo, az, IntegrityDataValidator(), gen, skip_ai=False
    )
    # failure must not abort pipeline: report still generated via fallback
    assert path == Path("/tmp/report_600519.html")
    gen.generate.assert_called_once()


@pytest.mark.asyncio
async def test_analyze_stock_skip_ai_returns_fallback_report():
    az = _make_analyzer()
    info = StockAnalysis(symbol="600519", name="贵州茅台", quote=StockQuote(price=1700))
    rep = await analyze_stock(info, az, IntegrityDataValidator(), skip_ai=True)
    assert isinstance(rep, AnalysisReport)
    assert rep.symbol == "600519"
    az.analyze.assert_not_awaited()


@pytest.mark.asyncio
async def test_analyze_stock_with_ai_returns_analysis():
    az = _make_analyzer()
    info = StockAnalysis(symbol="600519", name="贵州茅台", quote=StockQuote(price=1700))
    rep = await analyze_stock(info, az, IntegrityDataValidator(), skip_ai=False)
    assert rep.report_text == "ok"
    az.analyze.assert_awaited_once()
