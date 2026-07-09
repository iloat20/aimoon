"""DeepSeek AI analysis engine (facade).

Delegates prompt building, HTTP/SSE transport, and post-processing to the
``prompt_builder``, ``api_client``, and ``post_processor`` modules.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from aimoon.core.application.ports import AIAnalyzer as AIAnalyzerPort
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

from ..config.settings import get_settings
from .api_client import DeepSeekApiClient
from .post_processor import (
    build_analysis_report,
    deduplicate_tail,
    strip_xml_tool_calls,
    with_degradation_notice,
)
from .prompt_builder import build_data_dict, build_user_message
from .prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 0


class DeepSeekAIAnalyzer(AIAnalyzerPort):
    """DeepSeek AI analysis implementation.

    Calls DeepSeek API directly (no openai SDK), supports tool calling
    and streaming output. Produces AnalysisReport compatible with HTML templates.
    """

    def __init__(
        self,
        mock: bool = False,
        api_key: str = "",
        api_url: str = "",
        settings: Any = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provided_settings = settings
        self._settings = settings or get_settings()
        self._mock = mock or self._settings.mock_mode
        self.api_key = api_key or self._settings.deepseek_api_key
        base = self._settings.deepseek_base_url.rstrip("/")
        self.api_url = api_url or f"{base}/v1/chat/completions"
        self._http = http_client or httpx.AsyncClient(
            timeout=180.0,
            limits=httpx.Limits(max_keepalive_connections=5),
        )
        self._api = DeepSeekApiClient(
            api_url=self.api_url,
            api_key=self.api_key,
            settings=self._settings,
            http_client=self._http,
        )

    async def analyze(
        self,
        stock_info: StockAnalysis,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
        *,
        use_pipeline_v2: bool = False,
        use_fast: bool = False,
        use_single_call: bool = False,
        use_ultra_fast: bool = False,
    ) -> AnalysisReport:
        """AI analysis entry point - receives domain entity, returns AnalysisReport.

        When ``use_pipeline_v2`` is True, run the two-phase pipeline orchestrator
        (ANALYSIS + COMPILE); otherwise preserve the existing single-shot behavior
        (``_legacy_analyze``). Old callers (without the kwarg) work identically —
        DEFAULT OFF. ``use_fast`` skips ANALYSIS self-check for a faster run.
        ``use_single_call`` / ``use_ultra_fast`` are experimental low-latency modes.
        """
        if use_pipeline_v2:
            return await self._pipeline_analyze(
                stock_info, reports, financial_md_path, use_fast=use_fast,
                use_single_call=use_single_call, use_ultra_fast=use_ultra_fast,
            )
        return await self._legacy_analyze(stock_info, reports, financial_md_path)

    async def _legacy_analyze(
        self,
        stock_info: StockAnalysis,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
    ) -> AnalysisReport:
        if self._mock:
            from ..common.mock import mock_analysis_report

            return mock_analysis_report(stock_info.symbol, stock_info.name)

        # 检查缓存
        from .cache import get_analysis_cache

        cached = get_analysis_cache(stock_info.symbol)
        if cached:
            logger.info("[ai_analysis] cache hit for %s", stock_info.symbol)
            return AnalysisReport(
                symbol=stock_info.symbol,
                name=stock_info.name,
                summary=cached[:200] + "..." if len(cached) > 200 else cached,
                report_text=cached,
                investment_advice="本报告由DeepSeek AI自动生成，仅供参考，不构成投资建议。",
            )

        t0 = time.monotonic()
        collected_data = build_data_dict(stock_info, reports, financial_md_path)

        try:
            md = await self._call_deepseek(stock_info.symbol, stock_info.name, collected_data)
            md = deduplicate_tail(md)
        except Exception as e:  # broad tolerance
            logger.warning("[ai_analyze_stock] %s: %s", type(e).__name__, e)
            md = "AI分析暂不可用，以下为基础数据汇总。"

        elapsed = int((time.monotonic() - t0) * 1000)
        logger.info("[ai_analysis] completed in %dms, output %d chars", elapsed, len(md))

        # 写入缓存
        from .cache import set_analysis_cache

        set_analysis_cache(stock_info.symbol, md)

        return build_analysis_report(
            symbol=stock_info.symbol,
            name=stock_info.name,
            md=md,
            current_price=stock_info.quote.price if stock_info.quote else None,
        )

    async def _pipeline_analyze(
        self,
        stock_info: StockAnalysis,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
        *,
        use_fast: bool = False,
        use_single_call: bool = False,
        use_ultra_fast: bool = False,
    ) -> AnalysisReport:
        """Two-phase pipeline v2 analysis entry (Plan B in brainstorming).

        Runs ANALYSIS (parallel tools + LLM + self-check) then COMPILE
        (long-form final report) and returns ``final_markdown``. The L1 disk cache
        (``cache.py``) is reused; on orchestrator failure a degraded fallback
        report is produced so the pipeline never aborts.
        """
        from .pipeline.orchestrator import PipelineOrchestrator

        ctx: dict = {}
        try:
            ctx = await PipelineOrchestrator(self).run(
                stock_info, reports=reports, financial_md_path=financial_md_path,
                use_fast=use_fast, use_single_call=use_single_call,
                use_ultra_fast=use_ultra_fast,
            )
        except Exception as e:
            logger.warning("[pipeline_v2] orchestrator failed: %s: %s", type(e).__name__, e)
        text = ctx.get("final_markdown", "") if isinstance(ctx, dict) else ""
        if not text:
            # v2 失败时降级到 legacy 一段式(而非数据汇总 fallback),并插入可见降级标记。
            logger.info("[pipeline_v2] 降级到 legacy 一段式分析")
            legacy = await self._legacy_analyze(stock_info, reports, financial_md_path)
            return with_degradation_notice(legacy, "降级 legacy 一段式(v2 未产出文本)")
        from .cache import set_analysis_cache

        set_analysis_cache(stock_info.symbol, text)
        report = build_analysis_report(
            symbol=stock_info.symbol,
            name=stock_info.name,
            md=text,
            current_price=stock_info.quote.price if stock_info.quote else None,
        )
        # v2 部分阶段降级时,在报告插入可见降级标记(阶段名列表),避免静默丢失。
        partial = ctx.get("partial_phases") if isinstance(ctx, dict) else []
        if isinstance(partial, list) and partial:
            report = with_degradation_notice(
                report, "部分阶段降级: " + "、".join(partial)
            )
        return report

    async def _call_deepseek(
        self, stock_code: str, stock_name: str, collected_data: Any
    ) -> str:
        """Call DeepSeek API with streaming + tool calling."""
        user_msg = build_user_message(stock_code, stock_name, collected_data)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        for _round_idx in range(_MAX_TOOL_ROUNDS):
            tool_call_result = await self._api.call_with_tools(messages)
            if tool_call_result is None:
                break
            messages, should_break = tool_call_result
            if should_break:
                break

        if _MAX_TOOL_ROUNDS > 0 and any(
            m.get("role") == "tool" for m in messages
        ):
            messages.append({
                "role": "user",
                "content": (
                    "以上所有搜索已完成，数据已全部提供给你。"
                    "请立即基于以上全部数据，输出完整的深度分析报告。"
                    "不要再调用搜索工具，直接开始分析输出。"
                ),
            })

        result = await self._api.stream_final_response(messages)
        return strip_xml_tool_calls(result)
