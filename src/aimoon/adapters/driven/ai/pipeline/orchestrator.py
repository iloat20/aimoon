"""Pipeline v2 orchestrator.

串联五阶段 + 300s 总硬上限。LLM 接入前只跑阶段往返 + 计时;
阶段实现(Task 13+)在各 ``_call_phase_*`` 中填充。当前为占位往返:
每阶段记日志、登记 ``__placeholder__`` 结果,最终返回包含
``final_markdown`` / ``phase_results`` / ``partial_phases`` 的 PipelineContext。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

from .phases import get_pipeline_phases

logger = logging.getLogger(__name__)

MAX_TOTAL_SEC = 300  # 总硬上限 5 分钟


class PipelineOrchestrator:
    """串联 phases 的 orchestrator;LLM 接入前只跑阶段往返 + 计时。"""

    def __init__(self, analyzer: object) -> None:
        self.analyzer = analyzer
        self._log: list[dict] = []

    async def run(
        self,
        si: StockAnalysis,
        *,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
    ) -> dict[str, object]:
        """Run the 5-phase pipeline (占位往返版)。

        ``reports`` / ``financial_md_path`` are accepted for forward-compat
        with analyzer._pipeline_analyze wiring; the placeholder transit only
        logs their presence. Returns a PipelineContext dict with
        ``final_markdown``, ``phase_results`` and ``partial_phases``.
        """
        t0 = time.monotonic()
        ctx: dict[str, object] = {
            "final_markdown": "",
            "phase_results": {},
            "partial_phases": [],
        }
        kwargs = {"reports": reports, "financial_md_path": financial_md_path}
        for spec in get_pipeline_phases():
            if time.monotonic() - t0 >= MAX_TOTAL_SEC:
                logger.warning("[pipeline] 超时 300s,剩余阶段占位降级")
                ctx["partial_phases"].append(spec.phase.value)
                ctx["phase_results"][spec.phase.value] = "__timeout__"
                continue
            logger.info("[pipeline] 进入阶段 %s (%.0fs 已用)", spec.phase.value, time.monotonic() - t0)
            # LLM 调用在 Task 13+ 接入;当前仅往返占位。
            await asyncio.sleep(0)
            ctx["phase_results"][spec.phase.value] = "__placeholder__"
        return ctx
