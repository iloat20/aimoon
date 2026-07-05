"""Pipeline v2 orchestrator (占位 v1)。

串联五阶段 + 300s 总硬上限。LLM 调用在 Task 8-13 接入,
当前为占位 sleep。
"""

from __future__ import annotations

import asyncio
import logging
import time

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

from .phases import get_pipeline_phases

logger = logging.getLogger(__name__)

MAX_TOTAL_SEC = 300  # 总硬上限 5 分钟


class PipelineOrchestrator:
    """串联 phases 的占位 orchestrator;LLM 接入前只跑阶段往返 + 计时。"""

    def __init__(self, analyzer: object) -> None:
        self.analyzer = analyzer
        self._log: list[dict] = []

    async def run(self, si: StockAnalysis) -> dict:
        t0 = time.monotonic()
        ctx: dict = {"report_partial": [], "phase_results": {}}
        for spec in get_pipeline_phases():
            if time.monotonic() - t0 >= MAX_TOTAL_SEC:
                logger.warning("[pipeline] 超时 300s,剩余标 超时降级")
                ctx["report_partial"].append("超时降级")
                break
            await asyncio.sleep(0)  # LLM 调用在 Task 8-13 接入
            ctx["phase_results"][spec.phase.value] = "__placeholder__"
        return ctx
