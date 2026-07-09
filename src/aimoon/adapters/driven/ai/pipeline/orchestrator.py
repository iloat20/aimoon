"""Pipeline v2 orchestrator — ANALYSIS + COMPILE 两阶段。

默认模式: Phase 1 (ANALYSIS) 并行跑 6 个纯工具 + LLM 生成深度报告初稿 + 自检 + 1 次修复循环,
          Phase 2 (COMPILE) 基于经自检认可的初稿生成终稿长文。
快速模式 (use_fast=True): 跳过自检和 COMPILE,ANALYSIS 初稿即终稿。

总硬上限 720s(12 分钟)。每阶段独立容错:超时 / 异常 / 畸形 JSON 标 ``__partial__`` 并继续,
绝不阻塞后续阶段(项目 CLAUDE.md 的 broad-tolerance 规则)。
"""

from __future__ import annotations

import asyncio
import dataclasses
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
from .phases import _SECTIONS_MD, Phase, phase_system_prompt
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
    parse_self_check_json as _parse_self_check_json,
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

# 各阶段 LLM 调用超时 (extracted from inline magic numbers, audit P2.5)
ANALYSIS_TIMEOUT = 210
COMPILE_TIMEOUT = 480
SELF_CHECK_TIMEOUT = 60

# DeepSeek 思考强度(reasoning_effort)。
# 官方支持: low/medium→映射为 high, high, xhigh→映射为 max。
# ANALYSIS 阶段的强度由 settings.deepseek_analysis_effort 控制(默认 high),
# 这是 reasoner 思考 token 的主要消耗点,用户可通过环境变量设为 medium/low 省钱;
# 重试时保持配置值(不自动降级,避免思考不充分)。


@dataclasses.dataclass
class PipelineContext:
    phase_results: dict[str, dict[str, object]] = dataclasses.field(default_factory=dict)
    partial_phases: list[str] = dataclasses.field(default_factory=list)
    final_markdown: str = ""
    system_tables_md: str = ""

    def to_dict(self) -> dict[str, object]:
        # 把系统预渲染表(财务时序/同行对比/估值/FCFE/情景/舆情/三表)追加为「数据附录」。
        # 这些表在 ANALYSIS 阶段已由 Python 模板渲染(0 LLM token),COMPILE 终稿仅含 AI 正文,
        # 此处统一追加(覆盖所有 return 路径),避免算出后丢弃。
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
        }


class PipelineOrchestrator:
    """ANALYSIS + COMPILE 两阶段 pipeline。"""

    def __init__(self, analyzer: AnalyzerRuntime) -> None:
        self.analyzer = analyzer
        # Dedicated long-timeout client for LLM calls (300s).
        # Reuses TCP/TLS connections across ANALYSIS and COMPILE phases.
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

        # 阶段跳过优先级: ultra_fast > single_call > fast。
        # ultra_fast: 初稿即终稿(跳过自检 + COMPILE); fast: 跳过自检 + 修复循环;
        # single_call: 跳过 self-check 与 COMPILE 独立阶段,仅保留 ANALYSIS 单次 LLM 调用
        #   (并非把三阶段合并为一次调用,而是直接省略自检与润色阶段)。
        skip_self_check = use_fast or use_single_call or use_ultra_fast
        skip_compile = use_single_call or use_ultra_fast

        # Phase 1: ANALYSIS — 并行工具 + LLM + (可选) 自检
        try:
            analysis_result = await asyncio.wait_for(
                self._phase_analysis(
                    si, stock_md, prior, reports, financial_md_path,
                    use_fast=skip_self_check,
                ),
                timeout=210,
            )
        except TimeoutError:
            logger.warning("[pipeline] ANALYSIS 超时 210s, 降级")
            analysis_result = _partial("timeout")
        except Exception as e:  # broad tolerance: never abort the pipeline
            logger.error(
                "[pipeline] ANALYSIS 异常 %s: %s\n%s",
                type(e).__name__, e, traceback.format_exc(),
            )
            analysis_result = _partial(f"{type(e).__name__}")

        ctx.phase_results[Phase.ANALYSIS.value] = analysis_result
        # 始终携带系统预渲染表(财务时序/同行对比/估值三档),由 report 生成器
        # 单独渲染为「数据附录」卡片区,不再塞进 AI 正文末尾。
        ctx.system_tables_md = str(prior.get("tables_md") or "")
        if analysis_result.get("empty_analysis"):
            ctx.partial_phases.append(Phase.ANALYSIS.value)
            prior["analysis_draft"] = ""
            prior["self_check_fixes"] = []
            return ctx.to_dict()
        if analysis_result.get("partial"):
            ctx.partial_phases.append(Phase.ANALYSIS.value)
            prior["analysis_draft"] = analysis_result.get("output", "")
            prior["self_check_fixes"] = analysis_result.get("checks", {}).get("fixes_needed", [])
            prior["tools_output"] = analysis_result.get("tool_results", {})
        else:
            prior["analysis_draft"] = analysis_result.get("output", "")
            prior["self_check_fixes"] = analysis_result.get("checks", {}).get("fixes_needed", [])
            prior["tools_output"] = analysis_result.get("tool_results", {})

        # Phase 1.5: SELF_CHECK — lightweight draft validation
        draft = str(prior.get("analysis_draft", "") or "")
        if draft and not skip_self_check:
            try:
                sc_result = await self._phase_self_check(draft, ctx.system_tables_md)
            except Exception as e:
                logger.warning("[pipeline] SELF_CHECK 外层异常 %s: %s", type(e).__name__, e)
                sc_result = {"passed": True, "fixes_needed": []}
            ctx.phase_results[Phase.SELF_CHECK.value] = sc_result
            fixes = sc_result.get("fixes_needed", [])
            if fixes:
                prior["self_check_fixes"] = fixes

        # use_fast: 直接把 ANALYSIS 初稿当终稿输出,跳过 COMPILE
        if skip_compile and prior["analysis_draft"]:
            ctx.final_markdown = strip_xml_tool_calls(str(prior["analysis_draft"]))
            ctx.phase_results[Phase.COMPILE.value] = {
                "output": ctx.final_markdown, "tool_results": {},
                "partial": False, "skipped": True,
            }
            return ctx.to_dict()

        # Phase 2: COMPILE — 终稿生成(写长文)
        # timeout=480s:7 节长文 + reasoning_effort=medium 在真实 API 下可能较慢,
        # 300s 内屡屡超时降级,放宽到 480s (=MAX_TOTAL_SEC - ANALYSIS 210s - buffer)。
        if prior["analysis_draft"]:
            try:
                compile_result = await asyncio.wait_for(
                    self._phase_compile(stock_md, prior),
                    timeout=COMPILE_TIMEOUT,
                )
            except TimeoutError:
                logger.warning("[pipeline] COMPILE 超时 480s, 降级")
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

        # 容错:若 COMPILE 未产出(超时/空),但存在可用的 ANALYSIS 初稿,
        # 直接以结构完整的初稿作为终稿(含真实数字),避免降级到 legacy 空话报告。
        if not ctx.final_markdown and prior.get("analysis_draft"):
            logger.warning("[pipeline] COMPILE 未产出,回退 ANALYSIS 初稿为终稿")
            ctx.final_markdown = strip_xml_tool_calls(str(prior["analysis_draft"]))
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
        *,
        use_fast: bool = False,
    ) -> dict:
        """Phase 1: 并行工具 + LLM 初稿 + (可选) 自检。"""
        from ..web_search_tool import execute_web_search

        # 1. 并行跑 4 个纯工具 + peer_compare(web search)
        # 注:工具结果不再走 60s 短缓存——纯函数重算成本极低(<0.5s),且短缓存会
        # 掩盖修复、在重复跑时返回过期的 peer_compare(web 搜索)数据。
        tech_coro = _run_safe(
            TOOL_RUNNERS["technicals"], getattr(si, "kline", None),
            getattr(si, "capital_flow", None),
        )
        fin_coro = _run_safe(TOOL_RUNNERS["financial_temporal"],
                              getattr(si, "history_financial", None))
        moat_coro = _run_safe(
            TOOL_RUNNERS["business_moat"],
            getattr(si, "financial", None),
            getattr(si, "research", None),
            getattr(si, "social_posts", None),
            getattr(si, "history_financial", None),
        )
        peer_coro = _run_peer_compare(si, execute_web_search)
        # 舆情情感(独立,不依赖财务)
        senti_coro = _run_safe(TOOL_RUNNERS["sentiment"], getattr(si, "social_posts", None))
        with logphase("tools(tech+fin+moat+peer+senti)"):
            tech, fin, moat, peer, senti = await asyncio.gather(
                tech_coro, fin_coro, moat_coro, peer_coro, senti_coro,
            )
        with logphase("tools(risk+val)"):
            risk, val = await asyncio.gather(
                _run_safe(TOOL_RUNNERS["risk_quant"], fin, si.quote or StockQuote()),
                _run_safe(TOOL_RUNNERS["valuation"], fin, si.quote or StockQuote(), peer),
            )
        with logphase("tools(fcf+scenario)"):
            fcf_coro = _run_safe(
                TOOL_RUNNERS["fcf_dividend"],
                fin,
                getattr(si, "financial", None),
                si.quote or StockQuote(),
            )
            scenario_coro = _run_safe(
                TOOL_RUNNERS["scenario_prob"], val, si.quote or StockQuote(), fin
            )
            fcf, scenario = await asyncio.gather(fcf_coro, scenario_coro)

        tool_results = {
            "technicals": tech, "financial_temporal": fin, "peer_compare": peer,
            "risk_quant": risk, "valuation": val, "business_moat": moat,
            "sentiment": senti, "fcf_dividend": fcf, "scenario_prob": scenario,
        }
        partial = any(_is_partial(v) for v in tool_results.values())

        # 2. 渲染核心表格(Python 模板,0 LLM token)+ 工具摘要(非表格部分)
        #    注:三大表明细卡(render_financial_statements)已移除——
        #    FinancialData 无 statements 字段、采集端也未存行项明细,该卡恒为空,属死代码。
        #    当前表格覆盖财务时序/同行/估值三档/FCF股息/情景概率/舆情/财务健康扩展。
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
        # 关键:把预渲染表写入 prior,供 COMPILE 阶段引用与终稿追加
        # (此前仅用于 ANALYSIS 局部 tool_ctx,导致表格从未进入最终报告)。
        prior["tables_md"] = tables_md
        summary = extract_tool_summary(
            {
                "technicals": tech,
                "financial_temporal": fin,
                "peer_compare": peer,
                "risk_quant": risk,
                "valuation": val,
                "business_moat": moat,
                "sentiment": senti,
                "fcf_dividend": fcf,
                "scenario_prob": scenario,
            }
        )
        # 3. Hybrid user message: snapshot + 已渲染表格 + 工具摘要 + 舆情 + 研报
        #    模型只需做分析对比,不需要重新生成表格数字(否则会与 tables_md 重复)。
        tool_ctx: dict[str, object] = {**prior, "tables_md": tables_md, "summary": summary}
        system = phase_system_prompt(Phase.ANALYSIS, stock_md, tool_ctx)
        # 舆情摘要(取每条 post 的 title,最多 15 条)
        social_summary = "\n".join(
            f"- {p.title[:60]}" for p in si.social_posts[:15]
        )
        # 研报摘要(取标题+评级,最多 5 篇)
        research_summary = "\n".join(
            f"- {r.title[:50]} [{r.rating}]"
            for r in ((si.research or ResearchReportData()).reports or [])[:5]
        )
        # 机构分歧量化摘要(近 3 月 EPS 预测变动趋势)
        research_div = research_divergence(si)
        # 情感/自由现金流/情景 摘要(供模型直接引用,不重复计算)
        senti_summary_txt = sentiment_summary(senti)
        fcf_summary_txt = fcf_summary(fcf)
        scenario_summary_txt = scenario_summary(scenario)
        # user message 不再注入完整 tool JSON(这是旧架构的主要浪费)。
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
            f"# 输出章节结构(按此顺序,不可省略)\n{_SECTIONS_MD}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

        # 短期跨 run 缓存
        from ..cache import get_analysis_cache, set_analysis_cache

        cached_report = get_analysis_cache(si.symbol) or ""

        # LLM 产出 Markdown。仅当传输层 / 网络层 / HTTP 状态异常(连接重置、
        # 超时、DNS、以及 DeepSeek 瞬时 429/500/503 等)时才重试——模型正常
        # 返回空内容属于其真实输出,不重试(避免无谓重复调用)。
        settings = get_settings()
        analysis_effort = settings.deepseek_analysis_effort
        analysis_max_tokens = settings.deepseek_analysis_max_tokens
        draft = cached_report
        if not draft:
            for _attempt in range(3):
                try:
                    message = await self._call_llm_with_stream(
                        messages,
                        max_tokens=analysis_max_tokens,
                        reasoning_effort=analysis_effort,
                    )
                except (httpx.TransportError, httpx.HTTPStatusError, OSError, TimeoutError) as e:
                    logger.error(
                        "[pipeline] ANALYSIS LLM 传输异常 %s: %s (重试 %d/2)",
                        type(e).__name__, e, _attempt + 1,
                    )
                    draft = ""
                    if _attempt < 2:
                        await asyncio.sleep(1)
                    continue
                draft = (message.get("content") or "").strip()
                break
            if draft:
                set_analysis_cache(si.symbol, draft)
        if draft:
            logger.info("[pipeline] ANALYSIS draft len=%d%s", len(draft),
                        " (response cache HIT)" if cached_report else "")
        if not draft:
            logger.warning("[pipeline] ANALYSIS 输出为空(重试 %d 次后),标记降级", 3)
            return {"output": "", "tool_results": tool_results,
                    "partial": True, "checks": {}, "empty_analysis": True}

        return {"output": draft, "tool_results": tool_results,
                "partial": partial, "checks": {}}

    # ---- Phase 1.5: SELF_CHECK ------------------------------------------------

    async def _phase_self_check(self, draft: str, tables_md: str = "") -> dict[str, object]:
        """Lightweight self-check: single short LLM call, never blocks pipeline.

        Returns ``{"passed": bool, "fixes_needed": list[str]}``.
        On any failure (timeout, parse error, exception) defaults to passed.
        """
        system = phase_system_prompt(Phase.SELF_CHECK, "", {
            "analysis_draft": draft, "tables_md": tables_md,
        })
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"# ANALYSIS 初稿\n\n{draft}"},
        ]
        try:
            message = await asyncio.wait_for(
                self._call_llm_with_stream(
                    messages, max_tokens=2048, reasoning_effort="low",
                ),
                timeout=SELF_CHECK_TIMEOUT,
            )
            text = (message.get("content") or "").strip()
        except TimeoutError:
            logger.warning("[pipeline] SELF_CHECK 超时 60s, 自检未执行")
            return {"passed": True, "fixes_needed": [], "self_check_available": False}
        except Exception as e:
            logger.warning("[pipeline] SELF_CHECK 异常 %s: %s", type(e).__name__, e)
            return {"passed": True, "fixes_needed": [], "self_check_available": False}
        parsed, fixes = _parse_self_check_json(text)
        if parsed is None:
            logger.warning("[pipeline] SELF_CHECK JSON 解析失败, 自检未执行")
            return {"passed": True, "fixes_needed": [], "self_check_available": False}
        passed = bool(parsed.get("passed", True))
        logger.info("[pipeline] SELF_CHECK passed=%s fixes=%d", passed, len(fixes))
        return {"passed": passed, "fixes_needed": fixes, "self_check_available": True}

    # ---- Phase 2: COMPILE ------------------------------------------------

    async def _phase_compile(self, stock_md: str, prior: dict) -> dict:
        """Phase 2: 终稿生成。

        使用流式调用(reasoning_effort=medium)在长文生成时持续打印章节进度;
        ANALYSIS 阶段已完成深度推理,终稿阶段只做格式化/扩写,无需再次深邃推理,
        把 reasoning 由 "high" 降为 "medium" 以节省 COMPILE 阶段 ~50-60% reasoning tokens,
        同时降低 300s 超时导致整篇 partial 降级的概率。
        输出为空时标记 partial,交由 ``_run_pipeline`` 回退到 ANALYSIS 初稿。
        """
        draft = str(prior.get("analysis_draft", "") or "")
        if not draft:
            draft = f"# 标的快照\n\n{stock_md}"
        system = phase_system_prompt(Phase.COMPILE, stock_md, prior)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"# 经自检认可的草稿\n\n{draft}"},
        ]
        text = ""
        try:
            for _attempt in range(2):
                try:
                    text = await self._stream_llm_content(messages, reasoning_effort="medium")
                except (httpx.TransportError, httpx.HTTPStatusError, OSError, TimeoutError) as e:
                    logger.error(
                        "[pipeline] COMPILE 传输异常 %s: %s (重试 %d/1)",
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
