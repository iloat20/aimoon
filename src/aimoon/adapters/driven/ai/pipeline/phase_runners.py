"""Phase runners & tool-context gathering for the v2 pipeline.

Extracted from ``orchestrator.py`` (architecture review 2026-07-19, #4) so the
orchestrator becomes a thin sequencing facade. Each function takes the
``PipelineOrchestrator`` instance (``orch``) for the few pieces that must stay
methods on that class (the LLM transport delegates that tests monkeypatch), and
otherwise operates on plain domain inputs.

No behaviour change vs the original methods — bodies are preserved verbatim.
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback

import httpx

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.entities.research import ResearchReportData

from ...config.settings import get_settings, resolve_ai_provider
from ..xml_utils import strip_xml_tool_calls
from ._helpers import _build_assertable_facts, _verify_and_fix
from .phases import Phase, phase_system_prompt
from .skeleton_validator import validate_skeleton
from .table_renderer import (
    render_annual_report_footnotes,
    render_channel_proxy,
    render_fcf_dividend,
    render_financial_health_ext,
    render_financial_temporal,
    render_margin_of_safety,
    render_margin_of_safety_cards,
    render_peer_comparison,
    render_quarterly_breakdown,
    render_region_breakdown,
    render_segment_revenue,
    render_sentiment,
)
from .timing import logphase
from .tool_summaries import (
    extract_tool_summary,
    fcf_summary,
    research_divergence,
)
from .tool_summaries import (
    senti_summary as sentiment_summary,
)
from .types import PipelineContext, _ToolContext
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
    run_safe as _run_safe,
)

logger = logging.getLogger(__name__)

# 各阶段 LLM 调用超时(与 orchestrator 常量保持一致,避免重复定义漂移)
DIRECT_TIMEOUT = 600


async def gather_tool_context(
    si: StockAnalysis, stock_md: str, prior: dict,
) -> _ToolContext:
    """并行跑 9 个工具 → 0-LLM 渲染表格 + 摘要 → 拼 user 消息主体。

    无任何 LLM 调用。产物同时供 ANALYSIS(骨架)与 DIRECT(直出)两条流。
    会写入 ``prior['tables_md']`` / ``prior['summary']``。
    """
    from ..web_search_tool import execute_web_search

    # TOOL_RUNNERS / _run_peer_compare 必须经由 orchestrator 模块读取,而非直接
    # import,因为部分测试会在 orch_mod 上 monkeypatch 这两个名字(见
    # test_orchestrator_wiring / test_pipeline_phases)。函数体在内层延迟引用,
    # 由 orchestrator 重新导出这些名字,确保补丁生效。
    from . import orchestrator as _orch_mod

    runners = _orch_mod.TOOL_RUNNERS
    run_peer_compare_fn = _orch_mod._run_peer_compare

    quote = si.quote or StockQuote()

    # 1. 并行跑 5 个工具(批1)
    tech_coro = _run_safe(
        runners["technicals"], getattr(si, "kline", None),
        getattr(si, "capital_flow", None),
    )
    fin_coro = _run_safe(
        runners["financial_temporal"], getattr(si, "history_financial", None),
    )
    moat_coro = _run_safe(
        runners["business_moat"],
        getattr(si, "financial", None),
        getattr(si, "research", None),
        getattr(si, "social_posts", None),
        getattr(si, "history_financial", None),
    )
    peer_coro = run_peer_compare_fn(si, execute_web_search)
    senti_coro = _run_safe(runners["sentiment"], getattr(si, "social_posts", None))
    with logphase("tools(tech+fin+moat+peer+senti)"):
        tech, fin, moat, peer, senti = await asyncio.gather(
            tech_coro, fin_coro, moat_coro, peer_coro, senti_coro,
        )

    # 2. 批2: risk + valuation(安全边际) + fcf 并行
    with logphase("tools(risk+val+fcf)"):
        risk_task = asyncio.create_task(
            _run_safe(runners["risk_quant"], fin, quote)
        )
        val_task = asyncio.create_task(
            _run_safe(
                runners["valuation"],
                fin,
                quote,
                peer,
                getattr(si, "financial", None),
            )
        )
        fcf_task = asyncio.create_task(
            _run_safe(
                runners["fcf_dividend"],
                fin, getattr(si, "financial", None), quote,
            )
        )
        risk = await risk_task
        val = await val_task
        fcf = await fcf_task

    tool_results: dict[str, object] = {
        "technicals": tech, "financial_temporal": fin, "peer_compare": peer,
        "risk_quant": risk, "valuation": val, "business_moat": moat,
        "sentiment": senti, "fcf_dividend": fcf,
    }
    partial = any(_is_partial(v) for v in tool_results.values())

    # 3. 渲染核心表格(Python 模板,0 LLM token)+ 工具摘要
    tables_md = "\n\n".join(
        [
            render_financial_temporal(fin),
            render_peer_comparison(peer),
            render_margin_of_safety(val),
            render_fcf_dividend(fcf),
            render_sentiment(senti),
            render_financial_health_ext(
                getattr(si, "financial", None), fcf,
            ),
            render_segment_revenue(getattr(si, "financial", None)),
            render_annual_report_footnotes(getattr(si, "financial", None)),
            render_quarterly_breakdown(getattr(si, "financial", None)),
            render_region_breakdown(getattr(si, "financial", None)),
            render_channel_proxy(getattr(si, "financial", None)),
        ]
    )
    prior["tables_md"] = tables_md
    # 估值情景卡片(可信 HTML,前端以 |safe 注入;与 tables_md 同源,失败返回 "")。
    prior["margin_of_safety_html"] = render_margin_of_safety_cards(val)
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

    body = (
        f"{stock_md}\n\n"
        f"# 已渲染表格\n{tables_md}\n\n"
        f"# 工具摘要\n{summary}\n\n"
        f"# 已采集社交媒体舆情(共 {len(si.social_posts)} 条)\n{social_summary}\n"
        f"{senti_summary_txt}\n\n"
        f"# 自由现金流与股息(系统计算)\n{fcf_summary_txt}\n\n"
        f"# 已采集机构研报(共 "
        f"{(si.research or ResearchReportData()).total_count} 篇)\n{research_summary}\n"
        f"{research_div}\n\n"
    )
    return _ToolContext(
        tool_results=tool_results, partial=partial,
        tables_md=tables_md, summary=summary, body=body,
    )


async def gather_catalysts(si: StockAnalysis) -> str:
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


async def run_direct(
    orch: object,
    si: StockAnalysis,
    stock_md: str,
    prior: dict,
    ctx: PipelineContext,
) -> dict[str, object]:
    """直出流入口: 采集 → 一次 LLM 直出完整报告 → 填充 ctx。"""
    try:
        result = await asyncio.wait_for(
            phase_direct(orch, si, stock_md, prior),
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
    ctx.margin_of_safety_html = str(prior.get("margin_of_safety_html") or "")
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
                "AI 直出阶段未产出正文,以下为系统预渲染数据(见下方「数据底稿」)。"
            )
        else:
            ctx.final_markdown = "# 分析报告（降级）\n\n数据采集或分析暂不可用。"
    return ctx.to_dict()


async def phase_direct(
    orch: object,
    si: StockAnalysis,
    stock_md: str,
    prior: dict,
) -> dict:
    """采集工具上下文后,一次流式 LLM 调用直出完整 Markdown 报告。

    使用 ``deepseek_analysis_effort`` 思考强度 + ``deepseek_max_tokens``
    输出上限(长文需要充足额度)。不产出 JSON 骨架,不做二次扩写。

    直出完成后做 0-LLM 数字对账 + 可选 LLM 定点重写(非阻断护栏),
    并把可信度摘要随结果透传。
    """
    tc = await gather_tool_context(si, stock_md, prior)

    # 可选: 实时检索近期催化注入(默认关,开启才有行为变化)
    user_body = tc.body
    settings = get_settings()
    cfg = resolve_ai_provider(settings)
    if settings.direct_web_search_enabled:
        catalyst = await gather_catalysts(si)
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

    effort = cfg.analysis_effort
    # 尊重 *_thinking_enabled 开关 (原硬编码 True 使关思考省钱对默认 DIRECT 流失效)。
    # None = 走 provider 官方默认(开启); False = 显式关思考(思考 token 按输出计价, 是主成本)。
    thinking = cfg.thinking_enabled
    text = ""
    for _attempt in range(2):
        try:
            text = await orch._stream_llm_content(  # type: ignore[attr-defined]
                messages,
                max_tokens=cfg.max_tokens,
                reasoning_effort=effort,
                thinking=thinking,
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
    batch_llm = orch._make_rewrite_batch_llm()  # type: ignore[attr-defined]
    fixed_text, credibility_summary = _verify_and_fix(
        stripped, facts, batch_llm=batch_llm
    )

    return {
        "output": fixed_text,
        "tool_results": tc.tool_results,
        "partial": tc.partial,
        "credibility": credibility_summary,
    }


async def phase_analysis(
    orch: object,
    si: StockAnalysis,
    stock_md: str,
    prior: dict,
    reports: dict | None,
    financial_md_path: object | None,
) -> dict:
    """Phase 1: 并行工具 + LLM 输出 JSON 骨架。"""
    tc = await gather_tool_context(si, stock_md, prior)
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

    # 短期跨 run 缓存(缓存骨架 JSON 文本)。
    # 必须用独立的 skeleton 缓存, 不能复用 analysis:* (终稿缓存) —— 否则终稿会覆盖骨架,
    # 使复跑把终稿当骨架 → 解析失败 → 静默降级(2026-07-14 修复的 key 碰撞)。
    from ..cache import get_skeleton_cache, set_skeleton_cache

    cached_skeleton = get_skeleton_cache(si.symbol) or ""
    # 仅当缓存内容确为合法骨架 JSON 时才信任并跳过 LLM; 否则重新生成。
    if cached_skeleton and _parse_skeleton_json(cached_skeleton) is None:
        logger.warning("[pipeline] 缓存骨架非合法 JSON,忽略并重新生成")
        cached_skeleton = ""
    skeleton_text = cached_skeleton
    if not skeleton_text:
        settings = get_settings()
        analysis_cfg = resolve_ai_provider(settings)
        analysis_effort = analysis_cfg.analysis_effort
        analysis_max_tokens = analysis_cfg.analysis_max_tokens
        for _attempt in range(3):
            try:
                message = await orch._call_llm_with_stream(  # type: ignore[attr-defined]
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
            set_skeleton_cache(si.symbol, skeleton_text)
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


async def phase_self_check(skeleton: dict, tables_md: str = "") -> dict[str, object]:
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


async def phase_compile(
    orch: object,
    stock_md: str,
    prior: dict,
) -> dict:
    """Phase 2: 基于骨架扩写终稿。

    纯扩写/格式化,不做二次推理 —— 关闭思考模式(thinking=disabled)以省下
    全部思考 token(思考 token 按输出计价,是主要成本)。关闭后 temperature
    恢复生效(默认 0.3,保证行文自然)。

    为命中 DeepSeek 前缀缓存,system prompt 只放 compile.md 固定规则(稳定前缀),
    本次标的的可变数据(快照/系统表/骨架/摘要/自检)全部放到 user 消息里。
    """
    skeleton = prior.get("skeleton") or {}
    if not skeleton:
        return {"output": "", "tool_results": {}, "partial": True, "reason": "no_skeleton"}
    system = phase_system_prompt(Phase.COMPILE, stock_md, prior)
    tables_md = str(prior.get("tables_md", "") or "")
    summary = str(prior.get("summary") or prior.get("tools_summary") or "")
    self_check_fixes = prior.get("self_check_fixes") or []
    user_parts = [
        "# 标的快照",
        stock_md or "(无)",
        "",
        "# 系统预渲染数据(权威数字来源,每个数字必须与此一致,禁止编造)",
        tables_md or "(无)",
        "",
        "# 推理骨架(JSON,权威结论,扩写的唯一依据)",
        f"```json\n{json.dumps(skeleton, ensure_ascii=False, indent=2)}\n```",
    ]
    if summary.strip():
        user_parts += ["", "# 上游工具摘要(可选参考)", summary]
    if self_check_fixes:
        user_parts += [
            "", "# 自检备注(如有)",
            json.dumps(self_check_fixes, ensure_ascii=False),
        ]
    user_parts += [
        "", "请基于以上骨架与数据,并遵循系统提示中的规则,扩写为完整的投资策略报告。",
    ]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
    text = ""
    try:
        for _attempt in range(2):
            try:
                text = await orch._stream_llm_content(  # type: ignore[attr-defined]
                    messages, thinking=False
                )
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
