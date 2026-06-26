"""End-to-end mock pipeline test."""

import asyncio

import pytest


class TestMockPipeline:
    """Test that the mock pipeline runs without errors."""

    def test_mock_pipeline(self):
        from src.aimoon.pipeline import PipelineOrchestrator

        orch = PipelineOrchestrator()
        loop = asyncio.new_event_loop()
        output = loop.run_until_complete(orch.run_mock("600519", "贵州茅台"))
        loop.close()
        assert output.exists()
        assert output.suffix == ".html"

    def test_mock_pipeline_custom_output(self, tmp_path):
        from src.aimoon.pipeline import PipelineOrchestrator

        orch = PipelineOrchestrator(output_dir=str(tmp_path))
        loop = asyncio.new_event_loop()
        output = loop.run_until_complete(orch.run_mock("000001", "平安银行"))
        loop.close()
        assert output.exists()
        assert str(tmp_path) in str(output)
