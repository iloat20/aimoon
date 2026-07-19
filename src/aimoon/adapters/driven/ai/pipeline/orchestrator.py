"""Pipeline v2 orchestrator — ANALYSIS (JSON skeleton) + COMPILE (expand).

重构后(orchestrator 仅作「编排门面」):
- ``_run_pipeline`` 负责串阶段与容错(超时 / 异常 / 畸形 JSON 标 ``__partial__``)。
- 各重阶段体(工具采集、DIRECT 直出、ANALYSIS/COMPILE 调用)已抽到
  ``phase_runners.py`` 的自由函数;本模块只保留薄委托方法,便于测试 monkeypatch
  传输层方法(``_call_llm_with_stream`` / ``_stream_llm_content``)。

降级策略:任何阶段失败都 0 LLM(骨架/表格模板渲染),不再降级到 legacy。
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Callable
from pathlib import Path

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

# TOOL_RUNNERS / _run_peer_compare 仅作「重新导出」,供测试在 orch_mod 上 monkeypatch
# (test_orchestrator_wiring 打 TOOL_RUNNERS;test_pipeline_phases 打 _run_peer_compare)。
# phase_runners 在调用时经由 orchestrator 模块读取二者,确保补丁生效(生产路径也依赖此)。
from ..tools import TOOL_RUNNERS  # noqa: F401
from ._helpers import (
    _run_rewrite_llm,
    _run_rewrite_llm_batch,
)
from .context_renderer import render_stock_context
from .llm_client import PipelineLlmClient
from .phase_runners import (
    gather_catalysts,
    gather_tool_context,
    phase_analysis,
    phase_compile,
    phase_direct,
    phase_self_check,
    run_direct,
)
from .phases import Phase
from .skeleton_renderer import render_skeleton_md
from .types import AnalyzerRuntime, PipelineContext, _ToolContext
from .utils import partial as _partial
from .utils import run_peer_compare as _run_peer_compare  # noqa: F401

logger = logging.getLogger(__name__)

# 各阶段 LLM 调用超时
ANALYSIS_TIMEOUT = 210
COMPILE_TIMEOUT = 480
SELF_CHECK_TIMEOUT = 5  # 程序化校验,秒级(纯 Python,0 LLM)


class PipelineOrchestrator:
    """ANALYSIS (skeleton) + SELF_CHECK (programmatic) + COMPILE (expand)."""

    def __init__(self, analyzer: AnalyzerRuntime) -> None:
        self.analyzer = analyzer
        self._llm = PipelineLlmClient(analyzer)

    async def run(
        self,
        si: StockAnalysis,
        *,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
        use_fast: bool = False,
        use_single_call: bool = False,
        use_ultra_fast: bool = False,
    ) -> dict[str, object]:
        try:
            return await self._run_pipeline(
                si, reports=reports, financial_md_path=financial_md_path,
                use_fast=use_fast, use_single_call=use_single_call,
                use_ultra_fast=use_ultra_fast,
            )
        finally:
            # 收尾清理绝不能盖掉已成功返回的报告: aclose 抛错(如底层 client 被
            # 其他事件循环污染)时只记录,不向上抛,否则会丢弃完整正文触发降级。
            try:
                await self._llm.aclose()
            except Exception as e:  # noqa: BLE001 - 清理失败不影响结果
                logger.warning("[pipeline] llm client aclose 异常 %s: %s", type(e).__name__, e)

    async def _run_pipeline(
        self,
        si: StockAnalysis,
        *,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
        use_fast: bool = False,
        use_single_call: bool = False,
        use_ultra_fast: bool = False,
    ) -> dict[str, object]:
        ctx = PipelineContext()
        stock_md = render_stock_context(si, reports, financial_md_path)
        prior: dict[str, object] = {}

        skip_self_check = use_fast or use_single_call or use_ultra_fast
        skip_compile = use_single_call or use_ultra_fast

        # ---- DIRECT 流(默认): 工具采集 + 一次 LLM 直出完整报告 ----
        # 不经 JSON 骨架、不做二次扩写。two-phase(use_single_call=False,
        # use_ultra_fast=False)才走下方 skeleton+compile 流。
        direct_mode = use_single_call or use_ultra_fast
        if direct_mode:
            return await self._run_direct(si, stock_md, prior, ctx)

        # Phase 1: ANALYSIS — 并行工具 + LLM 输出 JSON 骨架
        try:
            analysis_result = await asyncio.wait_for(
                self._phase_analysis(
                    si, stock_md, prior, reports, financial_md_path,
                ),
                timeout=ANALYSIS_TIMEOUT,
            )
        except TimeoutError:
            logger.warning("[pipeline] ANALYSIS 超时 %ds, 降级", ANALYSIS_TIMEOUT)
            analysis_result = _partial("timeout")
        except Exception as e:
            logger.error(
                "[pipeline] ANALYSIS 异常 %s: %s\n%s",
                type(e).__name__, e, traceback.format_exc(),
            )
            analysis_result = _partial(f"{type(e).__name__}")

        ctx.phase_results[Phase.ANALYSIS.value] = analysis_result
        ctx.system_tables_md = str(prior.get("tables_md") or "")
        _skel = prior.get("skeleton")
        ctx.skeleton = _skel if isinstance(_skel, dict) else None

        if analysis_result.get("empty_analysis"):
            ctx.partial_phases.append(Phase.ANALYSIS.value)
            # 降级:用系统表格 + 数据汇总(0 LLM)
            if ctx.system_tables_md.strip():
                ctx.final_markdown = (
                    "# 分析报告（降级）\n\n"
                    "ANALYSIS 阶段未产出骨架,以下为系统预渲染数据(见下方「数据底稿」)。"
                )
            else:
                ctx.final_markdown = "# 分析报告（降级）\n\n数据采集或分析暂不可用。"
            return ctx.to_dict()
        # partial=True (工具部分失败) 但骨架已解析,标记后继续后续阶段
        if analysis_result.get("partial"):
            ctx.partial_phases.append(Phase.ANALYSIS.value)

        # Phase 1.5: SELF_CHECK — 程序化校验(0 LLM)
        skeleton = prior.get("skeleton")
        if not isinstance(skeleton, dict):
            skeleton = None
        if skeleton and not skip_self_check:
            try:
                sc_result = await asyncio.wait_for(
                    self._phase_self_check(skeleton, ctx.system_tables_md),
                    timeout=SELF_CHECK_TIMEOUT,
                )
            except Exception as e:
                logger.warning("[pipeline] SELF_CHECK 异常 %s: %s", type(e).__name__, e)
                sc_result = {"passed": True, "fixes_needed": []}
            ctx.phase_results[Phase.SELF_CHECK.value] = sc_result
            fixes = sc_result.get("fixes_needed", [])
            if fixes:
                prior["self_check_fixes"] = fixes

        # skip_compile: 直接用骨架渲染(0 LLM)
        if skip_compile and skeleton:
            ctx.final_markdown = render_skeleton_md(skeleton)
            ctx.phase_results[Phase.COMPILE.value] = {
                "output": ctx.final_markdown, "tool_results": {},
                "partial": False, "skipped": True,
            }
            return ctx.to_dict()

        # Phase 2: COMPILE — 基于骨架扩写
        if skeleton:
            try:
                compile_result = await asyncio.wait_for(
                    self._phase_compile(stock_md, prior),
                    timeout=COMPILE_TIMEOUT,
                )
            except TimeoutError:
                logger.warning("[pipeline] COMPILE 超时 %ds, 降级", COMPILE_TIMEOUT)
                compile_result = _partial("timeout")
            except Exception as e:
                logger.warning("[pipeline] COMPILE 异常 %s: %s", type(e).__name__, e)
                compile_result = _partial(f"{type(e).__name__}")
            ctx.phase_results[Phase.COMPILE.value] = compile_result
            if compile_result.get("partial"):
                ctx.partial_phases.append(Phase.COMPILE.value)
            else:
                text = compile_result.get("output", "")
                if text:
                    ctx.final_markdown = text

        # 容错:若 COMPILE 未产出,用骨架渲染(0 LLM,含真实数字)
        if not ctx.final_markdown and skeleton:
            logger.warning("[pipeline] COMPILE 未产出,回退骨架渲染")
            ctx.final_markdown = render_skeleton_md(skeleton)
            if Phase.COMPILE.value not in ctx.partial_phases:
                ctx.partial_phases.append(Phase.COMPILE.value)

        return ctx.to_dict()

    # ---- 共享: 工具采集 + 表格 + 摘要 (skeleton 与 direct 复用) ----------

    async def _gather_tool_context(
        self, si: StockAnalysis, stock_md: str, prior: dict,
    ) -> _ToolContext:
        """并行跑 9 个工具 → 0-LLM 渲染表格 + 摘要 → 拼 user 消息主体。

        无任何 LLM 调用。产物同时供 ANALYSIS(骨架)与 DIRECT(直出)两条流。
        会写入 ``prior['tables_md']`` / ``prior['summary']``。
        """
        return await gather_tool_context(si, stock_md, prior)

    async def _gather_catalysts(self, si: StockAnalysis) -> str:
        """尝试拉取近期催化并注入 DIRECT user message(实时检索)。

        仅在 ``direct_web_search_enabled=True`` 时由调用方触发。任何失败都安全
        降级为空串(不注入),绝不影响报告生成。
        """
        return await gather_catalysts(si)

    # ---- DIRECT 流: 工具 + 一次 LLM 直出完整报告 (无骨架、无扩写) --------

    async def _run_direct(
        self, si: StockAnalysis, stock_md: str, prior: dict, ctx: PipelineContext,
    ) -> dict[str, object]:
        """直出流入口: 采集 → 一次 LLM 直出完整报告 → 填充 ctx。"""
        return await run_direct(self, si, stock_md, prior, ctx)

    async def _phase_direct(
        self, si: StockAnalysis, stock_md: str, prior: dict,
    ) -> dict:
        """采集工具上下文后,一次流式 LLM 调用直出完整 Markdown 报告。

        使用 ``deepseek_analysis_effort`` 思考强度 + ``deepseek_max_tokens``
        输出上限(长文需要充足额度)。不产出 JSON 骨架,不做二次扩写。

        直出完成后做 0-LLM 数字对账 + 可选 LLM 定点重写(非阻断护栏),
        并把可信度摘要随结果透传。
        """
        return await phase_direct(self, si, stock_md, prior)

    # ---- Phase 1: ANALYSIS ------------------------------------------------

    async def _phase_analysis(
        self,
        si: StockAnalysis,
        stock_md: str,
        prior: dict,
        reports: dict | None,
        financial_md_path: Path | None,
    ) -> dict:
        """Phase 1: 并行工具 + LLM 输出 JSON 骨架。"""
        return await phase_analysis(self, si, stock_md, prior, reports, financial_md_path)

    # ---- Phase 1.5: SELF_CHECK (programmatic, 0 LLM) --------------------

    async def _phase_self_check(
        self, skeleton: dict, tables_md: str = "",
    ) -> dict[str, object]:
        """Programmatic skeleton validation — 0 LLM, pure Python.

        Returns ``{"passed": bool, "fixes_needed": list[str]}``.
        Never raises — on any failure defaults to passed.
        """
        return await phase_self_check(skeleton, tables_md)

    # ---- Phase 2: COMPILE ------------------------------------------------

    async def _phase_compile(self, stock_md: str, prior: dict) -> dict:
        """Phase 2: 基于骨架扩写终稿。

        纯扩写/格式化,不做二次推理 —— 关闭思考模式(thinking=disabled)以省下
        全部思考 token(思考 token 按输出计价,是主要成本)。关闭后 temperature
        恢复生效(默认 0.3,保证行文自然)。

        为命中 DeepSeek 前缀缓存,system prompt 只放 compile.md 固定规则(稳定前缀),
        本次标的的可变数据(快照/系统表/骨架/摘要/自检)全部放到 user 消息里。
        """
        return await phase_compile(self, stock_md, prior)

    # ---- LLM transport delegates (kept as methods so tests can monkeypatch) ----

    async def _call_llm_with_stream(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str | None = "max",
        thinking: bool | None = None,
    ) -> dict:
        return await self._llm.call_llm_with_stream(
            messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort,
            thinking=thinking,
        )

    async def _stream_llm_content(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str | None = "high",
        thinking: bool | None = None,
    ) -> str:
        return await self._llm.stream_llm_content(
            messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort,
            thinking=thinking,
        )

    def _make_rewrite_llm(self) -> Callable[[str, str], str]:
        """构造供 ``_verify_and_fix`` 使用的同步 llm 封装 ``(system, user) -> str``。

        底层走 ``PipelineLlmClient.call_llm_with_stream`` 非流式单发,并强制
        ``thinking=False``(纯修正场景,省思考 token)。为不与主事件循环冲突,
        实际 HTTP 调用在独立线程的新事件循环里执行(见 ``_run_rewrite_llm``)。
        """
        client = self._llm

        def _call(system: str, user: str) -> str:
            return _run_rewrite_llm(client, system, user)

        return _call

    def _make_rewrite_batch_llm(
        self,
    ) -> Callable[[list[tuple[str, str]]], list[str]]:
        """构造供 ``_verify_and_fix`` 使用的批量 llm 封装。

        ``(list[(system, user)]) -> list[corrected]``。底层 ``_run_rewrite_llm_batch``
        用单线程 + 单事件循环 + 单临时 httpx client 并发 ``gather`` 全部疑点，
        省去逐条改正时 N-1 次线程/循环/client 创建开销;强制 ``thinking=False``。
        """
        client = self._llm

        def _call(prompts: list[tuple[str, str]]) -> list[str]:
            return _run_rewrite_llm_batch(client, prompts)

        return _call
