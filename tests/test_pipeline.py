"""End-to-end mock pipeline test."""

import pytest


class TestMockPipeline:
    """Test that the mock pipeline runs without errors."""

    @pytest.mark.asyncio
    async def test_mock_pipeline(self):
        from src.aimoon.pipeline import PipelineOrchestrator

        orch = PipelineOrchestrator()
        output = await orch.run_mock("600519", "贵州茅台")
        assert output.exists()
        assert output.suffix == ".html"

    @pytest.mark.asyncio
    async def test_mock_pipeline_custom_output(self, tmp_path):
        from src.aimoon.pipeline import PipelineOrchestrator

        orch = PipelineOrchestrator(output_dir=str(tmp_path))
        output = await orch.run_mock("000001", "平安银行")
        assert output.exists()
        assert str(tmp_path) in str(output)
