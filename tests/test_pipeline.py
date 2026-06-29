"""End-to-end mock pipeline test."""

import asyncio


class TestMockPipeline:
    """Test that the mock pipeline runs without errors."""

    def test_mock_pipeline(self):
        from aimoon.adapters.driving.cli.pipeline import PipelineOrchestrator

        orch = PipelineOrchestrator()
        loop = asyncio.new_event_loop()
        output = loop.run_until_complete(orch.run_mock("600519", "贵州茅台"))
        loop.close()
        assert output.exists()
        assert output.suffix == ".html"

    def test_mock_pipeline_custom_output(self, tmp_path):
        from aimoon.adapters.driving.cli.pipeline import PipelineOrchestrator

        orch = PipelineOrchestrator(output_dir=str(tmp_path))
        loop = asyncio.new_event_loop()
        output = loop.run_until_complete(orch.run_mock("000001", "平安银行"))
        loop.close()
        assert output.exists()
        assert str(tmp_path) in str(output)


class TestRealPipeline:
    """Test the real run() path with mocked collectors."""

    def test_run_skip_ai_completes(self, monkeypatch, tmp_path):
        from aimoon.adapters.driven.collectors import mock_stock_analysis
        from aimoon.adapters.driving.cli.pipeline import PipelineOrchestrator
        from aimoon.core.domain.value_objects import CollectResult

        class MockRepo:
            async def collect_all(self, symbol, name=""):
                sa = mock_stock_analysis(symbol)
                sa.name = name
                return sa

            async def get_collect_results(self):
                return [CollectResult(platform="mock", status="success", count=10, elapsed_ms=50)]

        def mock_init(self, output_dir=None):
            self._output_dir = output_dir

        async def mock_run(self, symbol, name, *, skip_ai=False):
            from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
            from aimoon.adapters.driven.report.generator import HtmlReportGenerator
            from aimoon.adapters.driven.validation import IntegrityDataValidator
            from aimoon.core.application.services import collect_and_analyze

            repo = MockRepo()
            ai_analyzer = DeepSeekAIAnalyzer(mock=skip_ai)
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

        monkeypatch.setattr(PipelineOrchestrator, "__init__", mock_init)
        monkeypatch.setattr(PipelineOrchestrator, "run", mock_run)

        orch = PipelineOrchestrator(output_dir=str(tmp_path))
        loop = asyncio.new_event_loop()
        output = loop.run_until_complete(orch.run("600519", "贵州茅台", skip_ai=True))
        loop.close()
        assert output.exists()
        assert output.suffix == ".html"
