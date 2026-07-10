"""Pipeline orchestrator — assembles dependencies and invokes application services.

The orchestrator acts as a composer:
- Creates all adapter instances
- Injects dependencies into application services
- Calls the application service function to execute business logic
- Does NOT contain business logic itself
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
from aimoon.adapters.driven.collectors import (
    CompositeStockAnalysisRepository,
    MockStockAnalysisRepository,
)
from aimoon.adapters.driven.config.settings import get_settings
from aimoon.adapters.driven.financial.akshare_adapter import AkshareFinancialAdapter
from aimoon.adapters.driven.report.generator import HtmlReportGenerator
from aimoon.adapters.driven.validation import IntegrityDataValidator
from aimoon.adapters.driving.cli.run_summary import render_run_summary
from aimoon.core.application.services import collect_and_analyze


class PipelineOrchestrator:
    """Coordinates the full pipeline by assembling adapters and calling application services."""

    def __init__(
        self, output_dir: str | None = None, mock_mode: bool | None = None, use_v2: bool = True,
        use_fast: bool = False, use_single_call: bool = False, use_ultra_fast: bool = False,
    ) -> None:
        self._settings = get_settings()
        self._output_dir = output_dir
        self._mock_mode = mock_mode if mock_mode is not None else self._settings.mock_mode
        self._use_v2 = use_v2
        self._use_fast = use_fast
        self._use_single_call = use_single_call
        self._use_ultra_fast = use_ultra_fast

    async def run(self, symbol: str, name: str, *, skip_ai: bool = False) -> Path:
        """Run full pipeline with real data collection."""
        async with httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        ) as http:
            repo = CompositeStockAnalysisRepository(
                financial_collector=AkshareFinancialAdapter(),
                http_client=http,
            )
            ai_analyzer = DeepSeekAIAnalyzer(mock=self._mock_mode, http_client=http)
            data_validator = IntegrityDataValidator()
            report_generator = HtmlReportGenerator()
            t0 = time.monotonic()
            report_path = await collect_and_analyze(
                    symbol=symbol,
                    name=name,
                    repo=repo,
                    ai_analyzer=ai_analyzer,
                    data_validator=data_validator,
                    report_generator=report_generator,
                    output_dir=self._output_dir,
                    skip_ai=skip_ai,
                    use_pipeline_v2=self._use_v2,
                    use_fast=self._use_fast,
                    use_single_call=self._use_single_call,
                    use_ultra_fast=self._use_ultra_fast,
                )
            elapsed = int((time.monotonic() - t0) * 1000)
            results = await repo.get_collect_results()
            print(render_run_summary(list(results), total_elapsed_ms=elapsed, skip_ai=skip_ai))
            return report_path

    async def run_mock(self, symbol: str, name: str) -> Path:
        """Run full pipeline with mock data."""
        print(f" [Mock] 生成 {name}({symbol}) 的模拟分析报告...")

        repo = MockStockAnalysisRepository()
        ai_analyzer = DeepSeekAIAnalyzer(mock=True)
        data_validator = IntegrityDataValidator()
        report_generator = HtmlReportGenerator()

        t0 = time.monotonic()
        report_path = await collect_and_analyze(
            symbol=symbol,
            name=name,
            repo=repo,
            ai_analyzer=ai_analyzer,
            data_validator=data_validator,
            report_generator=report_generator,
            output_dir=self._output_dir,
            skip_ai=False,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        results = await repo.get_collect_results()
        print(render_run_summary(list(results), total_elapsed_ms=elapsed, skip_ai=False))
        return report_path
