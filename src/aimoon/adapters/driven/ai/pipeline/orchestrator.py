"""Pipeline v2 orchestrator — ANALYSIS (JSON skeleton) + COMPILE (expand).

重构后架构(骨架+扩写):
- Phase 1 (ANALYSIS): 并行工具 + LLM 输出 JSON 骨架(推理结论,不写文章)
- Phase 1.5 (SELF_CHECK): 程序化校验骨架(0 LLM,纯 Python)
- Phase 2 (COMPILE): 基于骨架扩写为完整长文

总硬上限 720s(12 分钟)。每阶段独立容错:超时 / 异常 / 畸形 JSON 标 ``__partial__`` 并继续,
绝不阻塞后续阶段(项目 CLAUDE.md 的 broad-tolerance 规则)。
降级策略:任何阶段失败都 0 LLM(骨架/表格模板渲染),不再降级到 legacy。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import traceback
from pathlib import Path

import httpx

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.entities.research import ResearchReportData

from ...config.settings import get_settings
from ..tools import TOOL_RUNNERS
from ..xml_utils import strip_xml_tool_calls
from .context_renderer import render_stock_context
from .llm_client import PipelineLlmClient
from .phases import Phase, phase_system_prompt
from .skeleton_renderer import render_skeleton_md
from .skeleton_validator import validate_skeleton
from .table_renderer import (
    render_fcf_dividend,
    render_financial_health_ext,
    render_financial_temporal,
    render_peer_comparison,
    render_scenario_prob,
    render_sentiment,
    render_valuation_targets,
)
from .timing import logphase
from .tool_summaries import (
    extract_tool_summary,
    fcf_summary,
    research_divergence,
    scenario_summary,
)
from .tool_summaries import (
    senti_summary as sentiment_summary,
)
from .types import AnalyzerRuntime
from .utils import (
    is_partial as _is_partial,
)
from .utils import (
    parse_skeleton_json as _parse_skeleton_json,
)
from .utils import (
    partial as _partial,
)
from .utils import (
    run_peer_compare as _run_peer_compare,
)
from .utils import (
    run_safe as _run_safe,
)

logger = logging.getLogger(__name__)

MAX_TOTAL_SEC = 720  # 12 min (采集+ANALYSIS 210s + COMPILE 480s + buffer)

# 各阶段 LLM 调用超时
ANALYSIS_TIMEOUT = 210
COMPILE_TIMEOUT = 480
SELF_CHECK_TIMEOUT = 5  # 程序化校验,秒级(纯 Python,0 LLM)


@dataclasses.dataclass
class PipelineContext:
    phase_results: dict[str, dict[str, object]] = dataclasses.field(default_factory=dict)
    partial_phases: list[str] = dataclasses.field(default_factory=list)
    final_markdown: str = ""
    system_tables_md: str = ""
    skeleton: dict | None = None

    def to_dict(self) -> dict[str, object]:
        # 把系统预渲染表(财务时序/同行对比/估值/FCFE/情景/舆情/三表)追加为「数据附录」。
        md = self.final_markdown or ""
        if self.system_tables_md.strip():
            appendix = "\n\n## 数据附录(系统预渲染)\n\n" + self.system_tables_md
            if appendix.strip() not in md:
                md = md + appendix
        return {
            "final_markdown": md,
            "system_tables_md": self.system_tables_md,
            "phase_results": self.phase_results,
            "partial_phases": self.partial_phases,
            "skeleton": self.skeleton,
        }


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
            await self._llm.aclose()

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
                    "ANALYSIS 阶段未产出骨架,以下为系统预渲染数据。\n\n"
                    + ctx.system_tables_md
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

    # ---- Phase 1: ANALYSIS ------------------------------------------------

    async def _phase_analysis(
        self,
        si: StockAnalysis,
        stock_md: str,
        prior: dict,
        reports: dict | None,
        financial_md_path: Path | None,
    ) -> dict:
        """Phase 1: 并行工具 + LLM 输出 JSON 骨架 + (可选) 程序化自检。"""
        from ..web_search_tool import execute_web_search

        quote = si.quote or StockQuote()

        # 1. 并行跑 5 个工具(批1)
        tech_coro = _run_safe(
            TOOL_RUNNERS["technicals"], getattr(si, "kline", None),
            getattr(si, "capital_flow", None),
        )
        fin_coro = _run_safe(
            TOOL_RUNNERS["financial_temporal"], getattr(si, "history_financial", None),
        )
        moat_coro = _run_safe(
            TOOL_RUNNERS["business_moat"],
            getattr(si, "financial", None),
            getattr(si, "research", None),
            getattr(si, "social_posts", None),
            getattr(si, "history_financial", None),
        )
        peer_coro = _run_peer_compare(si, execute_web_search)
        senti_coro = _run_safe(TOOL_RUNNERS["sentiment"], getattr(si, "social_posts", None))
        with logphase("tools(tech+fin+moat+peer+senti)"):
            tech, fin, moat, peer, senti = await asyncio.gather(
                tech_coro, fin_coro, moat_coro, peer_coro, senti_coro,
            )

        # 2. 批2: risk + valuation + fcf 并行; scenario 在 val 完成后立即启动
        with logphase("tools(risk+val+fcf+scenario)"):
            risk_task = asyncio.create_task(
                _run_safe(TOOL_RUNNERS["risk_quant"], fin, quote)
            )
            val_task = asyncio.create_task(
                _run_safe(TOOL_RUNNERS["valuation"], fin, quote, peer)
            )
            fcf_task = asyncio.create_task(
                _run_safe(
                    TOOL_RUNNERS["fcf_dividend"],
                    fin, getattr(si, "financial", None), quote,
                )
            )
            val = await val_task
            scenario_task = asyncio.create_task(
                _run_safe(TOOL_RUNNERS["scenario_prob"], val, quote, fin)
            )
            risk = await risk_task
            fcf = await fcf_task
            scenario = await scenario_task

        tool_results = {
            "technicals": tech, "financial_temporal": fin, "peer_compare": peer,
            "risk_quant": risk, "valuation": val, "business_moat": moat,
            "sentiment": senti, "fcf_dividend": fcf, "scenario_prob": scenario,
        }
        partial = any(_is_partial(v) for v in tool_results.values())

        # 3. 渲染核心表格(Python 模板,0 LLM token)+ 工具摘要
        tables_md = "\n\n".join(
            [
                render_financial_temporal(fin),
                render_peer_comparison(peer),
                render_valuation_targets(val),
                render_fcf_dividend(fcf),
                render_scenario_prob(scenario),
                render_sentiment(senti),
                render_financial_health_ext(getattr(si, "financial", None)),
            ]
        )
        prior["tables_md"] = tables_md
        summary = extract_tool_summary(tool_results)
        tool_ctx: dict[str, object] = {
            **prior, "tables_md": tables_md, "summary": summary,
        }
        prior["summary"] = summary
        system = phase_system_prompt(Phase.ANALYSIS, stock_md, tool_ctx)

        # 舆情/研报/工具摘要(供模型直接引用)
        social_summary = "\n".join(
            f"- {p.title[:60]}" for p in si.social_posts[:15]
        )
        research_summary = "\n".join(
            f"- {r.title[:50]} [{r.rating}]"
            for r in ((si.research or ResearchReportData()).reports or [])[:5]
        )
        research_div = research_divergence(si)
        senti_summary_txt = sentiment_summary(senti)
        fcf_summary_txt = fcf_summary(fcf)
        scenario_summary_txt = scenario_summary(scenario)

        user_content = (
            f"{stock_md}\n\n"
            f"# 已渲染表格\n{tables_md}\n\n"
            f"# 工具摘要\n{summary}\n\n"
            f"# 已采集社交媒体舆情(共 {len(si.social_posts)} 条)\n{social_summary}\n"
            f"{senti_summary_txt}\n\n"
            f"# 自由现金流与股息(系统计算)\n{fcf_summary_txt}\n\n"
            f"# 情景概率与风险收益比(系统计算)\n{scenario_summary_txt}\n\n"
            f"# 已采集机构研报(共 "
            f"{(si.research or ResearchReportData()).total_count} 篇)\n{research_summary}\n"
            f"{research_div}\n\n"
            f"# 输出要求\n请输出结构化 JSON 骨架(放在 ```json 代码块内),"
            f"包含全部推理结论。骨架字段结构见系统提示。"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

        # 短期跨 run 缓存(缓存骨架 JSON 文本)
        from ..cache import get_analysis_cache, set_analysis_cache

        cached_skeleton = get_analysis_cache(si.symbol) or ""
        skeleton_text = cached_skeleton
        if not skeleton_text:
            settings = get_settings()
            analysis_effort = settings.deepseek_analysis_effort
            analysis_max_tokens = settings.deepseek_analysis_max_tokens
            for _attempt in range(3):
                try:
                    message = await self._call_llm_with_stream(
                        messages,
                        max_tokens=analysis_max_tokens,
                        reasoning_effort=analysis_effort,
                    )
                except (httpx.TransportError, httpx.HTTPStatusError, OSError, TimeoutError) as e:
                    logger.error(
                        "[pipeline] ANALYSIS LLM 传输异常 %s: %s (重试 %d/3)",
                        type(e).__name__, e, _attempt + 1,
                    )
                    skeleton_text = ""
                    if _attempt < 2:
                        await asyncio.sleep(1)
                    continue
                skeleton_text = (message.get("content") or "").strip()
                break
            if skeleton_text:
                set_analysis_cache(si.symbol, skeleton_text)
        if skeleton_text:
            logger.info(
                "[pipeline] ANALYSIS skeleton len=%d%s",
                len(skeleton_text),
                " (cache HIT)" if cached_skeleton else "",
            )
        if not skeleton_text:
            logger.warning("[pipeline] ANALYSIS 输出为空(重试 %d 次后),标记降级", 3)
            return {
                "output": "", "tool_results": tool_results,
                "partial": True, "checks": {}, "empty_analysis": True,
            }

        # 解析 JSON 骨架
        skeleton = _parse_skeleton_json(skeleton_text)
        if skeleton is None:
            logger.warning("[pipeline] 骨架 JSON 解析失败,标记降级")
            return {
                "output": skeleton_text, "tool_results": tool_results,
                "partial": True, "checks": {},
                "empty_analysis": True,
            }

        prior["skeleton"] = skeleton
        logger.info("[pipeline] 骨架解析成功,字段数=%d", len(skeleton))

        return {
            "output": skeleton_text, "tool_results": tool_results,
            "partial": partial, "checks": {},
        }

    # ---- Phase 1.5: SELF_CHECK (programmatic, 0 LLM) --------------------

    async def _phase_self_check(
        self, skeleton: dict, tables_md: str = "",
    ) -> dict[str, object]:
        """Programmatic skeleton validation — 0 LLM, pure Python.

        Returns ``{"passed": bool, "fixes_needed": list[str]}``.
        Never raises — on any failure defaults to passed.
        """
        result = validate_skeleton(skeleton, tables_md)
        logger.info(
            "[pipeline] SELF_CHECK passed=%s fixes=%d",
            result.get("passed"), len(result.get("fixes_needed", [])),
        )
        return result

    # ---- Phase 2: COMPILE ------------------------------------------------

    async def _phase_compile(self, stock_md: str, prior: dict) -> dict:
        """Phase 2: 基于骨架扩写终稿。

        使用流式调用(reasoning_effort=medium):ANALYSIS 阶段已完成深度推理,
        终稿阶段只做扩写/格式化,无需再次深邃推理。
        骨架 JSON 通过 {{ skeleton }} 占位符注入 system prompt。
        """
        skeleton = prior.get("skeleton") or {}
        if not skeleton:
            return {"output": "", "tool_results": {}, "partial": True, "reason": "no_skeleton"}
        system = phase_system_prompt(Phase.COMPILE, stock_md, prior)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                "# 推理骨架(JSON)\n\n"
                f"```json\n{json.dumps(skeleton, ensure_ascii=False, indent=2)}\n```\n\n"
                "请基于上方骨架和系统提示中的数据,扩写为完整的投资策略报告。"
            )},
        ]
        text = ""
        try:
            for _attempt in range(2):
                try:
                    text = await self._stream_llm_content(messages, reasoning_effort="medium")
                except (httpx.TransportError, httpx.HTTPStatusError, OSError, TimeoutError) as e:
                    logger.error(
                        "[pipeline] COMPILE 传输异常 %s: %s (重试 %d/2)",
                        type(e).__name__, e, _attempt + 1,
                    )
                    text = ""
                    if _attempt < 1:
                        await asyncio.sleep(1)
                    continue
                text = (text or "").strip()
                break
        except Exception as e:
            logger.warning("[pipeline] COMPILE 调用失败 %s: %s", type(e).__name__, e)
            text = ""
        if not text:
            logger.warning("[pipeline] COMPILE 输出为空,标记降级")
            return {"output": "", "tool_results": {}, "partial": True, "reason": "empty_compile"}
        stripped = strip_xml_tool_calls(text)
        return {"output": stripped, "tool_results": {}, "partial": False}

    # ---- LLM transport delegates (kept as methods so tests can monkeypatch) ----

    async def _call_llm_with_stream(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str = "max",
    ) -> dict:
        return await self._llm.call_llm_with_stream(
            messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort,
        )

    async def _stream_llm_content(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str = "high",
    ) -> str:
        return await self._llm.stream_llm_content(
            messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort,
        )
