"""Pipeline 编排器的模块级私有 helper(结构拆分自 ``orchestrator.py``)。

把 _phase_* 之外的大型纯函数(数字对账事实抽取、Gordon 反推一致性检查、
重写 LLM 同步封装、0-LLM 数字对账 + 定点重写护栏)抽到本子模块。
本模块是这些符号的权威定义处;外部(含测试)应直接 ``from ._helpers import ...``,
``orchestrator`` 不再为其做再导出(架构审查 #8 已去除测试对私有符号的耦合)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import httpx

from aimoon.adapters.driven.config.settings import get_settings

from .llm_client import PipelineLlmClient
from .report_reconciler import reconcile
from .self_check_rewrite import self_check_rewrite

if TYPE_CHECKING:
    from .types import _ToolContext

logger = logging.getLogger(__name__)


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

    注: 目标价(target_base)已被整体移除——本报告严禁输出任何目标价,
    故不再作为可断言事实。report_reconciler 仍把「目标价」映射为
    target_base 并因 facts 无此键而判为 critical(虚构指标),这正是护栏。

    当前 ``_ToolContext`` 不含 quote 实体,现价 ``price`` 无法抽取,故跳过。
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

    # 财务明细是否已由代码确定性计算(capex/FCF/分红/净利/应收账款):
    # fcf_dividend 的 dividend_paid 齐备即作"财务明细已核验"的代理信号。
    fcf_fv = tr.get("fcf_dividend")
    financial_verified = bool(
        isinstance(fcf_fv, dict) and _is_number(fcf_fv.get("dividend_paid"))
    )
    facts["financial_verified"] = financial_verified

    return facts


def _run_rewrite_llm(client: PipelineLlmClient, system: str, user: str) -> str:
    """同步执行一次非流式 LLM 调用(thinking=False),用于定点重写。

    在独立线程里跑新事件循环 ``asyncio.run``,既不阻塞主事件循环、也不与主循环
    的 ``run_until_complete`` 冲突。任何异常返回空串,交给 ``self_check_rewrite``
    的安全护栏决定保留原文。

    关键: 线程内**新建**一个临时 ``httpx.AsyncClient`` 并在同一线程循环内用完即关,
    绝不复用 orchestrator 主循环持有的共享 client。否则跨事件循环使用同一 httpx
    连接池会污染共享 client(``RuntimeError: Event loop is closed``),进而在 run()
    的 ``finally: aclose()`` 处抛错、把已生成的完整报告整体丢弃(降级 bug)。
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    analyzer = client.analyzer

    def _work() -> str:
        async def _go() -> str:
            # 线程私有 client: 绑定本线程的新循环,用完即关,不触碰共享 client。
            async with httpx.AsyncClient(timeout=120.0) as http:
                fresh = PipelineLlmClient(analyzer, http_client=http)
                msg = await fresh.call_llm_with_stream(messages, thinking=False)
                return (msg.get("content") or "").strip()

        try:
            return asyncio.run(_go())
        except Exception as e:  # noqa: BLE001 - 安全降级返回空串
            logger.warning("[verify] rewrite llm 异常 %s: %s", type(e).__name__, e)
            return ""

    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_work).result()


def _run_rewrite_llm_batch(
    client: PipelineLlmClient, prompts: list[tuple[str, str]]
) -> list[str]:
    """批量执行定点重写 LLM 调用：单线程 + 单事件循环 + 单临时 httpx client，
    N 条 prompt 并发 ``gather``。

    相比逐条 ``_run_rewrite_llm``（每条各起一次 ThreadPoolExecutor + 新事件循环 +
    新 httpx client），本函数只创建一次线程/循环/client，把 N-1 次固定开销省掉；
    且 N 条互相独立的改正请求并发执行，wall-clock 从 N×latency 降到 ~1×latency。

    线程私有 client 的理由同 ``_run_rewrite_llm``：绝不复用 orchestrator 主循环
    持有的共享 client，避免跨事件循环污染连接池（``Event loop is closed``）导致
    finally: aclose() 抛错、把完整报告整体丢弃。任一条异常降级为空串，交给
    ``self_check_rewrite`` 的安全护栏决定保留原文；返回列表与 prompts 等长。
    """
    if not prompts:
        return []
    analyzer = client.analyzer

    def _work() -> list[str]:
        async def _one(fresh: PipelineLlmClient, system: str, user: str) -> str:
            try:
                msg = await fresh.call_llm_with_stream(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    thinking=False,
                )
                return (msg.get("content") or "").strip()
            except Exception as e:  # noqa: BLE001 - 单条降级返回空串
                logger.warning(
                    "[verify] batch rewrite 单条异常 %s: %s", type(e).__name__, e
                )
                return ""

        async def _go() -> list[str]:
            # 线程私有 client: 绑定本线程新循环,用完即关,不触碰共享 client。
            async with httpx.AsyncClient(timeout=120.0) as http:
                fresh = PipelineLlmClient(analyzer, http_client=http)
                return list(
                    await asyncio.gather(*(_one(fresh, s, u) for s, u in prompts))
                )

        try:
            return asyncio.run(_go())
        except Exception as e:  # noqa: BLE001 - 安全降级:整批返回空串
            logger.warning(
                "[verify] batch rewrite llm 异常 %s: %s", type(e).__name__, e
            )
            return ["" for _ in prompts]

    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_work).result()


def _verify_and_fix(
    report_md: str,
    facts: dict[str, object],
    *,
    llm: Callable[[str, str], str] | None = None,
    batch_llm: Callable[[list[tuple[str, str]]], list[str]] | None = None,
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
        # 财务明细是否已代码确定性计算(capex/FCF/分红/应收账款),驱动页脚动态文案。
        fv = facts.get("financial_verified")
        if isinstance(fv, bool):
            summary["financial_verified"] = fv

        # critical（虚构指标）永不自动重写，仅标记 uncertain 让用户可见。
        uncertain: list[str] = []
        critical = [m for m in res.mismatches if m.severity == "critical"]
        fixable = [m for m in res.mismatches if m.severity != "critical"]
        if critical:
            uncertain.extend(m.snippet for m in critical)

        if fixable and get_settings().self_check_rewrite_enabled:
            fixed = self_check_rewrite(
                report_md, fixable, facts, llm=llm, batch_llm=batch_llm
            )
            actually_fixed = sum(1 for m in fixable if m.snippet not in fixed)
            summary["corrected"] = actually_fixed
            report_md = fixed
        elif fixable:
            # 重写关闭时，可修复疑点也标记 uncertain 让用户知悉。
            uncertain.extend(m.snippet for m in fixable)

        if uncertain:
            summary["uncertain"] = uncertain

        return (report_md, summary)
    except Exception as e:  # noqa: BLE001 - 任何异常都保底返回原报告
        logger.warning("[verify] _verify_and_fix 异常 %s: %s", type(e).__name__, e)
        return (report_md, {"skipped": "verify crashed"})
