"""Pipeline orchestrator — assembles dependencies and invokes application services.

The orchestrator acts as a composer:
- Creates all adapter instances
- Injects dependencies into application services
- Calls the application service function to execute business logic
- Does NOT contain business logic itself
"""

from __future__ import annotations

from pathlib import Path

from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
from aimoon.adapters.driven.collectors import (
    CompositeStockAnalysisRepository,
    MockStockAnalysisRepository,
)
from aimoon.adapters.driven.config.settings import get_settings
from aimoon.adapters.driven.financial.akshare_adapter import AkshareFinancialAdapter
from aimoon.adapters.driven.report.generator import HtmlReportGenerator
from aimoon.adapters.driven.validation import IntegrityDataValidator
from aimoon.core.application.services import collect_and_analyze


class PipelineOrchestrator:
    """Coordinates the full pipeline by assembling adapters and calling application services."""

    def __init__(self, output_dir: str | None = None, mock_mode: bool | None = None) -> None:
        self._settings = get_settings()
        self._output_dir = output_dir
        self._mock_mode = mock_mode if mock_mode is not None else self._settings.mock_mode

    async def run(self, symbol: str, name: str, *, skip_ai: bool = False) -> Path:
        """Run full pipeline with real data collection."""
        repo = CompositeStockAnalysisRepository(
            financial_collector=AkshareFinancialAdapter(),
        )
        ai_analyzer = DeepSeekAIAnalyzer(mock=self._mock_mode)
        data_validator = IntegrityDataValidator()
        report_generator = HtmlReportGenerator()

        return await collect_and_analyze(
            symbol=symbol,
            name=name,
            repo=repo,
            ai_analyzer=ai_analyzer,
            data_validator=data_validator,
            report_generator=report_generator,
            output_dir=self._output_dir,
            skip_ai=skip_ai,
        )

    async def run_mock(self, symbol: str, name: str) -> Path:
        """Run full pipeline with mock data."""
        print(f" [Mock] 生成 {name}({symbol}) 的模拟分析报告...")

        repo = MockStockAnalysisRepository()
        ai_analyzer = DeepSeekAIAnalyzer(mock=True)
        data_validator = IntegrityDataValidator()
        report_generator = HtmlReportGenerator()

        return await collect_and_analyze(
            symbol=symbol,
            name=name,
            repo=repo,
            ai_analyzer=ai_analyzer,
            data_validator=data_validator,
            report_generator=report_generator,
            output_dir=self._output_dir,
            skip_ai=False,
        )
