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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
from .report_reconciler import reconcile
from .self_check_rewrite import self_check_rewrite
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
DIRECT_TIMEOUT = 600    # 直出模式: 工具采集 + 一次长文 LLM,给足预算(< 720 硬顶)


@dataclasses.dataclass
class _ToolContext:
    """工具采集 + 表格渲染 + 摘要的共享产物(skeleton 与 direct 两条流复用)。"""

    tool_results: dict[str, object]
    partial: bool
    tables_md: str
    summary: str
    body: str  # user 消息主体(不含各阶段各自的「# 输出要求」尾巴)


@dataclasses.dataclass
class PipelineContext:
    phase_results: dict[str, dict[str, object]] = dataclasses.field(default_factory=dict)
    partial_phases: list[str] = dataclasses.field(default_factory=list)
    final_markdown: str = ""
    system_tables_md: str = ""
    skeleton: dict | None = None
    credibility: dict[str, object] = dataclasses.field(default_factory=dict)

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
            "credibility": self.credibility,
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

    # ---- 共享: 工具采集 + 表格 + 摘要 (skeleton 与 direct 复用) ----------

    async def _gather_tool_context(
        self, si: StockAnalysis, stock_md: str, prior: dict,
    ) -> _ToolContext:
        """并行跑 9 个工具 → 0-LLM 渲染表格 + 摘要 → 拼 user 消息主体。

        无任何 LLM 调用。产物同时供 ANALYSIS(骨架)与 DIRECT(直出)两条流。
        会写入 ``prior['tables_md']`` / ``prior['summary']``。
        """
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
                _run_safe(
                    TOOL_RUNNERS["valuation"],
                    fin,
                    quote,
                    peer,
                    getattr(si, "financial", None),
                )
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

        tool_results: dict[str, object] = {
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
        prior["summary"] = summary

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

        body = (
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
        )
        return _ToolContext(
            tool_results=tool_results, partial=partial,
            tables_md=tables_md, summary=summary, body=body,
        )

    async def _gather_catalysts(self, si: StockAnalysis) -> str:
        """尝试拉取近期催化并注入 DIRECT user message(实时检索)。

        仅在 ``direct_web_search_enabled=True`` 时由调用方触发。任何失败都安全
        降级为空串(不注入),绝不影响报告生成。
        """
        try:
            from ..web_search_tool import execute_web_search

            name = getattr(si, "name", "") or getattr(si, "symbol", "")
            query = f"{name} 最新 催化 利好 利空 公告 研报"
            raw = await execute_web_search(query, max_results=5)
            if raw and "搜索失败" not in raw:
                return f"# 实时检索到的近期催化(网络)\n{raw}\n\n"
        except Exception as e:  # noqa: BLE001 - 安全降级
            logger.debug("[pipeline] 实时检索催化失败(安全跳过): %s", e)
        return ""

    # ---- DIRECT 流: 工具 + 一次 LLM 直出完整报告 (无骨架、无扩写) --------

    async def _run_direct(
        self, si: StockAnalysis, stock_md: str, prior: dict, ctx: PipelineContext,
    ) -> dict[str, object]:
        """直出流入口: 采集 → 一次 LLM 直出完整报告 → 填充 ctx。"""
        try:
            result = await asyncio.wait_for(
                self._phase_direct(si, stock_md, prior),
                timeout=DIRECT_TIMEOUT,
            )
        except TimeoutError:
            logger.warning("[pipeline] DIRECT 超时 %ds, 降级", DIRECT_TIMEOUT)
            result = _partial("timeout")
        except Exception as e:
            logger.error(
                "[pipeline] DIRECT 异常 %s: %s\n%s",
                type(e).__name__, e, traceback.format_exc(),
            )
            result = _partial(f"{type(e).__name__}")

        ctx.phase_results[Phase.DIRECT.value] = result
        # 透传可信度摘要到报告上下文(Task 8 将渲染 credibility 页脚)
        ctx.credibility = result.get("credibility") or {}
        ctx.system_tables_md = str(prior.get("tables_md") or "")
        text = str(result.get("output") or "")
        if text:
            ctx.final_markdown = text
            if result.get("partial"):
                ctx.partial_phases.append(Phase.DIRECT.value)
        else:
            # 降级: 无 LLM 产出时用系统预渲染表格兜底(0 LLM)
            ctx.partial_phases.append(Phase.DIRECT.value)
            if ctx.system_tables_md.strip():
                ctx.final_markdown = (
                    "# 分析报告（降级）\n\n"
                    "AI 直出阶段未产出正文,以下为系统预渲染数据。\n\n"
                    + ctx.system_tables_md
                )
            else:
                ctx.final_markdown = "# 分析报告（降级）\n\n数据采集或分析暂不可用。"
        return ctx.to_dict()

    async def _phase_direct(
        self, si: StockAnalysis, stock_md: str, prior: dict,
    ) -> dict:
        """采集工具上下文后,一次流式 LLM 调用直出完整 Markdown 报告。

        使用 ``deepseek_analysis_effort`` 思考强度 + ``deepseek_max_tokens``
        输出上限(长文需要充足额度)。不产出 JSON 骨架,不做二次扩写。

        直出完成后做 0-LLM 数字对账 + 可选 LLM 定点重写(非阻断护栏),
        并把可信度摘要随结果透传。
        """
        tc = await self._gather_tool_context(si, stock_md, prior)

        # 可选: 实时检索近期催化注入(默认关,开启才有行为变化)
        user_body = tc.body
        settings = get_settings()
        if settings.direct_web_search_enabled:
            catalyst = await self._gather_catalysts(si)
            if catalyst:
                user_body = catalyst + user_body

        system = phase_system_prompt(Phase.DIRECT, stock_md, {**prior})
        user_content = (
            user_body
            + "# 输出要求\n直接输出完整、深入的投资分析报告(Markdown),"
            + "严格按系统提示的 8 节结构,每一节充分论述。不要输出 JSON、不要输出骨架。"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

        effort = settings.deepseek_analysis_effort
        text = ""
        for _attempt in range(2):
            try:
                text = await self._stream_llm_content(
                    messages,
                    max_tokens=settings.deepseek_max_tokens,
                    reasoning_effort=effort,
                    thinking=True,
                )
            except (httpx.TransportError, httpx.HTTPStatusError, OSError, TimeoutError) as e:
                logger.error(
                    "[pipeline] DIRECT 传输异常 %s: %s (重试 %d/2)",
                    type(e).__name__, e, _attempt + 1,
                )
                text = ""
                if _attempt < 1:
                    await asyncio.sleep(1)
                continue
            text = (text or "").strip()
            break

        if not text:
            logger.warning("[pipeline] DIRECT 输出为空,标记降级")
            return {"output": "", "tool_results": tc.tool_results, "partial": True}

        stripped = strip_xml_tool_calls(text)

        # 数字对账 + 定点重写(非阻断护栏): 任何异常都保底返回原文。
        facts = _build_assertable_facts(tc)
        llm = self._make_rewrite_llm()
        fixed_text, credibility_summary = _verify_and_fix(stripped, facts, llm=llm)

        return {
            "output": fixed_text,
            "tool_results": tc.tool_results,
            "partial": tc.partial,
            "credibility": credibility_summary,
        }

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
        tc = await self._gather_tool_context(si, stock_md, prior)
        tool_results = tc.tool_results
        partial = tc.partial
        system = phase_system_prompt(Phase.ANALYSIS, stock_md, {**prior})
        user_content = (
            tc.body
            + "# 输出要求\n请输出结构化 JSON 骨架(放在 ```json 代码块内),"
            + "包含全部推理结论。骨架字段结构见系统提示。"
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

        纯扩写/格式化,不做二次推理 —— 关闭思考模式(thinking=disabled)以省下
        全部思考 token(思考 token 按输出计价,是主要成本)。关闭后 temperature
        恢复生效(默认 0.3,保证行文自然)。骨架 JSON 通过 {{ skeleton }} 占位符
        注入 system prompt。
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
                    text = await self._stream_llm_content(messages, thinking=False)
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
        reasoning_effort: str = "high",
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


def _is_number(v: object) -> bool:
    """安全判断是否为可用数字(排除 bool,因其是 int 子类)。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _build_assertable_facts(tool_ctx: _ToolContext) -> dict[str, object]:
    """从 ``_ToolContext.tool_results`` 抽『可断言事实』(基准单位),供数字对账。

    只读真实存在的字段;取不到的跳过(不硬编码不存在的字段)。

    单位约定(对齐 report_reconciler 的 facts 语义):
      - pe_ttm / pb : 比率原值(来自 valuation 工具的 quote.pe / quote.pb)
      - roe         : 百分数原值(financial_temporal 以小数存储,×100)
      - revenue     : 元(financial_temporal.years[0].revenue)
      - target_base : 元(valuation.fcfe_targets.neutral.price)

    注: 当前 ``_ToolContext`` 不含 quote 实体,现价 ``price`` 无法抽取,故跳过。
    """
    facts: dict[str, object] = {}
    tr = tool_ctx.tool_results
    if not isinstance(tr, dict):
        return facts

    valuation = tr.get("valuation")
    if isinstance(valuation, dict):
        pe = valuation.get("pe")
        if _is_number(pe):
            facts["pe_ttm"] = float(pe)  # type: ignore[arg-type]
        pb = valuation.get("pb")
        if _is_number(pb):
            facts["pb"] = float(pb)  # type: ignore[arg-type]
        fcfe = valuation.get("fcfe_targets") or {}
        if isinstance(fcfe, dict):
            neutral = fcfe.get("neutral") or {}
            tgt = neutral.get("price") if isinstance(neutral, dict) else None
            if _is_number(tgt):
                facts["target_base"] = float(tgt)  # type: ignore[arg-type]

    fin = tr.get("financial_temporal")
    if isinstance(fin, dict):
        roe_trend = fin.get("roe_trend")
        if isinstance(roe_trend, list) and roe_trend:
            latest_roe = roe_trend[0]
            if _is_number(latest_roe):
                facts["roe"] = float(latest_roe) * 100.0
        years = fin.get("years")
        if isinstance(years, list) and years:
            latest_year = years[0]
            if isinstance(latest_year, dict):
                rev = latest_year.get("revenue")
                if _is_number(rev):
                    facts["revenue"] = float(rev)  # type: ignore[arg-type]

    return facts


def _run_rewrite_llm(client: PipelineLlmClient, system: str, user: str) -> str:
    """同步执行一次非流式 LLM 调用(thinking=False),用于定点重写。

    在独立线程里跑新事件循环 ``asyncio.run``,既不阻塞主事件循环、也不与主循环
    的 ``run_until_complete`` 冲突。任何异常返回空串,交给 ``self_check_rewrite``
    的安全护栏决定保留原文。
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    def _work() -> str:
        async def _go() -> str:
            msg = await client.call_llm_with_stream(messages, thinking=False)
            return (msg.get("content") or "").strip()

        try:
            return asyncio.run(_go())
        except Exception as e:  # noqa: BLE001 - 安全降级返回空串
            logger.warning("[verify] rewrite llm 异常 %s: %s", type(e).__name__, e)
            return ""

    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_work).result()


def _verify_and_fix(
    report_md: str,
    facts: dict[str, object],
    *,
    llm: Callable[[str, str], str],
) -> tuple[str, dict[str, object]]:
    """0-LLM 数字对账 + 可选 LLM 定点重写。全程 try/except,绝不阻断报告。

    返回 ``(最终报告文本, 可信度摘要)``。任何异常都保底返回原报告文本 + 跳过标记。
    """
    try:
        if not get_settings().reconcile_enabled:
            return (report_md, {"skipped": "reconcile disabled"})

        res = reconcile(report_md, facts)
        summary: dict[str, object] = {
            "checked": res.checked, "corrected": 0, "uncertain": [],
        }

        if res.mismatches and get_settings().self_check_rewrite_enabled:
            fixed = self_check_rewrite(report_md, res.mismatches, facts, llm=llm)
            actually_fixed = sum(1 for m in res.mismatches if m.snippet not in fixed)
            summary["corrected"] = actually_fixed
            report_md = fixed
        elif res.mismatches:
            summary["uncertain"] = [m.snippet for m in res.mismatches]

        return (report_md, summary)
    except Exception as e:  # noqa: BLE001 - 任何异常都保底返回原报告
        logger.warning("[verify] _verify_and_fix 异常 %s: %s", type(e).__name__, e)
        return (report_md, {"skipped": "verify crashed"})
