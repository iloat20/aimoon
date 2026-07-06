"""Pipeline v2 orchestrator — two-phase: ANALYSIS + COMPILE.

Phase 1 (ANALYSIS): 并行跑 6 个纯工具 + LLM 生成深度报告初稿 + 自检 JSON + 1 次修复循环。
Phase 2 (COMPILE): 基于经自检认可的初稿,复用 analyzer._stream_final_response 生成终稿长文。

总硬上限 540s(9 分钟)。每阶段独立容错:超时 / 异常 / 畸形 JSON 标 ``__partial__`` 并继续,
绝不阻塞后续阶段(项目 CLAUDE.md 的 broad-tolerance 规则)。
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

from ..tools import TOOL_RUNNERS
from .phases import Phase, phase_system_prompt
from .table_renderer import (
    render_financial_temporal,
    render_peer_comparison,
    render_valuation_targets,
)
from .timing import logphase
from .tool_cache import get_cached_tool_results, set_cached_tool_results

logger = logging.getLogger(__name__)

MAX_TOTAL_SEC = 540  # 9 min (240 ANALYSIS + 300 COMPILE + buffer)

# DeepSeek 思考强度(reasoning_effort)。
# 官方支持: low/medium→映射为 high, high, xhigh→映射为 max。
# 默认 max(最深思考);重试时保持 max(不降级,避免思考不充分)。
_EFFORT_SINGLE = "max"
_EFFORT_TWO_PHASE = "high"  # 旧双阶段模式(重试时仍用 high)

# 报告章节结构 —— 代码侧维护,运行时格式化为 `## 一、…## 八、…` 标题块。
# 替换 system 模板占位符 `{{ sections }}` 的同时,作为 sections_list 注入
# 到 user message,让模型直接按自检 JSON 前的自然章节顺序输出。
# 标题之外的细节要求(1000 字/三张表/FCFE 三档…)交给工具数据 + 三要素规则驱动,
# 不再写入 prompt,以压缩输入 token。
_REPORT_SECTIONS = [
    ("一", "业务画像与护城河"),
    ("二", "财务健康诊断"),
    ("三", "交叉验证"),
    ("四", "风险量化与看空逻辑"),
    ("五", "估值建模"),
    ("六", "逆向视角"),
    ("七", "投资建议"),
    ("八", "附录"),
]
_SECTIONS_MD = "\n".join(f"## {n}、{t}" for n, t in _REPORT_SECTIONS)



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

    def to_dict(self) -> dict[str, object]:
        return {
            "final_markdown": self.final_markdown,
            "phase_results": self.phase_results,
            "partial_phases": self.partial_phases,
        }


class PipelineOrchestrator:
    """ANALYSIS + COMPILE 两阶段 pipeline。"""

    def __init__(self, analyzer: AnalyzerRuntime) -> None:
        self.analyzer = analyzer

    async def run(self, si: StockAnalysis, *, reports: dict | None = None,
                  financial_md_path: Path | None = None,
                  use_fast: bool = False,
                  use_single_call: bool = False,
                  use_ultra_fast: bool = False) -> dict[str, object]:
        ctx = PipelineContext()
        stock_md = self._render_stock_context(si, reports, financial_md_path)
        prior: dict[str, object] = {}

        skip_self_check = use_fast or use_ultra_fast or use_single_call
        skip_compile = use_single_call or use_ultra_fast

        # Phase 1: ANALYSIS — 并行工具 + LLM + (可选) 自检
        try:
            analysis_result = await asyncio.wait_for(
                self._phase_analysis(
                    si, stock_md, prior, reports, financial_md_path,
                    use_fast=skip_self_check, use_single_call=use_single_call,
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

        # single-call / ultra-fast: 直接把 ANALYSIS 初稿当终稿输出,跳过 COMPILE
        if skip_compile and prior["analysis_draft"]:
            ctx.final_markdown = _strip_xml_tool_calls_local(str(prior["analysis_draft"]))
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

        return ctx.to_dict()

    # ---- Phase 1: ANALYSIS ------------------------------------------------

    async def _phase_analysis(self, si: StockAnalysis, stock_md: str, prior: dict,
                              reports: dict | None, financial_md_path: Path | None,
                              *, use_fast: bool = False,
                              use_single_call: bool = False) -> dict:
        """Phase 1: 并行工具 + LLM 初稿 + (可选) 自检。

        ``use_single_call`` 合并两次 LLM 调用为一次:要求模型在初稿末尾内联自检 JSON,
        既省掉独立 self-check 调用,也跳过后续 COMPILE 阶段。
        """
        from ..web_search_tool import execute_web_search

        # 工具结果缓存:同 stock hash 命中时跳过 6 个纯工具重算(省 0.4s 无足轻重,
        # 但避免 pandas 重复计算,更重要的是为后续可能的 LLM 缓存提供稳定输入 hash)
        cached_tools = get_cached_tool_results(si)
        if cached_tools is not None:
            tech = cached_tools.get("technicals", {})
            fin = cached_tools.get("financial_temporal", {})
            peer = cached_tools.get("peer_compare", {})
            risk = cached_tools.get("risk_quant", {})
            val = cached_tools.get("valuation", {})
            moat = cached_tools.get("business_moat", {})
            partial = any(_is_partial(v) for v in [tech, fin, peer, risk, val, moat])
        else:
            # 1. 并行跑 4 个纯工具(async-safe coroutines)
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
            peer_raw = _run_peer_compare(si, execute_web_search)
            if inspect.iscoroutine(peer_raw):
                peer = await peer_raw
            else:
                peer = peer_raw
            tech, fin, moat = await asyncio.gather(tech_coro, fin_coro, moat_coro)
            risk = await _run_safe(TOOL_RUNNERS["risk_quant"], fin, si.quote)
            val = await _run_safe(TOOL_RUNNERS["valuation"], fin, peer, si.quote)

            tool_results = {
                "technicals": tech, "financial_temporal": fin, "peer_compare": peer,
                "risk_quant": risk, "valuation": val, "business_moat": moat,
            }
            partial = any(_is_partial(v) for v in tool_results.values())
            # Cache tool results for rapid re-runs (same upstream data hash)
            if not partial:
                set_cached_tool_results(si, tool_results)

        # 2. 渲染核心表格(Python 模板,0 LLM token)+ 工具摘要(非表格部分)
        tables_md = "\n\n".join(
            [
                render_financial_temporal(fin),
                render_peer_comparison(peer),
                render_valuation_targets(val),
            ]
        )
        summary = _extract_tool_summary(
            {
                "technicals": tech,
                "financial_temporal": fin,
                "peer_compare": peer,
                "risk_quant": risk,
                "valuation": val,
                "business_moat": moat,
            }
        )

        # 3. Hybrid user message: snapshot + 已渲染表格 + 工具摘要(无完整 JSON)
        #    模型只需做分析对比,不需要重新生成表格数字(否则会与 tables_md 重复)。
        tool_ctx = {**prior, "tables_md": tables_md, "summary": summary}
        system = phase_system_prompt(
            Phase.ANALYSIS, stock_md, tool_ctx
        ).replace("{{ sections }}", _SECTIONS_MD)
        # user message 不再注入完整 tool JSON(这是旧架构的主要浪费)。
        user_content = (
            f"{stock_md}\n\n"
            f"# 已渲染表格\n{tables_md}\n\n"
            f"# 工具摘要\n{summary}\n\n"
            f"# 输出章节结构(按此顺序,不可省略)\n{_SECTIONS_MD}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

        # 短期跨 run 缓存
        from ..cache import get_cached_report, set_cached_report
        from .tool_cache import _fingerprint
        rsp_cache_key_fp = _fingerprint(si)
        cached_report = get_cached_report(si.symbol, rsp_cache_key_fp) or ""

        # LLM 产出 Markdown(空输出重试至多 2 次)
        draft = cached_report
        if not draft:
            for _attempt in range(3):
                try:
                    effort = _EFFORT_SINGLE if use_single_call else _EFFORT_TWO_PHASE
                    message = await self._call_llm_with_stream(
                        messages, max_tokens=16384 if use_single_call else None,
                        reasoning_effort=effort)
                    draft = (message.get("content") or "").strip()
                    draft = (message.get("content") or "").strip()
                except Exception as e:
                    logger.error("[pipeline] ANALYSIS LLM 调用失败 %s: %s", type(e).__name__, e)
                    draft = ""
                if draft:
                    break
                if _attempt < 2:
                    await asyncio.sleep(1)
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



    # ---- Phase 2: COMPILE ------------------------------------------------

    async def _phase_compile(self, stock_md: str, prior: dict) -> dict:
        draft = str(prior.get("analysis_draft", "") or "")
        if not draft:
            draft = f"# 标的快照\n\n{stock_md}"
        system = phase_system_prompt(Phase.COMPILE, stock_md, prior)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"# 经自检认可的草稿\n\n{draft}"},
        ]
        try:
            message = await self._call_llm_with_stream(
                messages, reasoning_effort="max")
            text = (message.get("content") or "").strip() if isinstance(message, dict) else ""
        except Exception as e:
            logger.warning("[pipeline] COMPILE 非流式调用失败 %s: %s", type(e).__name__, e)
            text = draft
        stripped = _strip_xml_tool_calls_local(text) if text else ""
        return {"output": stripped or draft, "tool_results": {}, "partial": False}

    async def _call_llm_with_stream(self, messages: list[dict], *,
                                     max_tokens: int | None = None,
                                     reasoning_effort: str = "max") -> dict:
        """单次 LLM 调用 wrapper,带 DeepSeek 思考模式 + 300s timeout。

        使用 reasoning_effort 控制思考强度(max = 最深思考)。
        思考模式下 temperature/top_p 等参数无效,不传。
        """
        import httpx as _httpx
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model, "messages": messages,
            "max_tokens": max_tokens or settings.deepseek_max_tokens,
            "reasoning_effort": reasoning_effort,
        }
        with logphase(f"llm(effort={reasoning_effort}, mt={body['max_tokens']})"):
            async with _httpx.AsyncClient(timeout=300.0) as http:
                resp = await http.post(analyzer.api_url,
                                       headers={"Authorization": f"Bearer {analyzer.api_key}",
                                                "Content-Type": "application/json"},
                                       json=body)
        if resp.status_code >= 400:
            logger.error("[pipeline] LLM HTTP %d: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

    async def _stream_llm(self, messages: list[dict], *,
                          max_tokens: int | None = None,
                          reasoning_effort: str = "max") -> str:
        """流式 LLM 调用,适用于 COMPILE 期(终稿输出需要流式累积)。"""
        import httpx as _httpx
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model, "messages": messages,
            "max_tokens": max_tokens or min(settings.deepseek_max_tokens, 4096),
            "stream": True,
            "reasoning_effort": reasoning_effort,
        }
        full_text: list[str] = []
        async with _httpx.AsyncClient(timeout=300.0) as http:
            async with http.stream("POST", analyzer.api_url,
                                   headers={"Authorization": f"Bearer {analyzer.api_key}",
                                            "Content-Type": "application/json"},
                                   json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            full_text.append(content)
                    except json.JSONDecodeError:
                        continue
        return "".join(full_text)
        """单次文本输出,禁用 tool_choice 避免搜索循环。

        当 ANALYSIS / COMPILE prompt 已经有充足的硬数据注入时,让模型自由调
        web_search 会陷入搜索循环(模型倾向每轮都调 tool 而不输出文本)。
        因此 tools=None,单次调用产出完整 Markdown 草稿。
        """
        tool_results: dict[str, object] = {}
        try:
            message = await self._llm_chat(messages, tools=None, tool_choice="none")
            content = (message.get("content") or "").strip()
            return content, tool_results
        except Exception as e:
            logger.error("[pipeline] _web_search_loop 失败 %s: %s", type(e).__name__, e)
            return "", tool_results

    async def _llm_chat(self, messages: list[dict], *,
                        tools: list[dict] | None = None,
                        tool_choice: str = "auto",
                        max_tokens: int | None = None,
                        thinking_budget: int = 500) -> dict:
        """单次文本输出 LLM 调用(非流式),带独立 HTTP client(300s timeout)。"""
        return await self._call_llm_with_stream(messages, max_tokens=max_tokens,
                                                 reasoning_effort="max")
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

    query = build_search_query(getattr(si, "name", None) or getattr(si, "symbol", ""),
                               getattr(si, "industry", ""))
    try:
        html = await search_fn(query) if search_fn else ""
    except Exception as e:
        logger.debug("[pipeline] peer_compare search failed: %s", e)
        html = ""
    return peer_parse(html, getattr(si, "financial", None)) if html else {"__partial__": "no_data"}


def _strip_xml_tool_calls_local(text: str) -> str:
    """模块级 XML tool-call 剥离(避免每个调用点重复 import)。"""
    import re
    text = re.sub(
        r"<｜｜DSML｜｜tool_calls>.*?</｜｜DSML｜｜tool_calls>", "", text, flags=re.DOTALL
    )
    text = re.sub(r"<｜｜DSML｜｜invoke.*?</｜｜DSML｜｜invoke>", "", text, flags=re.DOTALL)
    return text.strip()


def _tool_results_to_messages(tool_results: dict) -> list[dict]:
    """把 {name: result} 转成注入消息列表。

    每个工具输出提取关键字段,压缩到 ~300 字符内,避免完整 JSON 膨胀 input tokens。
    这是节省 API 成本的关键路径之一(input token 单价是 output 的 1/4,但累计量大)。
    """
    messages = []
    for name, value in tool_results.items():
        payload = _compact_tool_output(name, value)
        messages.append({
            "role": "user",
            "content": f"[{name}]\n{payload}",
        })
    return messages


def _compact_tool_output(name: str, value: Any) -> str:
    """提取 tool output 的关键数字,返回 100-300 字符的摘要字符串。

    模型只需要关键数字 + 对比结果,不需要完整链式结构(unique key + 完整 dict)。
    """
    import json as _json

    if not isinstance(value, dict):
        s = str(value)
        return s[:300] if len(s) > 300 else s

    # 通用:只保留数值 + 短字符串字段,丢弃长数组/重复结构
    compact: dict[str, Any] = {}
    for k, v in value.items():
        if isinstance(v, (int, float, bool)):
            compact[k] = v
        elif isinstance(v, str) and len(v) <= 30:
            compact[k] = v
        elif isinstance(v, dict) and len(str(v)) <= 100:
            compact[k] = v
        elif isinstance(v, list) and len(v) <= 3:
            compact[k] = v
        # 长数组/大 dict 丢弃 — 模型已经从其他字段读到关键数字

    # 特殊:某些 tool 的关键字段单独保留
    if name == "technicals":
        for key in ("trend", "ma5", "ma20", "ma60", "macd", "rsi14", "bollinger",
                     "main_net_5d", "volume_ratio_5"):
            if key in value and key not in compact:
                compact[key] = value[key]
    elif name == "financial_temporal":
        for key in ("revenue_cagr", "net_profit_cagr", "roe_trend"):
            if key in value and key not in compact:
                compact[key] = value[key]
    elif name == "peer_compare":
        # 只保留前 3 家 peer 的关键字段
        if "peers" in value and isinstance(value["peers"], list):
            compact["top_peers"] = [
                {k: p[k] for k in ("name", "pe", "pb", "roe") if k in p}
                for p in value["peers"][:3]
            ]
    elif name == "risk_quant":
        if "bears" in value:
            # 只保留触发条件 + 冲击%,丢弃 recommendation 等软文字
            compact["bears"] = [
                {k: b[k] for k in ("theme", "trigger_condition", "impact_pct") if k in b}
                for b in value["bears"][:3]
            ]
    elif name == "valuation":
        if "fcfe_targets" in value:
            compact["targets"] = value["fcfe_targets"]
        if "fcfe_assumptions" in value:
            compact["assumptions"] = value["fcfe_assumptions"]

    out = _json.dumps(compact, ensure_ascii=False, default=str)
    if len(out) > 500:
        out = out[:497] + "..."
    return out


    # 1. 优先匹配 ```json fence
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, dict):
                fixes = parsed.get("fixes_needed", [])
                return parsed, [str(f) for f in fixes if isinstance(f, (str, int, float))]
        except (json.JSONDecodeError, ValueError):
            pass
    # 2. 回退: 找最后一个独立的 { ... } 块
    for match in re.finditer(r"\{[^{}]*\}", text):
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict) and "citations_ok" in parsed:
                fixes = parsed.get("fixes_needed", [])
                return parsed, [str(f) for f in fixes if isinstance(f, (str, int, float))]
        except (json.JSONDecodeError, ValueError):
            continue
    # 3. 回退: 取最后一个 '}' 往前
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
            parts.append(f"主力5日={main / 1e8:.2f}亿" if isinstance(main, (int, float)) else f"主={main}")
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
    return ", ".join(parts) if parts else "N/A"


def _phase_output_text(phase_result: object) -> str:
    r = phase_result if isinstance(phase_result, dict) else {}
    out = r.get("output")
    return out if isinstance(out, str) else ""


