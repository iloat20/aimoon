"""Pipeline v2 orchestrator — ANALYSIS + COMPILE 两阶段。

默认模式: Phase 1 (ANALYSIS) 并行跑 6 个纯工具 + LLM 生成深度报告初稿 + 自检 + 1 次修复循环,
          Phase 2 (COMPILE) 基于经自检认可的初稿生成终稿长文。
快速模式 (use_fast=True): 跳过自检和 COMPILE,ANALYSIS 初稿即终稿。

总硬上限 540s(9 分钟)。每阶段独立容错:超时 / 异常 / 畸形 JSON 标 ``__partial__`` 并继续,
绝不阻塞后续阶段(项目 CLAUDE.md 的 broad-tolerance 规则)。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from pathlib import Path
from typing import Any, Protocol

import httpx

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

from ..tools import TOOL_RUNNERS
from ..xml_utils import strip_xml_tool_calls
from .phases import _SECTIONS_MD, Phase, phase_system_prompt
from .table_renderer import (
    render_fcf_dividend,
    render_financial_statements,
    render_financial_temporal,
    render_peer_comparison,
    render_scenario_prob,
    render_sentiment,
    render_valuation_targets,
)
from .timing import logphase

logger = logging.getLogger(__name__)

MAX_TOTAL_SEC = 540  # 9 min (240 ANALYSIS + 300 COMPILE + buffer)

# DeepSeek 思考强度(reasoning_effort)。
# 官方支持: low/medium→映射为 high, high, xhigh→映射为 max。
# 默认 high;重试时保持 high(不降级,避免思考不充分)。
_EFFORT_TWO_PHASE = "high"  # 双阶段模式(重试时仍用 high)

# 报告章节结构 —— 代码侧维护,运行时格式化为 `## 一、…## 八、…` 标题块。
# 替换 system 模板占位符 `{{ sections }}` 的同时,作为 sections_list 注入
# 到 user message,让模型直接按自检 JSON 前的自然章节顺序输出。
# 报告章节结构(已迁移至 phases.py 的 _SECTIONS_MD,此处保留导入)



class AnalyzerRuntime(Protocol):
    _settings: Any
    _provided_settings: Any | None
    _http: Any
    api_url: str
    api_key: str

    def _build_data_dict(self, info: StockAnalysis, reports: dict | None = None,
                         financial_md_path: Path | None = None) -> dict[str, Any]: ...
    async def _stream_final_response(self, messages: list[dict]) -> str: ...


@dataclasses.dataclass
class PipelineContext:
    phase_results: dict[str, dict[str, object]] = dataclasses.field(default_factory=dict)
    partial_phases: list[str] = dataclasses.field(default_factory=list)
    final_markdown: str = ""
    system_tables_md: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "final_markdown": self.final_markdown,
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
        import httpx as _httpx
        self._llm_http = _httpx.AsyncClient(timeout=300.0)

    async def run(self, si: StockAnalysis, *, reports: dict | None = None,
                  financial_md_path: Path | None = None,
                  use_fast: bool = False) -> dict[str, object]:
        try:
            return await self._run_pipeline(
                si, reports=reports, financial_md_path=financial_md_path,
                use_fast=use_fast,
            )
        finally:
            await self._llm_http.aclose()

    async def _run_pipeline(self, si: StockAnalysis, *, reports: dict | None = None,
                            financial_md_path: Path | None = None,
                            use_fast: bool = False) -> dict[str, object]:
        ctx = PipelineContext()
        stock_md = self._render_stock_context(si, reports, financial_md_path)
        prior: dict[str, object] = {}

        skip_self_check = use_fast
        skip_compile = use_fast

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
            import traceback as tb_mod
            logger.error(
                "[pipeline] ANALYSIS 异常 %s: %s\n%s",
                type(e).__name__, e, tb_mod.format_exc()
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
                sc_result = await self._phase_self_check(draft)
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
        if prior["analysis_draft"]:
            try:
                compile_result = await asyncio.wait_for(
                    self._phase_compile(stock_md, prior),
                    timeout=300,
                )
            except TimeoutError:
                logger.warning("[pipeline] COMPILE 超时 300s, 降级")
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

    async def _phase_analysis(self, si: StockAnalysis, stock_md: str, prior: dict,
                              reports: dict | None, financial_md_path: Path | None,
                              *, use_fast: bool = False) -> dict:
        """Phase 1: 并行工具 + LLM 初稿 + (可选) 自检。"""
        from ..web_search_tool import execute_web_search

        # 1. 并行跑 4 个纯工具 + peer_compare(web search)
        # 注:工具结果不再走 60s 短缓存——纯函数重算成本极低(<0.5s),且短缓存会
        # 掩盖修复、在重复跑时返回过期的 peer_compare(web 搜索)数据。
        tech_coro = _run_safe(TOOL_RUNNERS["technicals"], getattr(si, "kline", None),
                               getattr(si, "capital_flow", None))
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
                _run_safe(TOOL_RUNNERS["risk_quant"], fin, si.quote),
                _run_safe(TOOL_RUNNERS["valuation"], fin, si.quote, peer),
            )
        with logphase("tools(fcf+scenario)"):
            fcf_coro = _run_safe(
                TOOL_RUNNERS["fcf_dividend"], fin, getattr(si, "financial", None), si.quote
            )
            scenario_coro = _run_safe(TOOL_RUNNERS["scenario_prob"], val, si.quote, fin)
            fcf, scenario = await asyncio.gather(fcf_coro, scenario_coro)

        tool_results = {
            "technicals": tech, "financial_temporal": fin, "peer_compare": peer,
            "risk_quant": risk, "valuation": val, "business_moat": moat,
            "sentiment": senti, "fcf_dividend": fcf, "scenario_prob": scenario,
        }
        partial = any(_is_partial(v) for v in tool_results.values())

        # 2. 渲染核心表格(Python 模板,0 LLM token)+ 工具摘要(非表格部分)
        #    三大表(利润表/资产负债表/现金流量表)明细来自 si.financial.statements,
        #    旧实现只抽汇总数字丢弃明细,导致 AI 看不到三大表;现在一并渲染。
        tables_md = "\n\n".join(
            [
                render_financial_temporal(fin),
                render_peer_comparison(peer),
                render_valuation_targets(val),
                render_fcf_dividend(fcf),
                render_scenario_prob(scenario),
                render_sentiment(senti),
                render_financial_statements(getattr(si, "financial", None)),
            ]
        )
        # 关键:把预渲染表写入 prior,供 COMPILE 阶段引用与终稿追加
        # (此前仅用于 ANALYSIS 局部 tool_ctx,导致表格从未进入最终报告)。
        prior["tables_md"] = tables_md
        summary = _extract_tool_summary(
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
            f"- {r.title[:50]} [{r.rating}]" for r in (si.research.reports or [])[:5]
        )
        # 机构分歧量化摘要(近 3 月 EPS 预测变动趋势)
        research_div = _research_divergence(si)
        # 情感/自由现金流/情景 摘要(供模型直接引用,不重复计算)
        senti_summary = _sentiment_summary(senti)
        fcf_summary = _fcf_summary(fcf)
        scenario_summary = _scenario_summary(scenario)
        # user message 不再注入完整 tool JSON(这是旧架构的主要浪费)。
        user_content = (
            f"{stock_md}\n\n"
            f"# 已渲染表格\n{tables_md}\n\n"
            f"# 工具摘要\n{summary}\n\n"
            f"# 已采集社交媒体舆情(共 {len(si.social_posts)} 条)\n{social_summary}\n"
            f"{senti_summary}\n\n"
            f"# 自由现金流与股息(系统计算)\n{fcf_summary}\n\n"
            f"# 情景概率与风险收益比(系统计算)\n{scenario_summary}\n\n"
            f"# 已采集机构研报(共 {si.research.total_count} 篇)\n{research_summary}\n"
            f"{research_div}\n\n"
            f"# 输出章节结构(按此顺序,不可省略)\n{_SECTIONS_MD}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

        # 短期跨 run 缓存
        from ..cache import fingerprint, get_cached_report, set_cached_report
        rsp_cache_key_fp = fingerprint(si)
        cached_report = get_cached_report(si.symbol, rsp_cache_key_fp) or ""

        # LLM 产出 Markdown。仅当传输层 / 网络层 / HTTP 状态异常(连接重置、
        # 超时、DNS、以及 DeepSeek 瞬时 429/500/503 等)时才重试——模型正常
        # 返回空内容属于其真实输出,不重试(避免无谓重复调用)。
        draft = cached_report
        if not draft:
            for _attempt in range(3):
                try:
                    message = await self._call_llm_with_stream(
                        messages, reasoning_effort=_EFFORT_TWO_PHASE)
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
                set_cached_report(si.symbol, rsp_cache_key_fp, draft)
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

    async def _phase_self_check(self, draft: str) -> dict[str, object]:
        """Lightweight self-check: single short LLM call, never blocks pipeline.

        Returns ``{"passed": bool, "fixes_needed": list[str]}``.
        On any failure (timeout, parse error, exception) defaults to passed.
        """
        system = phase_system_prompt(Phase.SELF_CHECK, "", {"analysis_draft": draft})
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"# ANALYSIS 初稿\n\n{draft}"},
        ]
        try:
            message = await asyncio.wait_for(
                self._call_llm_with_stream(
                    messages, max_tokens=2048, reasoning_effort="low",
                ),
                timeout=60,
            )
            text = (message.get("content") or "").strip()
        except TimeoutError:
            logger.warning("[pipeline] SELF_CHECK 超时 60s, 跳过")
            return {"passed": True, "fixes_needed": []}
        except Exception as e:
            logger.warning("[pipeline] SELF_CHECK 异常 %s: %s", type(e).__name__, e)
            return {"passed": True, "fixes_needed": []}

        parsed, fixes = _parse_self_check_json(text)
        if parsed is None:
            logger.warning("[pipeline] SELF_CHECK JSON 解析失败, 跳过")
            return {"passed": True, "fixes_needed": []}

        passed = bool(parsed.get("passed", True))
        logger.info("[pipeline] SELF_CHECK passed=%s fixes=%d", passed, len(fixes))
        return {"passed": passed, "fixes_needed": fixes}

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

    async def _call_llm_with_stream(self, messages: list[dict], *,
                                     max_tokens: int | None = None,
                                     reasoning_effort: str = "max") -> dict:
        """单次 LLM 调用 wrapper,带 DeepSeek 思考模式 + 300s timeout。

        使用 reasoning_effort 控制思考强度(max = 最深思考)。
        思考模式下 temperature/top_p 等参数无效,不传。
        """
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model, "messages": messages,
            "max_tokens": max_tokens or settings.deepseek_max_tokens,
            "reasoning_effort": reasoning_effort,
        }
        with logphase(f"llm(effort={reasoning_effort}, mt={body['max_tokens']})"):
            resp = await self._llm_http.post(
                analyzer.api_url,
                headers={"Authorization": f"Bearer {analyzer.api_key}",
                         "Content-Type": "application/json"},
                json=body,
            )
        if resp.status_code >= 400:
            logger.error("[pipeline] LLM HTTP %d: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

    async def _stream_llm_content(
        self, messages: list[dict], *, max_tokens: int | None = None,
        reasoning_effort: str = "high",
    ) -> str:
        """流式 LLM 调用,实时打印 `##` 章节进度,返回拼接后的完整正文。

        与 ``_call_llm_with_stream``(非流式、返回 message dict)不同,本方法面向
        长文生成场景(COMPILE),用 SSE 流持续输出进度并只回收 content 文本。
        """
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model,
            "messages": messages,
            "max_tokens": max_tokens or settings.deepseek_max_tokens,
            "reasoning_effort": reasoning_effort,
            "stream": True,
        }
        with logphase(f"llm-stream(effort={reasoning_effort})"):
            async with self._llm_http.stream(
                "POST",
                analyzer.api_url,
                headers={
                    "Authorization": f"Bearer {analyzer.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            ) as resp:
                resp.raise_for_status()
                return await self._collect_content_stream(resp)

    @staticmethod
    async def _collect_content_stream(resp: httpx.Response) -> str:
        """读取 SSE 流,实时打印 ``##`` 章节标题,返回拼接后的正文文本。

        仅回收 ``delta.content``(忽略 reasoning 中间过程),逻辑与 legacy
        ``_collect_stream`` 对齐。使用 splitlines() 做 O(n) 缓冲处理。
        """
        import re as _re

        full: list[str] = []
        buf = ""
        current_section = ""
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            content = delta.get("content") or ""
            if not content:
                continue
            full.append(content)
            buf += content
            *lines, buf = buf.splitlines()
            for line_text in lines:
                line_text = line_text + "\n"
                header_match = _re.match(r"^##\s+(.+)", line_text)
                if header_match:
                    if current_section:
                        print()
                    section_name = header_match.group(1).strip()
                    current_section = section_name
                    print(f"\n{'─' * 40}\n  {section_name}\n{'─' * 40}")
                elif current_section and line_text.strip():
                    stripped = line_text.rstrip()
                    if len(stripped) > 120:
                        stripped = stripped[:117] + "..."
                    print(f"  {stripped}")
        if buf.strip():
            full.append(buf)
        return "".join(full)

    def _render_stock_context(self, si: StockAnalysis, reports: dict | None,
                              financial_md_path: Path | None) -> str:
        data = self.analyzer._build_data_dict(si, reports, financial_md_path)
        lines: list[str] = [f"# 标的快照 {si.name or si.symbol}"]
        quote = data.get("quote") or {}
        if quote.get("price"):
            lines.append(
                f"- 最新价: {quote.get('price')} | "
                f"涨跌: {quote.get('change_pct')}% | PE: {quote.get('pe')}"
            )
        # 注意:报告期/营收/净利/ROE 等财务字段不再在此重复列出 —— 工具结果
        # `financial_temporal` 已注入同字段,这里只保留跨维度事实(K 线/资金/行业)
        # 与 snapshot 独有的舆情,避免 input token 重复。
        cf = data.get("capital_flow") or {}
        if cf.get("main_net_5d"):
            lines.append(f"- 近5日主力净流入: {cf['main_net_5d'] / 1e8:.2f} 亿元")
        kline = data.get("kline_summary") or {}
        if kline.get("bar_count"):
            lines.append(
                f"- K线: {kline.get('bar_count')}根,"
                f"最新 {kline.get('latest_close')} ({kline.get('latest_date')})"
            )
        if data.get("industry"):
            lines.append(f"- 行业: {data['industry']}")
        # 舆情雪球/头条近 N 条标题摘要(跨维度事实,不在工具结果里)
        posts = getattr(si, "social_posts", None)
        if posts:
            sample = "；".join(
                (p.title or p.content or "")[:30] for p in posts[:3]
            )
            if sample:
                lines.append(f"- 舆情摘要: {sample}")
        if getattr(si, "history_financial", None):
            lines.append("- 历史财务时序(近 N 年报):")
            for f in si.history_financial[:5]:
                rev_str = f"{f.revenue / 1e8:.1f}亿" if f.revenue else "N/A"
                lines.append(f"  - {f.report_period}:营收 {rev_str} | "
                             f"ROE {f.roe}% | EPS {f.eps}")
        return "\n".join(lines)


# ---- module-level helpers -------------------------------------------------

def _partial(reason: str) -> dict[str, object]:
    return {"output": "", "tool_results": {}, "partial": True, "reason": reason}


def _is_partial(tool_value: object) -> bool:
    return isinstance(tool_value, dict) and "__partial__" in tool_value


def _sentiment_summary(senti: object) -> str:
    """Format sentiment tool output into a compact bullet summary."""
    if not isinstance(senti, dict) or _is_partial(senti):
        return "- 社媒情感分析: 数据缺失(无可用舆情文本)"
    total = senti.get("total") or 0
    if not total:
        return "- 社媒情感分析: 样本为空"
    lines = [
        f"- 整体情绪: {senti.get('label', 'N/A')}"
        f"(指数 {senti.get('sentiment_index', 0)},引擎 {senti.get('engine', 'N/A')})",
        f"- 分布: 正面 {senti.get('pos', 0)} / 负面 {senti.get('neg', 0)}"
        f"/ 中性 {senti.get('neu', 0)}(共 {total} 条)",
    ]
    kws = senti.get("top_keywords") or []
    if kws:
        lines.append("- 高频词: " + "、".join(f"{k['word']}({k['count']})" for k in kws[:8]))
    nw = senti.get("neg_words") or []
    if nw:
        lines.append("- 负面词: " + "、".join(f"{w}({c})" for w, c in nw[:5]))
    return "\n".join(lines)


def _fmt_yi(v: object) -> str:
    """Format a yuan amount: 亿 if large, else raw 2-decimal; N/A on None."""
    if v is None:
        return "N/A"
    if isinstance(v, (int, float)) and abs(v) >= 1e8:
        return f"{v / 1e8:.1f}亿"
    return f"{v:.2f}"


def _fcf_summary(fcf: object) -> str:
    """Format FCF/dividend tool output into a compact bullet summary."""
    if not isinstance(fcf, dict) or _is_partial(fcf):
        return "- 自由现金流与股息: 数据缺失(缺 OCF 或分红科目)"
    ocf = fcf.get("ocf")
    fcf_v = fcf.get("fcf")
    lines = [
        f"- 经营现金流 OCF: {_fmt_yi(ocf)} | 自由现金流 FCF: {_fmt_yi(fcf_v)}",
    ]
    pay = fcf.get("payout_ratio")
    dy = fcf.get("dividend_yield")
    if pay is not None:
        if dy is not None:
            lines.append(f"- 股息支付率: {pay * 100:.1f}% | 股息率: {dy * 100:.1f}%")
        else:
            lines.append(f"- 股息支付率: {pay * 100:.1f}%")
    cover = fcf.get("fcf_cover")
    if cover is not None:
        note = "可持续" if cover >= 1.0 else f"⚠️ 不可持续(FCF 仅覆盖 {cover:.2f} 倍)"
        lines.append(f"- FCF 覆盖分红: {cover:.2f} 倍 → {note}")
    return "\n".join(lines)


def _scenario_summary(scenario: object) -> str:
    """Format scenario probability / risk-reward tool output into a summary."""
    if not isinstance(scenario, dict) or _is_partial(scenario):
        return "- 情景概率与风险收益比: 数据缺失(缺估值目标价)"
    exp = scenario.get("expected_target")
    rr = scenario.get("risk_reward_ratio")
    down = scenario.get("downside_neutral_pct")
    up = scenario.get("upside_optimistic_pct")
    lines = []
    if exp is not None:
        lines.append(f"- 加权期望目标价: {exp} 元(期望 PE {scenario.get('expected_pe')})")
    if down is not None or up is not None:
        d = f"{down:+.1f}%" if isinstance(down, (int, float)) else "N/A"
        u = f"{up:+.1f}%" if isinstance(up, (int, float)) else "N/A"
        rr_txt = f" → 非对称比 {rr:.2f}" if rr is not None else ""
        lines.append(f"- 风险收益比: 中性下行 {d} / 乐观上行 {u}{rr_txt}")
    targets = scenario.get("targets") or {}
    if targets:
        parts = []
        name_map = {"conservative": "保守", "neutral": "中性", "optimistic": "乐观"}
        for tier in ("conservative", "neutral", "optimistic"):
            t = targets.get(tier) or {}
            p = t.get("probability")
            if p is not None:
                parts.append(f"{name_map.get(tier, tier)}{t.get('price')}({p}%)")
        if parts:
            lines.append("- 三档情景: " + " / ".join(parts))
    return "\n".join(lines) if lines else "- 情景概率与风险收益比: 数据缺失"


def _research_divergence(si: object) -> str:
    """量化机构研报分歧:EPS 预测区间 + 评级分布。"""
    research = getattr(si, "research", None)
    reports = (research.reports if research else None) or []
    if not reports:
        return "- 机构研报分歧: 数据缺失(无研报)"
    buys = sum(1 for r in reports if "买入" in (r.rating or "") or "推荐" in (r.rating or ""))
    holds = sum(1 for r in reports if "增持" in (r.rating or ""))
    neutrals = sum(1 for r in reports if "中性" in (r.rating or "") or "持有" in (r.rating or ""))
    eps_list = [float(r.eps_this_yr) for r in reports if getattr(r, "eps_this_yr", 0)]
    lines = [
        f"- 评级分布: 买入 {buys} / 增持 {holds} / 中性 {neutrals}(共 {len(reports)} 篇)",
    ]
    if eps_list:
        lo, hi = min(eps_list), max(eps_list)
        avg = sum(eps_list) / len(eps_list)
        spread = (hi - lo) / lo * 100 if lo else 0.0
        lines.append(
            f"- 当年 EPS 预测: 区间 [{lo:.2f}, {hi:.2f}], 均值 {avg:.2f}, "
            f"分歧幅度 {spread:.1f}%(分歧>15% 视为预期差大)"
        )
    return "\n".join(lines)


async def _run_safe(fn, *args) -> dict[str, object]:
    try:
        result = fn(*args)
        return result if isinstance(result, dict) else {"__partial__": "bad_return"}
    except Exception as e:
        logger.warning("[pipeline] 工具 %s 异常 %s: %s",
                       getattr(fn, "__name__", fn), type(e).__name__, e)
        return {"__partial__": f"{type(e).__name__}"}


async def _run_peer_compare(si: object, search_fn) -> dict:
    from ..tools.peer_compare import build_search_query
    from ..tools.peer_compare import parse as peer_parse

    name = str(getattr(si, "name", "") or getattr(si, "symbol", "") or "")
    query = build_search_query(name, getattr(si, "industry", "") or "")
    try:
        html = await search_fn(query) if search_fn else ""
    except Exception as e:
        logger.debug("[pipeline] peer_compare search failed: %s", e)
        html = ""
    if not html:
        return {"__partial__": "no_data", "peers": []}
    # parse() 返回裸 list,必须包成 {"peers": ...} 才能被 render_peer_comparison 消费
    # (该函数取 data.get("peers"));直接返回 list 会让同行对比表恒空。
    peers = peer_parse(html, getattr(si, "financial", None))
    return {"peers": peers, "industry": getattr(si, "industry", "")}


def _parse_self_check_json(text: str) -> tuple[dict | None, list[str]]:
    """Parse self-check JSON from LLM response text.

    Tries ``json`` code fence first, then falls back to finding any JSON
    object containing a ``passed`` key.  Returns ``(parsed_dict, fixes_list)``
    or ``(None, [])`` on failure.
    """
    import re

    # 1. Prefer ```json fence
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, dict):
                fixes = parsed.get("fixes_needed", [])
                return parsed, [str(f) for f in fixes if isinstance(f, (str, int, float))]
        except (json.JSONDecodeError, ValueError):
            pass
    # 2. Fallback: find any { ... } containing "passed"
    for match in re.finditer(r"\{[^{}]*\}", text):
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict) and "passed" in parsed:
                fixes = parsed.get("fixes_needed", [])
                return parsed, [str(f) for f in fixes if isinstance(f, (str, int, float))]
        except (json.JSONDecodeError, ValueError):
            continue
    # 3. Last resort: find outermost braces
    last_brace = text.rfind("}")
    if last_brace > 0:
        first_brace = text.rfind("{", 0, last_brace)
        if first_brace >= 0:
            try:
                parsed = json.loads(text[first_brace:last_brace + 1])
                if isinstance(parsed, dict):
                    fixes = parsed.get("fixes_needed", [])
                    return parsed, [str(f) for f in fixes if isinstance(f, (str, int, float))]
            except (json.JSONDecodeError, ValueError):
                pass
    return None, []


def _extract_tool_summary(results: dict) -> str:
    """Generate a short (~200 chars) text summary of non-tabular tool outputs.

    Helps the LLM produce analysis without needing the full tool JSON.
    """
    parts: list[str] = []
    t = results.get("technicals") or {}
    if isinstance(t, dict):
        trend = t.get("trend") or ""
        rsi = t.get("rsi14")
        main = t.get("main_net_5d")
        if trend:
            parts.append(f"趋势={trend}")
        if rsi is not None:
            parts.append(f"RSI={rsi:.1f}" if isinstance(rsi, (int, float)) else f"RSI={rsi}")
        if main is not None:
            if isinstance(main, (int, float)):
                parts.append(f"主力5日={main / 1e8:.2f}亿")
            else:
                parts.append(f"主={main}")
    r = results.get("risk_quant") or {}
    if isinstance(r, dict) and isinstance(r.get("bears"), list):
        nb = len(r["bears"])
        if nb:
            parts.append(f"看空={nb}条")
    m = results.get("business_moat") or {}
    if isinstance(m, dict):
        moat = m.get("moat_sources") or []
        if isinstance(moat, list) and moat:
            parts.append(f"护城河={','.join(str(x) for x in moat[:3])}")
        ocf_q = m.get("ocf_quality")
        if ocf_q:
            parts.append(f"OCF质量={ocf_q}")
    # 自由现金流 / 股息
    f = results.get("fcf_dividend") or {}
    if isinstance(f, dict) and not _is_partial(f):
        fcf = f.get("fcf")
        pay = f.get("payout_ratio")
        dy = f.get("dividend_yield")
        if fcf is not None:
            parts.append(f"FCF={fcf/1e8:.1f}亿" if abs(fcf) >= 1e8 else f"FCF={fcf:.1f}")
        if pay is not None:
            parts.append(f"股息支付率={pay*100:.1f}%")
        if dy is not None:
            parts.append(f"股息率={dy*100:.1f}%")
    # 情景概率 / 风险收益比
    s = results.get("scenario_prob") or {}
    if isinstance(s, dict) and not _is_partial(s):
        exp = s.get("expected_target")
        rr = s.get("risk_reward_ratio")
        if exp is not None:
            parts.append(f"加权期望目标价={exp}")
        if rr is not None:
            parts.append(f"风险收益比={rr}")
    # 舆情情感
    senti = results.get("sentiment") or {}
    if isinstance(senti, dict) and not _is_partial(senti):
        label = senti.get("label")
        idx = senti.get("sentiment_index")
        if label:
            parts.append(f"舆情情绪={label}(指数{idx})")
    return ", ".join(parts) if parts else "N/A"





