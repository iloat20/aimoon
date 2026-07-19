"""AI analysis engine (facade) — provider-agnostic (DeepSeek / LongCat).

Delegates prompt building, HTTP/SSE transport, and post-processing to the
``prompt_builder``, ``api_client``, and ``post_processor`` modules. The active
provider (DeepSeek vs LongCat) is selected via ``Settings.ai_provider``; the
resolved endpoint / key / model come from ``resolve_ai_provider``.
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

from ..config.settings import AIProviderConfig, get_settings, resolve_ai_provider
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

# 遗留路径(_legacy_analyze)的 LLM 工具调用轮数开关 —— 有意置 0 关闭。
# 默认 DIRECT 流不走此路径,联网检索改由 pipeline 并行工具 _gather_catalysts 负责
# (且受 direct_web_search_enabled 控制)。置 0 时 _call_deepseek 的工具循环整体跳过;
# 若要重新启用遗留 web_search 工具调用,将其调到 >0 即可。这是功能开关,不是死代码。
_MAX_TOOL_ROUNDS = 0


class DeepSeekAIAnalyzer(AIAnalyzerPort):
    """Provider-agnostic AI analysis implementation (DeepSeek / LongCat).

    Calls an OpenAI-compatible chat completions API directly (no openai SDK),
    supports tool calling and streaming output. Produces an ``AnalysisReport``
    compatible with the HTML templates. The concrete backend is resolved from
    ``Settings.ai_provider`` so the same class serves DeepSeek and LongCat.
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
        # 解析当前 AI 提供商配置(deepseek / longcat ...),统一驱动 url/key/model。
        self._cfg: AIProviderConfig = resolve_ai_provider(self._settings)
        self.api_key = api_key or self._cfg.api_key
        self.api_url = api_url or self._cfg.chat_url
        self._http = http_client or httpx.AsyncClient(
            timeout=180.0,
            limits=httpx.Limits(max_keepalive_connections=5),
        )
        self._api = DeepSeekApiClient(
            api_url=self.api_url,
            api_key=self.api_key,
            settings=self._settings,
            provider_config=self._cfg,
            http_client=self._http,
        )

    @property
    def provider_config(self) -> AIProviderConfig:
        """Expose the resolved provider config to the v2 pipeline transport."""
        return self._cfg


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

        DEPRECATION: the ``_legacy_analyze`` path is deprecated and scheduled for
        removal (v2 pipeline is the supported path). New code should pass
        ``use_pipeline_v2=True``; the legacy branch is retained only for backward
        compatibility. The ``analysis:*`` L1 cache is no longer read or written by
        either path (removed in #6); do not re-add cross-path caching. Do not
        expand legacy behavior.
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
        # DEPRECATED (架构审查 #6, 2026-07-19): 单发遗留路径,计划移除。
        # v2 pipeline(_pipeline_analyze) 是受支持路径;此分支仅保留向后兼容,
        # 不再读取 ``analysis:*`` 缓存(该跨路径死读已在 #6 移除)。
        # 移除前需同步更新 test_ai.py / test_integration_pipeline.py 的路由断言。
        if self._mock:
            from ..common.mock import mock_analysis_report

            return mock_analysis_report(stock_info.symbol, stock_info.name)

        # DEPRECATED 路径: 不再读取 ``analysis:*`` L1 缓存(该 key 仅由 v2 写入,
        # 属跨路径死读;见架构审查 #6)。legacy 每次重新生成——本路径已弃用,开销可接受。
        t0 = time.monotonic()
        collected_data = build_data_dict(stock_info, reports, financial_md_path)

        fallback = "AI分析暂不可用，以下为基础数据汇总。"
        try:
            md = await self._call_deepseek(stock_info.symbol, stock_info.name, collected_data)
            md = deduplicate_tail(md)
        except Exception as e:  # broad tolerance
            logger.warning("[ai_analyze_stock] %s: %s", type(e).__name__, e)
            md = ""
        # 流被中断/上游返回空时会得到空字符串(不一定抛异常),此前会静默缓存并产出空报告。
        # 现在显式兜底,且兜底文案不写缓存 —— 让下一次运行有机会重试拿到真实分析。
        if not md or not md.strip():
            logger.warning("[ai_analysis] AI 输出为空,使用兜底文案且跳过缓存")
            md = fallback

        elapsed = int((time.monotonic() - t0) * 1000)
        logger.info("[ai_analysis] completed in %dms, output %d chars", elapsed, len(md))

        # 写入缓存(兜底文案不缓存,避免污染当日缓存)
        if md != fallback:
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
            # v2 失败时用骨架渲染(0 LLM),不再降级到 legacy
            from .pipeline.skeleton_renderer import render_skeleton_md
            skeleton = ctx.get("skeleton") if isinstance(ctx, dict) else None
            if skeleton:
                text = render_skeleton_md(skeleton)
                logger.info("[pipeline_v2] 降级到骨架渲染(0 LLM)")
            else:
                text = "# 分析报告（降级）\n\n数据采集或分析暂不可用。"
        # 部分阶段降级(无 final / 部分阶段失败)由下方 with_degradation_notice 标记,
        # 不再写入 analysis:* L1 缓存(架构审查 #6: 该 key 仅被已弃用 legacy 读取,属跨路径死写)。
        # 骨架缓存(skeleton:*)由 phase_runners 独立管理,不受影响。
        partial = ctx.get("partial_phases") if isinstance(ctx, dict) else []
        appendix_md = ctx.get("system_tables_md") or "" if isinstance(ctx, dict) else ""
        mos_html = ctx.get("margin_of_safety_html") or "" if isinstance(ctx, dict) else ""
        report = build_analysis_report(
            symbol=stock_info.symbol,
            name=stock_info.name,
            md=text,
            current_price=stock_info.quote.price if stock_info.quote else None,
            data_appendix_md=appendix_md,
            margin_of_safety_html=mos_html,
        )
        # 透传质量护栏产出的可信度摘要(orchestrator 经 to_dict 返回)。
        # frozen 模型必须用 model_copy,不能就地赋值。
        credibility = ctx.get("credibility") or {} if isinstance(ctx, dict) else {}
        if credibility:
            report = report.model_copy(update={"credibility": credibility})
        # v2 部分阶段降级时,在报告插入可见降级标记(阶段名列表),避免静默丢失。
        # (partial 已在上方缓存判定处取过,此处复用同一变量。)
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


# 向后兼容 / 显式选择别名: 本分析器现已支持 DeepSeek 与 LongCat(由 Settings.ai_provider
# 切换),保留 DeepSeekAIAnalyzer 名称以维持既有调用与测试;新增 StockAIAnalyzer 与
# LongCatAIAnalyzer 便于按意图显式选用。三者指向同一实现。
StockAIAnalyzer = DeepSeekAIAnalyzer
LongCatAIAnalyzer = DeepSeekAIAnalyzer

