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

logger = logging.getLogger(__name__)

MAX_TOTAL_SEC = 540  # 9 min (240 ANALYSIS + 300 COMPILE + buffer)

# 运行时硬门控(prompt 级约束 financial/business_depth 不参与判定 → 少误杀)
_REQUIRED_GATES = (
    "citations_ok", "tables_ok", "trigger_ok", "advice_ok",
    "norepeat_ok", "justified_ok",
)


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
                  use_fast: bool = False) -> dict[str, object]:
        ctx = PipelineContext()
        stock_md = self._render_stock_context(si, reports, financial_md_path)
        prior: dict[str, object] = {}

        # Phase 1: ANALYSIS — 并行工具 + LLM + 自检
        try:
            analysis_result = await asyncio.wait_for(
                self._phase_analysis(
                    si, stock_md, prior, reports, financial_md_path, use_fast=use_fast,
                ),
                timeout=240,
            )
        except TimeoutError:
            logger.warning("[pipeline] ANALYSIS 超时 240s, 降级")
            analysis_result = _partial("timeout")
        except Exception as e:  # broad tolerance: never abort the pipeline
            import traceback as tb_mod
            logger.error(
                "[pipeline] ANALYSIS 异常 %s: %s\n%s",
                type(e).__name__, e, tb_mod.format_exc()
            )
            analysis_result = _partial(f"{type(e).__name__}")

        ctx.phase_results[Phase.ANALYSIS.value] = analysis_result
        # 空 ANALYSIS 直接降级 legacy(不用 forward 给后续)
        if analysis_result.get("empty_analysis"):
            ctx.partial_phases.append(Phase.ANALYSIS.value)
            prior["analysis_draft"] = ""
            prior["self_check_fixes"] = []
            return ctx.to_dict()
        if analysis_result.get("partial"):
            ctx.partial_phases.append(Phase.ANALYSIS.value)
            # partial 但 output 非空时仍把 output 传给 compile(降级而非丢弃)
            prior["analysis_draft"] = analysis_result.get("output", "")
            prior["self_check_fixes"] = analysis_result.get("checks", {}).get("fixes_needed", [])
            prior["tools_output"] = analysis_result.get("tool_results", {})
        else:
            prior["analysis_draft"] = analysis_result.get("output", "")
            prior["self_check_fixes"] = analysis_result.get("checks", {}).get("fixes_needed", [])
            prior["tools_output"] = analysis_result.get("tool_results", {})

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
                              *, use_fast: bool = False) -> dict:
        from ..web_search_tool import execute_web_search

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

        tool_results: dict[str, object] = {
            "technicals": tech, "financial_temporal": fin, "peer_compare": peer,
            "risk_quant": risk, "valuation": val, "business_moat": moat,
        }
        partial = any(_is_partial(v) for v in tool_results.values())

        # 2. 拼装注入 tool_result 的 messages(role=user 上下文)
        tool_ctx = {**prior, "tools_output": tool_results}
        system = phase_system_prompt(Phase.ANALYSIS, stock_md, tool_ctx)
        injected = _tool_results_to_messages(tool_results)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": stock_md},
            *injected,
        ]

        # 3. LLM 产出完整 Markdown 草稿(禁用 tool_choice 避免搜索循环)
        #    空输出重试至多 2 次,重试时提高 thinking_budget 让模型想更久 + 避限流
        draft = ""
        for _attempt in range(3):
            try:
                message = await self._call_llm_with_stream(
                    messages, thinking_budget=800 if _attempt > 0 else 500)
                draft = (message.get("content") or "").strip()
            except Exception as e:
                logger.error("[pipeline] ANALYSIS LLM 调用失败 %s: %s", type(e).__name__, e)
                draft = ""
            if draft:
                break
            if _attempt < 2:
                await asyncio.sleep(1)
        if not draft:
            logger.warning("[pipeline] ANALYSIS 输出为空(重试 %d 次后),标记降级", 3)
            return {"output": "", "tool_results": tool_results,
                    "partial": True, "checks": {}, "empty_analysis": True}

        # 4. 自检(可选)
        #    --fast / use_fast:跳过自检,直接把初稿传 COMPILE(prompt 已约束质控)
        if use_fast:
            return {"output": draft, "tool_results": tool_results,
                    "partial": partial, "checks": {}}

        check_json = await self._run_self_check(draft)
        checks, fixes = _parse_self_check_json(check_json)
        if checks is None:
            # 自检 JSON 解析失败(不是 draft 为空,上面已处理),把 draft 传出但 partial
            return {"output": draft, "tool_results": tool_results,
                    "partial": True, "checks": {}}

        # 5. 修复循环:仅在校验未通过 + 初稿不够长 → 重跑(至多 1 次重跑)
        #    阈值:全部 6 项 runtime 门控通过即视为通过;且长初稿(>3000 字)直接采纳
        gates_pass = all(checks.get(g) for g in _REQUIRED_GATES)
        if not gates_pass and fixes and len(draft) <= 3000:
            draft = await self._reanalysis_with_fixes(stock_md, prior, tool_results, fixes)
            check_json2 = await self._run_self_check(draft)
            checks2, fixes2 = _parse_self_check_json(check_json2)
            if checks2 is not None:
                checks, fixes = checks2, fixes2

        return {"output": draft, "tool_results": tool_results,
                "partial": partial, "checks": checks}

    async def _run_self_check(self, draft: str) -> str:
        """单独一次 LLM 调用,仅输出自检 JSON。"""
        system = (
            "你是报告质检员。基于下方草稿与强制校验清单,仅输出合法 JSON。\n"
            "强制校验 8 项(后两项为 prompt 级软约束;前 6 项为运行时硬门控):\n"
            "- citations_ok: 每个关键数字都标注来源(训练数据/公司年报/搜索结果)\n"
            "- tables_ok: 三张核心表格(近年财务时序 ≥5 行/同行竞品 ≥5 家/估值三档含概率)格式合规\n"
            "- trigger_ok: 每一条看空都含明确触发条件(可量化阈值)+估值冲击%\n"
            "- advice_ok: 投资建议明确(买/持/卖+价格区间+催化剂+止损)\n"
            "- norepeat_ok: 全文无连续超过 20 字的重复段落\n"
            "- justified_ok: 任何定性判断必须有具体数字支撑\n"
            "- financial_depth_ok: 5 年 CAGR+ROE 杜邦+OCF/利润比(prompt 级软约束)\n"
            "- business_depth_ok: 产品结构+护城河+竞争格局(prompt 级软约束)\n"
            "严格输出以下 JSON Schema(不要 fences,不要其他内容):\n"
            '{"citations_ok": bool, "tables_ok": bool, "trigger_ok": bool, '
            '"advice_ok": bool, "financial_depth_ok": bool, "business_depth_ok": bool, '
            '"norepeat_ok": bool, "justified_ok": bool, "fixes_needed": [str]}\n'
            "某项为 false 时,fixes_needed 必须列出具体修复点(中文,≤40 字/每条)。"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"# 分析草稿\n\n{draft[:8000]}"},
        ]
        try:
            message = await self._llm_chat(messages, tools=None, tool_choice="none")
            return (message.get("content") or "").strip()
        except Exception as e:
            logger.error("[pipeline] _run_self_check 失败 %s: %s", type(e).__name__, e)
            return ""

    async def _reanalysis_with_fixes(self, stock_md: str, prior: dict,
                                     tool_results: dict, fixes: list[str]) -> str:
        fix_note = (
            "以下自检未通过,请直接修改草稿以解决这些点,"
            "仅输出修改后的完整 Markdown 草稿(不要输出 JSON):\n"
            + "\n".join(f"- {f}" for f in fixes)
        )
        tool_ctx = {**prior, "tools_output": tool_results}
        system = phase_system_prompt(Phase.ANALYSIS, stock_md, tool_ctx)
        injected = _tool_results_to_messages(tool_results)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": stock_md},
            *injected,
            {"role": "user", "content": "# 修改要求\n\n" + fix_note},
        ]
        try:
            message = await self._call_llm_with_stream(messages, thinking_budget=500)
            text = (message.get("content") or "").strip()
            return text or ""
        except Exception as e:
            logger.warning("[pipeline] reanalysis 失败 %s: %s", type(e).__name__, e)
            return ""

    # ---- Phase 2: COMPILE ------------------------------------------------

    async def _phase_compile(self, stock_md: str, prior: dict) -> dict:
        from ..analyzer import _strip_xml_tool_calls as _strip_xml

        draft = str(prior.get("analysis_draft", "") or "")
        if not draft:
            draft = f"# 标的快照\n\n{stock_md}"
        system = phase_system_prompt(Phase.COMPILE, stock_md, prior)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"# 经自检认可的草稿\n\n{draft}"},
        ]
        try:
            message = await self._call_llm_with_stream(messages, thinking_budget=500)
            text = (message.get("content") or "").strip() if isinstance(message, dict) else ""
        except Exception as e:
            logger.warning("[pipeline] COMPILE 非流式调用失败 %s: %s", type(e).__name__, e)
            text = draft
        stripped = _strip_xml(text) if text else ""
        return {"output": stripped or draft, "tool_results": {}, "partial": False}

    async def _call_llm_with_stream(self, messages: list[dict], *,
                                     max_tokens: int | None = None,
                                     thinking_budget: int = 500) -> dict:
        """单次 LLM 调用 wrapper,带 thinking budget + 300s timeout。"""
        import httpx as _httpx
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model, "messages": messages,
            "temperature": settings.deepseek_temperature,
            "max_tokens": max_tokens or min(settings.deepseek_max_tokens, 4096),
            "thinking": {"type": "enabled", "budget_tokens": thinking_budget},
        }
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
                          thinking_budget: int = 800) -> str:
        """流式 LLM 调用,适用于 COMPILE 期(终稿输出需要流式累积)。"""
        import httpx as _httpx
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model, "messages": messages,
            "temperature": settings.deepseek_temperature,
            "max_tokens": max_tokens or min(settings.deepseek_max_tokens, 4096),
            "stream": True,
            "thinking": {"type": "enabled", "budget_tokens": thinking_budget},
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
                                                 thinking_budget=thinking_budget)
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
        fin = data.get("financial") or {}
        if fin.get("period"):
            lines.append(f"- 报告期: {fin.get('period')}")
            for k in ("rev", "rev_yoy", "np", "np_yoy", "roe", "eps", "ocf"):
                if fin.get(k) not in (None, 0, ""):
                    lines.append(f"  - {k}: {fin[k]}")
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
        # 历史财务时序(展示最近 N 年核心指标,作为 LLM 上下文的一部分)
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


def _tool_results_to_messages(tool_results: dict) -> list[dict]:
    messages = []
    for name, value in tool_results.items():
        payload = json.dumps(value, ensure_ascii=False, default=str)
        messages.append({
            "role": "user",
            "content": f"[并行工具结果: {name}]\n{payload}",
        })
    return messages


def _parse_self_check_json(text: str) -> tuple[dict | None, list[str]]:
    """从文本末尾解析自检 JSON(支持 ```json fence 或裸 JSON)。"""
    import re
    if not text:
        return None, []
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


def _phase_output_text(phase_result: object) -> str:
    r = phase_result if isinstance(phase_result, dict) else {}
    out = r.get("output")
    return out if isinstance(out, str) else ""
