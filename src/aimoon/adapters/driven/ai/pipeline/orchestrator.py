"""Pipeline v2 orchestrator.

串联五阶段 + 300s 总硬上限。PLAN / COLLECT / ANALYSIS 三阶段接 LLM +
并行纯工具(Task 13);SELF_CHECK / COMPILE 在 Task 14 / 15 接入。

每阶段独立容错:超时 / 异常 / 畸形 JSON 标 ``__partial__`` 并继续,
绝不阻塞后续阶段(项目 CLAUDE.md 的 broad-tolerance 规则)。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

from ..tools import TOOL_RUNNERS
from .phases import Phase, get_pipeline_phases
from .prompts import phase_system_prompt

logger = logging.getLogger(__name__)

MAX_TOTAL_SEC = 300  # 总硬上限 5 分钟


class AnalyzerRuntime(Protocol):
    """DeepSeekAIAnalyzer 运行时协议(避免 orchestrator→analyzer 循环 import)。

    仅声明 orchestrator 真正访问的属性;analyzer 侧自然满足。
    """

    _settings: Any
    _provided_settings: Any | None
    _http: Any
    api_url: str
    api_key: str

    def _build_data_dict(
        self,
        info: StockAnalysis,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
    ) -> dict[str, Any]: ...


@dataclasses.dataclass
class PipelineContext:
    """Pipeline 运行上下文(精确类型,避免 dict[str,object] 推窄)。"""

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
    """串联 phases 的 orchestrator;PLAN/COLLECT/ANALYSIS 接 LLM + 并行工具。"""

    def __init__(self, analyzer: AnalyzerRuntime) -> None:
        self.analyzer = analyzer
        self._log: list[dict] = []

    # ---- public entry -----------------------------------------------------

    async def run(
        self,
        si: StockAnalysis,
        *,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
    ) -> dict[str, object]:
        """Run the 5-phase pipeline and return a PipelineContext dict.

        Shape::

            {
              "final_markdown": "",
              "phase_results": {
                  "plan":     {"output": str, "tool_results": dict, "partial": bool},
                  "collect":  {...},
                  "analysis": {...},
                  ...
              },
              "partial_phases": ["collect", ...],
            }

        COMPILE (Task 15) fills ``final_markdown``; until then it stays "".
        """
        t0 = time.monotonic()
        ctx = PipelineContext()
        stock_md = self._render_stock_context(si, reports, financial_md_path)
        prior: dict[str, object] = {}
        for spec in get_pipeline_phases():
            if time.monotonic() - t0 >= MAX_TOTAL_SEC:
                logger.warning("[pipeline] 超时 300s,剩余阶段占位降级")
                ctx.partial_phases.append(spec.phase.value)
                ctx.phase_results[spec.phase.value] = _partial("timeout")
                continue
            logger.info(
                "[pipeline] 进入阶段 %s (%.0fs 已用)", spec.phase.value, time.monotonic() - t0
            )
            try:
                result = await asyncio.wait_for(
                    self._call_phase(spec.phase, si, stock_md, prior, reports, financial_md_path),
                    timeout=spec.timeout_sec,
                )
            except TimeoutError:
                logger.warning("[pipeline] 阶段 %s 超时(%ds)", spec.phase.value, spec.timeout_sec)
                result = _partial("timeout")
            except Exception as e:  # broad tolerance: never abort the pipeline
                logger.warning(
                    "[pipeline] 阶段 %s 异常 %s: %s", spec.phase.value, type(e).__name__, e
                )
                result = _partial(f"{type(e).__name__}")
            ctx.phase_results[spec.phase.value] = result
            if result.get("partial"):
                ctx.partial_phases.append(spec.phase.value)
            prior[spec.phase.value] = result.get("output", "")
        return ctx.to_dict()

    # ---- per-phase dispatch ----------------------------------------------

    async def _call_phase(
        self,
        phase: Phase,
        si: StockAnalysis,
        stock_md: str,
        prior: dict[str, object],
        reports: dict | None,
        financial_md_path: Path | None,
    ) -> dict[str, object]:
        """Route a phase to its implementation. Unknown phases degrade."""
        if phase == Phase.PLAN:
            return await self._phase_plan(stock_md, prior)
        if phase == Phase.COLLECT:
            return await self._phase_collect(si, stock_md, prior)
        if phase == Phase.ANALYSIS:
            return await self._phase_analysis(si, stock_md, prior)
        if phase == Phase.SELF_CHECK:
            return await self._phase_self_check(stock_md, prior)
        # COMPILE filled in Task 15; placeholder for now.
        return {"output": "", "tool_results": {}, "partial": True, "note": "not_implemented"}

    async def _phase_plan(
        self,
        stock_md: str,
        prior: dict[str, object],
    ) -> dict[str, object]:
        """PLAN: 仅 LLM,模型可补 web_search,最多 2 轮。"""
        system = phase_system_prompt(Phase.PLAN, stock_md, prior)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": stock_md},
        ]
        text, tool_results = await self._web_search_loop(messages, max_rounds=2)
        return {"output": text, "tool_results": tool_results, "partial": False}

    async def _phase_collect(
        self,
        si: StockAnalysis,
        stock_md: str,
        prior: dict[str, object],
    ) -> dict[str, object]:
        """COLLECT: 并行跑 technicals / financial_temporal / peer_compare,注入 tool_result,
        再调 LLM 一轮(模型可补一次 web_search)。"""
        from ..web_search_tool import execute_web_search

        # 1. 并行纯工具(含 peer_compare 的 search 组合)
        tech_coro = _run_safe(TOOL_RUNNERS["technicals"], si.kline, si.capital_flow)
        fin_coro = _run_safe(TOOL_RUNNERS["financial_temporal"], si.history_financial)
        peer_coro = _run_peer_compare(si, execute_web_search)
        tech, fin, peer = await asyncio.gather(tech_coro, fin_coro, peer_coro)

        tool_results: dict[str, object] = {
            "technicals": tech,
            "financial_temporal": fin,
            "peer_compare": peer,
        }
        partial = any(_is_partial(v) for v in tool_results.values())

        # 2. 拼装注入 tool_result 的 messages
        system = phase_system_prompt(Phase.COLLECT, stock_md, prior)
        injected = _tool_results_to_messages(tool_results)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": stock_md},
            *injected,
        ]
        # 3. LLM 一轮,模型可补一次 web_search
        text, search_results = await self._web_search_loop(messages, max_rounds=1)
        tool_results.update(search_results)
        return {"output": text, "tool_results": tool_results, "partial": partial}

    async def _phase_analysis(
        self,
        si: StockAnalysis,
        stock_md: str,
        prior: dict[str, object],
    ) -> dict[str, object]:
        """ANALYSIS: 并行跑 risk_quant / valuation / business_moat,注入 tool_result,
        再调 LLM 一轮(模型可补一次 web_search)。"""
        collect_output = _phase_output(prior.get("collect"))
        fin_temporal = _nested(collect_output, "tool_results", "financial_temporal")
        peer_comp = _nested(collect_output, "tool_results", "peer_compare")

        risk_coro = _run_safe(TOOL_RUNNERS["risk_quant"], fin_temporal, si.quote)
        val_coro = _run_safe(TOOL_RUNNERS["valuation"], fin_temporal, peer_comp, si.quote)
        moat_coro = _run_safe(
            TOOL_RUNNERS["business_moat"],
            si.financial,
            si.research,
            si.social_posts,
            si.history_financial,
        )
        risk, val, moat = await asyncio.gather(risk_coro, val_coro, moat_coro)

        tool_results: dict[str, object] = {
            "risk_quant": risk,
            "valuation": val,
            "business_moat": moat,
        }
        partial = any(_is_partial(v) for v in tool_results.values())

        system = phase_system_prompt(Phase.ANALYSIS, stock_md, prior)
        injected = _tool_results_to_messages(tool_results)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": stock_md},
            *injected,
        ]
        text, search_results = await self._web_search_loop(messages, max_rounds=1)
        tool_results.update(search_results)
        return {"output": text, "tool_results": tool_results, "partial": partial}

    async def _phase_self_check(
        self,
        stock_md: str,
        prior: dict[str, object],
    ) -> dict[str, object]:
        """SELF_CHECK: 5 项 JSON 校验闭环。

        把 ANALYSIS 草稿 + 强制校验清单喂给模型,要求返回结构化 JSON
        {citations_ok, tables_ok, trigger_ok, advice_ok, norepeat_ok, fixes_needed[]}。
        JSON 中任一项 false → 把 fixes_needed 注入 ANALYSIS 重跑一段(至多 1 次循环);
        解析失败 → 重试一次(带错误提示) → 再失败降级 __partial__。
        """
        analysis_output = _phase_output_text(prior.get("analysis"))
        schema_hint = (
            "基于下方草稿与强制校验清单,仅输出 JSON Schema:\n"
            '{"citations_ok": bool, "tables_ok": bool, "trigger_ok": bool, '
            '"advice_ok": bool, "norepeat_ok": bool, "fixes_needed": [str]}\n'
            "每项 false 必须列出 fixes_needed。"
        )
        system = phase_system_prompt(Phase.SELF_CHECK, stock_md, prior)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"# 分析草稿\n\n{analysis_output}"},
            {"role": "user", "content": schema_hint},
        ]
        check, result = await self._run_self_check(messages)
        if check is None:
            return result  # already degraded to __partial__
        # Gate: any false -> feed fixes back into ANALYSIS once, then re-check loosely.
        fixes_raw = check.get("fixes_needed")
        fixes = (
            [str(f) for f in fixes_raw if isinstance(f, str)]
            if isinstance(fixes_raw, list)
            else []
        )
        gates_pass = bool(
            check.get("citations_ok")
            and check.get("tables_ok")
            and check.get("trigger_ok")
            and check.get("advice_ok")
            and check.get("norepeat_ok")
        )
        if not gates_pass and fixes:
            await self._reanalysis_with_fixes(stock_md, prior, fixes)
        return result

    async def _run_self_check(
        self, messages: list[dict]
    ) -> tuple[dict[str, object] | None, dict[str, object]]:
        """请求并解析一次 SELF_CHECK JSON,解析失败重试一次。

        返回 (parsed_check | None, phase_result)。None 表示校验不可用(已降级)。
        """
        partial_result: dict[str, object] = {
            "output": "",
            "tool_results": {},
            "partial": True,
            "checks": {},
        }
        parse_err: str | None = None
        for attempt in range(2):
            msgs = list(messages)
            if parse_err is not None:
                msgs.append(
                    {
                        "role": "user",
                        "content": (
                            f"上次输出不是合法 JSON,解析错误: {parse_err}。"
                            "请严格输出仅 JSON,不要 fences,不要解释。"
                        ),
                    }
                )
            text = await self._phase_self_check_request(msgs)
            parsed, err = _parse_self_check_json(text)
            if parsed is not None:
                partial_result["partial"] = False
                partial_result["checks"] = parsed
                partial_result["output"] = text
                return parsed, partial_result
            parse_err = err
        return None, partial_result

    async def _phase_self_check_request(self, messages: list[dict]) -> str:
        """一次非流式 LLM 调用,只返文本(禁用工具避免模型再发车)。"""
        message = await self._llm_chat(messages, tools=None, tool_choice="none")
        return (message.get("content") or "").strip()

    async def _reanalysis_with_fixes(
        self, stock_md: str, prior: dict[str, object], fixes: list[str]
    ) -> str:
        """把 fixes_needed 注入 ANALYSIS prompt 重跑一轮,返回新草稿文本(失败降级)。"""
        fix_note = (
            "以下校验未通过,请直接修改草稿以解决这些点,仅输出修改后的完整 Markdown 草稿:\n"
            + "\n".join(f"- {f}" for f in fixes)
        )
        analysis_output = _phase_output_text(prior.get("analysis"))
        system = phase_system_prompt(Phase.ANALYSIS, stock_md, prior)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"# 当前草稿\n\n{analysis_output}"},
            {"role": "user", "content": fix_note},
        ]
        try:
            text, _ = await self._web_search_loop(messages, max_rounds=1)
            return text or analysis_output
        except Exception as e:  # broad tolerance: keep original draft on failure
            logger.warning("[pipeline] reanalysis_with_fixes 失败 %s: %s", type(e).__name__, e)
            return analysis_output

    # ---- LLM helpers ------------------------------------------------------

    async def _web_search_loop(
        self, messages: list[dict], max_rounds: int
    ) -> tuple[str, dict[str, object]]:
        """最多 ``max_rounds`` 轮 LLM 调用;模型若调 web_search 则执行并追加结果。

        返回 (最终文本, {web_search: 最后一次搜索结果})。
        """
        from ..web_search_tool import execute_web_search, get_tool_definitions

        tool_results: dict[str, object] = {}
        last_text = ""
        tools = get_tool_definitions()  # 仅 web_search 对模型可见
        for _ in range(max_rounds):
            message = await self._llm_chat(messages, tools=tools, tool_choice="auto")
            content = (message.get("content") or "").strip()
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                last_text = content
                break
            messages.append(message)
            for tc in tool_calls:
                fn = tc["function"]
                fn_name = fn.get("name", "")
                try:
                    fn_args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    fn_args = {}
                if fn_name == "web_search":
                    query = fn_args.get("query", "")
                    print(f"\n 🔍 联网搜索: {query}")
                    result = await execute_web_search(query)
                    print(f"    → 获取到 {len(result)} 字符结果")
                    tool_results.setdefault("web_search", "")
                    tool_results["web_search"] = result
                else:
                    result = f"[unsupported tool: {fn_name}]"
                messages.append(
                    {"role": "tool", "tool_call_id": tc.get("id", ""), "content": result}
                )
            # 一轮 web_search 后继续循环,让模型基于结果输出文本
        return last_text, tool_results

    async def _llm_chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> dict:
        """一次非流式 LLM 调用,返回 message dict(content + 可选 tool_calls)。"""
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model,
            "messages": messages,
            "temperature": settings.deepseek_temperature,
            "max_tokens": settings.deepseek_max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        resp = await analyzer._http.post(
            analyzer.api_url,
            headers={
                "Authorization": f"Bearer {analyzer.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]

    # ---- context rendering ------------------------------------------------

    def _render_stock_context(
        self,
        si: StockAnalysis,
        reports: dict | None,
        financial_md_path: Path | None,
    ) -> str:
        """把 StockAnalysis 渲染为给 LLM 的用户消息文本。

        复用 analyzer._build_data_dict 保持与旧链路一致的字段集合。
        """
        data = self.analyzer._build_data_dict(si, reports, financial_md_path)
        lines: list[str] = [f"# 标的快照 {si.name or si.symbol}"]
        quote = data.get("quote") or {}
        if quote.get("price"):
            lines.append(
                f"- 最新价: {quote.get('price')} | 涨跌: {quote.get('change_pct')}% | PE: {quote.get('pe')}"  # noqa: E501
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
                f"- K线: {kline.get('bar_count')}根,最新 {kline.get('latest_close')} ({kline.get('latest_date')})"  # noqa: E501
            )
        if data.get("industry"):
            lines.append(f"- 行业: {data['industry']}")
        return "\n".join(lines)


# ---- module-level helpers -------------------------------------------------


def _partial(reason: str) -> dict[str, object]:
    return {"output": "", "tool_results": {}, "partial": True, "reason": reason}


def _is_partial(tool_value: object) -> bool:
    return isinstance(tool_value, dict) and "__partial__" in tool_value


def _phase_output_text(phase_result: object) -> str:
    """从 prior 的阶段结果中安全提取 output 文本。"""
    if isinstance(phase_result, dict) and isinstance(phase_result.get("output"), str):
        return phase_result["output"]
    return ""


async def _run_safe(fn, *args) -> dict[str, object]:
    """运行单个工具,任何异常都降级为 ``{"__partial__": <reason>}``。"""
    try:
        result = fn(*args)
        return result if isinstance(result, dict) else {"__partial__": "bad_return"}
    except Exception as e:  # broad tolerance
        logger.warning(
            "[pipeline] 工具 %s 异常 %s: %s", getattr(fn, "__name__", fn), type(e).__name__, e
        )
        return {"__partial__": f"{type(e).__name__}"}


async def _run_peer_compare(
    si: StockAnalysis, search_fn: Callable[[str], Any]
) -> dict[str, object] | list[dict[str, object]]:
    """组合 peer_compare: build_search_query → search → parse。"""
    from ..tools.peer_compare import build_search_query, parse

    try:
        query = build_search_query(si.name)
        if not query or search_fn is None:
            return {"__partial__": "no_query", "peers": [], "industry": ""}
        result = search_fn(query)
        if asyncio.iscoroutine(result):
            result = await result
        return parse(result or "", si.financial)
    except Exception as e:  # broad tolerance
        logger.warning("[pipeline] peer_compare 异常 %s: %s", type(e).__name__, e)
        return {"__partial__": f"{type(e).__name__}", "peers": [], "industry": ""}


def _tool_results_to_messages(tool_results: dict[str, object]) -> list[dict]:
    """把 {name: result} 转成 role=tool 注入消息列表。"""
    messages: list[dict] = []
    for name, value in tool_results.items():
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"srv_{name}",
                "name": name,
                "content": json.dumps(value, ensure_ascii=False, default=str),
            }
        )
    return messages


def _phase_output(phase_result: object) -> dict[str, object]:
    """从 prior 的阶段结果中安全提取 output/tool_results 映射。"""
    if not isinstance(phase_result, dict):
        return {}
    tr = phase_result.get("tool_results")
    return tr if isinstance(tr, dict) else {}


_SELF_CHECK_KEYS = ("citations_ok", "tables_ok", "trigger_ok", "advice_ok", "norepeat_ok")


def _parse_self_check_json(text: str) -> tuple[dict[str, object] | None, str | None]:
    """解析 SELF_CHECK 模型输出为结构化 JSON。

    自动剥离 ``` fences;字段缺失按 false 处理。返回 (parsed, error_msg)。
    """
    if not text:
        return None, "empty"
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        return None, str(e)
    if not isinstance(data, dict):
        return None, "not_a_object"
    parsed: dict[str, object] = {
        k: bool(data.get(k, False)) for k in _SELF_CHECK_KEYS
    }
    fixes = data.get("fixes_needed", [])
    parsed["fixes_needed"] = fixes if isinstance(fixes, list) else []
    return parsed, None


def _nested(data: dict[str, object] | None, *keys: str) -> Any:
    """按 keys 链安全取值,任意一环缺失返 None。"""
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur
