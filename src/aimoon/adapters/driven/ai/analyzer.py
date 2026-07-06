"""DeepSeek AI analysis engine.

DeepSeekAIAnalyzer — orchestrates data -> AnalysisReport.
Handles streaming SSE collection, tool-calling, and user message construction.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from aimoon.core.application.ports import AIAnalyzer as AIAnalyzerPort
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport

from ..config.settings import get_settings
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .web_search_tool import execute_web_search, get_tool_definitions

_MAX_TOOL_ROUNDS = 0

# 行业关键词映射
_INDUSTRY_KEYWORDS = {
    "银行": ["银行", "工商银行", "建设银行", "农业银行", "招商银行", "兴业银行"],
    "地产": ["地产", "万科", "保利", "恒大", "碧桂园", "融创"],
    "消费": ["茅台", "五粮液", "泸州老窖", "伊利", "蒙牛", "海天"],
    "家电": ["格力", "美的", "海尔", "海信", "TCL", "长虹"],
    "科技": ["华为", "小米", "联想", "中兴", "立讯", "歌尔"],
    "医药": ["恒瑞", "药明", "迈瑞", "片仔癀", "云南白药"],
    "能源": ["中石油", "中石化", "中海油", "神华", "宁德时代"],
    "汽车": ["比亚迪", "长城", "吉利", "蔚来", "小鹏", "理想"],
}


def _detect_industry(symbol: str, name: str) -> str:
    """根据公司名称检测行业。"""
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        for kw in keywords:
            if kw in name:
                return industry
    if symbol.startswith("6"):
        return "沪市"
    elif symbol.startswith(("0", "3")):
        return "深市"
    else:
        return "北交所"


def _parse_xml_tool_calls(content: str) -> list[dict]:
    """Parse XML-style tool calls from model content as fallback.

    Handles DeepSeek's <｜｜DSML｜｜tool_calls> markup format.
    Returns list of {name, arguments} dicts.
    """
    calls: list[dict] = []
    for m in re.finditer(
        r'<｜｜DSML｜｜invoke\s+name="([^"]+)">(.*?)</｜｜DSML｜｜invoke>',
        content,
        re.DOTALL,
    ):
        fn_name = m.group(1)
        param_block = m.group(2)
        param_m = re.search(
            r'<｜｜DSML｜｜parameter\s+name="query"[^>]*>(.*?)</｜｜DSML｜｜parameter>',
            param_block,
            re.DOTALL,
        )
        query = param_m.group(1).strip() if param_m else ""
        calls.append({"name": fn_name, "arguments": json.dumps({"query": query})})
    return calls


def _strip_xml_tool_calls(text: str) -> str:
    """Remove XML-style tool call markup from response text."""
    text = re.sub(
        r"<｜｜DSML｜｜tool_calls>.*?</｜｜DSML｜｜tool_calls>", "", text, flags=re.DOTALL
    )
    text = re.sub(r"<｜｜DSML｜｜invoke.*?</｜｜DSML｜｜invoke>", "", text, flags=re.DOTALL)
    return text.strip()


def _build_fallback_report(stock_info: StockAnalysis) -> str:
    """Generate a degraded Markdown summary when the v2 pipeline produces no text.

    Never raises: returns a short, always-valid Markdown string assembled from
    whatever domain data is available.
    """
    lines = [f"# {stock_info.name or stock_info.symbol} 分析（降级）"]
    quote = stock_info.quote
    if quote and quote.price:
        lines.append(f"- 最新价: {quote.price} | 涨跌: {quote.change_pct}%")
    fin = stock_info.financial
    if fin and fin.report_period:
        lines.append(f"- 报告期: {fin.report_period}")
    lines.append("\n> 本次分析未能生成完整报告，以下为已采集基础数据汇总。")
    return "\n".join(lines)


def _deduplicate_tail(text: str) -> str:
    """Remove repeated blocks at the end of the response.

    Handles two cases:
    1. Last N paragraphs identical to preceding N paragraphs
    2. Repeated text within the response (e.g., model outputs conclusion twice)
    """
    # Case 1: Deduplicate repeated paragraphs
    paragraphs = re.split(r"\n\n+", text)
    if len(paragraphs) >= 4:
        for size in range(1, len(paragraphs) // 2 + 1):
            candidate = paragraphs[-size:]
            prev = paragraphs[-(2 * size) : -size]
            if [p.strip() for p in candidate] == [p.strip() for p in prev]:
                text = "\n\n".join(paragraphs[: -(size)])
                paragraphs = re.split(r"\n\n+", text)

    # Case 2: Deduplicate repeated text blocks within the response
    # Find repeated substrings of 50+ chars at the end
    for length in range(len(text) // 3, 50, -1):
        tail = text[-length:]
        # Find where this block first appears
        first_pos = text.find(tail)
        if first_pos >= 0 and first_pos + length < len(text):
            # Block appears earlier and is repeated at end
            # Remove the duplicate (keep first occurrence)
            return text[:first_pos + length]

    return text


class DeepSeekAIAnalyzer(AIAnalyzerPort):
    """DeepSeek AI analysis implementation.

    Calls DeepSeek API directly (no openai SDK), supports tool calling
    and streaming output. Produces AnalysisReport compatible with HTML templates.
    """

    def __init__(
        self,
        mock: bool = False,
        api_key: str = "",
        api_url: str = "",
        settings: Any = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provided_settings = settings
        if settings is not None:
            self._settings = settings
        else:
            self._settings = get_settings()
        self._mock = mock or self._settings.mock_mode
        self.api_key = api_key or self._settings.deepseek_api_key
        base = self._settings.deepseek_base_url.rstrip("/")
        self.api_url = api_url or f"{base}/v1/chat/completions"
        self._http = http_client or httpx.AsyncClient(
            timeout=180.0,
            limits=httpx.Limits(max_keepalive_connections=5),
        )

    async def analyze(
        self,
        stock_info: StockAnalysis,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
        *,
        use_pipeline_v2: bool = False,
        use_fast: bool = False,
        use_single_call: bool = False,
        use_ultra_fast: bool = False,
    ) -> AnalysisReport:
        """AI analysis entry point - receives domain entity, returns AnalysisReport.

        When ``use_pipeline_v2`` is True, run the two-phase pipeline orchestrator
        (ANALYSIS + COMPILE); otherwise preserve the existing single-shot behavior
        (``_legacy_analyze``). Old callers (without the kwarg) work identically —
        DEFAULT OFF. ``use_fast`` skips ANALYSIS self-check for a faster run.
        ``use_single_call`` / ``use_ultra_fast`` are experimental low-latency modes.
        """
        if use_pipeline_v2:
            return await self._pipeline_analyze(
                stock_info, reports, financial_md_path, use_fast=use_fast,
                use_single_call=use_single_call, use_ultra_fast=use_ultra_fast,
            )
        return await self._legacy_analyze(stock_info, reports, financial_md_path)

    async def _legacy_analyze(
        self,
        stock_info: StockAnalysis,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
    ) -> AnalysisReport:
        if self._mock:
            from ..collectors.mock import mock_analysis_report

            return mock_analysis_report(stock_info.symbol, stock_info.name)

        # 检查缓存
        from .cache import get_analysis_cache
        cached = get_analysis_cache(stock_info.symbol)
        if cached:
            logging.info("[ai_analysis] cache hit for %s", stock_info.symbol)
            return AnalysisReport(
                symbol=stock_info.symbol,
                name=stock_info.name,
                summary=cached[:200] + "..." if len(cached) > 200 else cached,
                report_text=cached,
                investment_advice="本报告由DeepSeek AI自动生成，仅供参考，不构成投资建议。",
            )

        import time
        t0 = time.monotonic()
        collected_data = self._build_data_dict(stock_info, reports, financial_md_path)

        try:
            md = await self._call_deepseek(stock_info.symbol, stock_info.name, collected_data)
            md = _deduplicate_tail(md)
        except Exception as e:
            logging.warning("[ai_analyze_stock] %s: %s", type(e).__name__, e)
            md = "AI分析暂不可用，以下为基础数据汇总。"

        elapsed = int((time.monotonic() - t0) * 1000)
        logging.info("[ai_analysis] completed in %dms, output %d chars", elapsed, len(md))

        # 写入缓存
        from .cache import set_analysis_cache
        set_analysis_cache(stock_info.symbol, md)

        short = md[:200]
        short = re.sub(r"\*\*(.*?)\*\*", r"\1", short)
        short = re.sub(r"##?\s*", "", short)
        short = re.sub(r"\* ", "• ", short)
        if len(md) > 200:
            short += "..."

        result = AnalysisReport(
            symbol=stock_info.symbol,
            name=stock_info.name,
            summary=short,
            report_text=md,
            investment_advice=("本报告由DeepSeek AI自动生成，仅供参考，不构成投资建议。"),
        )
        result = self._sanitize_support_resistance(
            result, stock_info.quote.price if stock_info.quote else None
        )
        return result

    def _build_report(self, stock_info: StockAnalysis, md: str) -> AnalysisReport:
        """Assemble a finalized ``AnalysisReport`` from raw Markdown output.

        Reuses the same summary-cleanup + support/resistance sanity path as the
        legacy analyzer so v2 output is subject to the same post-processing.
        """
        short = md[:200]
        short = re.sub(r"\*\*(.*?)\*\*", r"\1", short)
        short = re.sub(r"##?\s*", "", short)
        short = re.sub(r"\* ", "• ", short)
        if len(md) > 200:
            short += "..."
        result = AnalysisReport(
            symbol=stock_info.symbol,
            name=stock_info.name,
            summary=short,
            report_text=md,
            investment_advice="本报告由DeepSeek AI自动生成，仅供参考，不构成投资建议。",
        )
        return self._sanitize_support_resistance(
            result, stock_info.quote.price if stock_info.quote else None
        )

    async def _pipeline_analyze(
        self,
        stock_info: StockAnalysis,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
        *,
        use_fast: bool = False,
        use_single_call: bool = False,
        use_ultra_fast: bool = False,
    ) -> AnalysisReport:
        """Two-phase pipeline v2 analysis entry (Plan B in brainstorming).

        Runs ANALYSIS (parallel tools + LLM + self-check) then COMPILE
        (long-form final report) and returns ``final_markdown``. The L1 disk cache
        (``cache.py``) is reused; on orchestrator failure a degraded fallback
        report is produced so the pipeline never aborts.
        """
        from .pipeline.orchestrator import PipelineOrchestrator

        ctx: dict = {}
        try:
            ctx = await PipelineOrchestrator(self).run(
                stock_info, reports=reports, financial_md_path=financial_md_path,
                use_fast=use_fast, use_single_call=use_single_call,
                use_ultra_fast=use_ultra_fast,
            )
        except Exception as e:
            logging.warning("[pipeline_v2] orchestrator failed: %s: %s", type(e).__name__, e)
        text = ctx.get("final_markdown", "") if isinstance(ctx, dict) else ""
        if not text:
            # v2 失败时降级到 legacy 一段式(而非数据汇总 fallback)
            logging.info("[pipeline_v2] 降级到 legacy 一段式分析")
            return await self._legacy_analyze(stock_info, reports, financial_md_path)
        from .cache import set_analysis_cache
        set_analysis_cache(stock_info.symbol, text)
        return self._build_report(stock_info, text)

    async def _call_deepseek(self, stock_code: str, stock_name: str, collected_data: dict) -> str:
        """Call DeepSeek API with streaming + tool calling."""
        user_msg = self._build_user_message(stock_code, stock_name, collected_data)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        for _round_idx in range(_MAX_TOOL_ROUNDS):
            tool_call_result = await self._call_with_tools(messages)
            if tool_call_result is None:
                break
            messages, should_break = tool_call_result
            if should_break:
                break

        if _MAX_TOOL_ROUNDS > 0 and any(
            m.get("role") == "tool" for m in messages
        ):
            messages.append({
                "role": "user",
                "content": (
                    "以上所有搜索已完成，数据已全部提供给你。"
                    "请立即基于以上全部数据，输出完整的深度分析报告。"
                    "不要再调用搜索工具，直接开始分析输出。"
                ),
            })

        result = await self._stream_final_response(messages)
        return _strip_xml_tool_calls(result)

    async def _call_with_tools(
        self, messages: list[dict]
    ) -> tuple[list[dict], bool] | None:
        """Send a non-streaming request; if model requests tool calls,
        execute them and append results to messages.

        Returns:
            (updated_messages, should_break) if tool calls were processed,
            None if model returned a final text response (no tool calls).
        """
        settings = self._provided_settings or self._settings
        resp = await self._http.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.deepseek_model,
                "messages": messages,
                "temperature": settings.deepseek_temperature,
                "max_tokens": settings.deepseek_max_tokens,
                "tools": get_tool_definitions(),
                "tool_choice": "auto",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            content = message.get("content", "")
            xml_calls = _parse_xml_tool_calls(content)
            if xml_calls:
                tool_calls = [
                    {"id": f"xml_{i}", "function": tc}
                    for i, tc in enumerate(xml_calls)
                ]
                message["tool_calls"] = tool_calls
                message["content"] = _strip_xml_tool_calls(content)

        if not tool_calls:
            return None

        messages.append(message)

        for tc in tool_calls:
            fn = tc["function"]
            fn_name = fn["name"]
            try:
                fn_args = json.loads(fn["arguments"])
            except json.JSONDecodeError:
                fn_args = {}

            print(f"\n 🔍 联网搜索: {fn_args.get('query', fn_name)}")
            result = await execute_web_search(fn_args.get("query", ""))
            print(f"    → 获取到 {len(result)} 字符结果")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

        return messages, False

    async def _stream_final_response(self, messages: list[dict]) -> str:
        """Send a streaming request and return the full accumulated text."""
        settings = self._provided_settings or self._settings
        async with self._http.stream(
            "POST",
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.deepseek_model,
                "messages": messages,
                "temperature": settings.deepseek_temperature,
                "max_tokens": settings.deepseek_max_tokens,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            return await self._collect_stream(resp)

    @staticmethod
    async def _collect_stream(resp: httpx.Response) -> str:
        """Read SSE stream, print sections as they arrive, return full text.

        Uses splitlines() for O(n) buffer processing.
        """
        full_text: list[str] = []
        buffer = ""
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
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if not content:
                continue

            full_text.append(content)
            buffer += content

            # O(n) splitlines replaces O(n^2) while "\n" in buffer loop
            *lines, buffer = buffer.splitlines()
            for line_text in lines:
                line_text = line_text + "\n"
                header_match = re.match(r"^##\s+(.+)", line_text)
                if header_match:
                    if current_section:
                        print()
                    section_name = header_match.group(1).strip()
                    current_section = section_name
                    print(f"\n{'─' * 40}")
                    print(f"  {section_name}")
                    print(f"{'─' * 40}")
                elif current_section and line_text.strip():
                    stripped = line_text.rstrip()
                    if len(stripped) > 120:
                        stripped = stripped[:117] + "..."
                    print(f"  {stripped}")

        if buffer.strip():
            full_text.append(buffer)

        return "".join(full_text)

    def _build_user_message(self, stock_code: str, stock_name: str, data: dict) -> str:
        quote = data.get("quote", {})
        quote_data = (
            f"价格{quote.get('price', 'N/A')}元 "
            f"涨跌{quote.get('change_pct', 'N/A')}% "
            f"PE={quote.get('pe', 'N/A')}"
        )
        current_time = data.get("current_time", "")
        base = USER_PROMPT_TEMPLATE.format(
            stock_code=stock_code,
            stock_name=stock_name or stock_code,
            quote_data=quote_data,
            current_time=current_time,
        )

        sections = [base]

        financial = data.get("financial", {})
        # financial dict keys are short (e.g. "period", "rev")
        # built by _build_data_dict, used directly for prompt display
        if financial and financial.get("period"):
            sections.append(f"\n\n【已采集财务数据（{financial.get('period', '')}）】")
            for k, v in financial.items():
                if v and v != 0:
                    sections.append(f"- {k}: {v}")

        # Quarterly/semi-annual financial data
        quarterly = data.get("quarterly_financial", {})
        if quarterly and quarterly.get("period"):
            sections.append(
                f"\n\n【最近一期季报/中报（{quarterly.get('period', '')}，"
                f"{quarterly.get('type', '')}）】"
            )
            for k, v in quarterly.items():
                if v and v != 0 and k not in ("period",):
                    sections.append(f"- {k}: {v}")

        md_path = data.get("financial_md_path")
        md_loaded = False
        if md_path:
            md_file = Path(md_path)
            if md_file.exists():
                md_content = md_file.read_text(encoding="utf-8")
                sections.append(f"\n\n【财务数据提取（来自 {md_file.name}）】")
                sections.append(md_content)
                md_loaded = True

        if not md_loaded:
            for rkey, rlabel in [
                ("annual_report", "年报"),
                ("semi_annual_report", "半年报"),
                ("quarterly_report", "季报"),
            ]:
                report = data.get(rkey)
                if report and report.content:
                    sections.append(f"\n\n【{rlabel}原文摘要（{report.year}年）】")
                    sections.append(report.content)

        sections.extend(self._format_capital_flow(data))
        sections.extend(self._format_social_kline(data))

        return "".join(sections)

    @staticmethod
    def _format_capital_flow(data: dict) -> list[str]:
        """Format capital flow data section."""
        cf = data.get("capital_flow", {})
        if not cf or cf.get("main_net_5d") is None or cf.get("main_net_5d") == 0:
            return []
        parts = [
            "\n\n【已采集资金面数据】",
            f"- 近5日主力净流入: {cf.get('main_net_5d', 0) / 1e8:.2f}亿元",
            f"- 3日净流入: {cf.get('main_net_3d', 0) / 1e8:.2f}亿元",
            f"- 10日净流入: {cf.get('main_net_10d', 0) / 1e8:.2f}亿元",
            f"- 20日净流入: {cf.get('main_net_20d', 0) / 1e8:.2f}亿元",
        ]
        if cf.get("northbound_chg"):
            parts.append(f"- 北向资金变化: {cf['northbound_chg'] / 1e8:+.2f}亿元")
        if cf.get("lhb_date"):
            parts.append(
                f"- 龙虎榜({cf['lhb_date']}): 净买入{cf.get('lhb_net_buy', 0) / 1e8:.2f}亿元"
            )
            if cf.get("lhb_reason"):
                parts.append(f"- 龙虎榜原因: {cf['lhb_reason']}")
        return parts

    @staticmethod
    def _format_social_kline(data: dict) -> list[str]:
        """Format social media and K-line data sections."""
        parts: list[str] = []
        for key, label in [
            ("xueqiu", "雪球"),
            ("eastmoney", "东方财富股吧"),
            ("wechat", "微信公众号"),
        ]:
            text = data.get(key, "")
            if text and text != "暂无数据":
                parts.append(f"\n\n【已采集{label}舆情摘要】")
                parts.append(text[:1500])

        kline_summary = data.get("kline_summary", {})
        if kline_summary:
            parts.extend(
                [
                    "\n\n【已采集K线数据】",
                    f"- 最新收盘: {kline_summary['latest_close']} ({kline_summary['latest_date']})",
                    f"- 区间最高: {kline_summary['period_high']}",
                    f"- 区间最低: {kline_summary['period_low']}",
                    f"- K线条数: {kline_summary['bar_count']} [{kline_summary['source']}]",
                ]
            )
        return parts

    def _build_data_dict(
        self,
        info: StockAnalysis,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
    ) -> dict:
        from datetime import datetime

        quote = info.quote
        financial = info.financial
        quarterly = info.quarterly_financial
        capital_flow = info.capital_flow

        capital_flow_dict = {
            "main_net_5d": capital_flow.main_net_5d,
            "main_net_3d": capital_flow.main_net_3d,
            "main_net_10d": capital_flow.main_net_10d,
            "main_net_20d": capital_flow.main_net_20d,
            "northbound_chg": capital_flow.northbound_chg,
            "lhb_date": capital_flow.lhb_date,
            "lhb_reason": capital_flow.lhb_reason,
            "lhb_net_buy": capital_flow.lhb_net_buy,
        }

        return {
            "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "quote": {
                "price": quote.price or None,
                "change_pct": quote.change_pct,
                "open": quote.open,
                "high": quote.high,
                "low": quote.low,
                "prev_close": quote.prev_close,
                "pe": quote.pe,
                "source": quote.source,
            },
            "financial": {
                **(
                    {"rev": round(financial.revenue / 1e8, 2)}
                    if financial.revenue
                    else {}
                ),
                **({"rev_yoy": financial.revenue_yoy} if financial.revenue_yoy else {}),
                **(
                    {"np": round(financial.net_profit / 1e8, 2)}
                    if financial.net_profit
                    else {}
                ),
                **({"np_yoy": financial.net_profit_yoy} if financial.net_profit_yoy else {}),
                **({"roe": financial.roe} if financial.roe else {}),
                **({"eps": financial.eps} if financial.eps else {}),
                **(
                    {"ta": round(financial.total_assets / 1e8, 2)}
                    if financial.total_assets
                    else {}
                ),
                **(
                    {"tl": round(financial.total_liabilities / 1e8, 2)}
                    if financial.total_liabilities
                    else {}
                ),
                **(
                    {"ocf": round(financial.operating_cf / 1e8, 2)}
                    if financial.operating_cf
                    else {}
                ),
                "period": financial.report_period,
                "src": financial.source,
            },
            "quarterly_financial": {
                "period": quarterly.report_period,
                **({"type": quarterly.report_type} if quarterly.report_type else {}),
                **(
                    {"rev": round(quarterly.revenue / 1e8, 2)}
                    if quarterly.revenue
                    else {}
                ),
                **({"rev_yoy": quarterly.revenue_yoy} if quarterly.revenue_yoy else {}),
                **(
                    {"np": round(quarterly.net_profit / 1e8, 2)}
                    if quarterly.net_profit
                    else {}
                ),
                **({"np_yoy": quarterly.net_profit_yoy} if quarterly.net_profit_yoy else {}),
            },
            "capital_flow": capital_flow_dict,
            "annual_report": info.annual_report,
            "semi_annual_report": info.semi_annual_report,
            "quarterly_report": info.quarterly_report,
            "financial_md_path": str(financial_md_path) if financial_md_path else None,
            "industry": _detect_industry(info.symbol, info.name),
            "kline_summary": getattr(info, "kline_summary", None),
        }

    def _sanitize_support_resistance(
        self, report: AnalysisReport, current_price: float | None
    ) -> AnalysisReport:
        """Support/resistance sanity check.

        If AI gives support >= current price, override to price * 0.92.
        If AI gives resistance <= current price, override to price * 1.08.
        """
        if not current_price or current_price <= 0:
            return report

        md = report.report_text
        if not md:
            return report

        def _extract_first_price(pattern: str, text: str) -> tuple[float | None, re.Match | None]:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                return None, None
            price_str = match.group(1).replace(",", "")
            try:
                return float(price_str), match
            except ValueError:
                return None, None

        support_pattern = r"支撑位[：:\s]\s*([0-9]+(?:\.[0-9]+)?)"
        resistance_pattern = r"阻力位[：:\s]\s*([0-9]+(?:\.[0-9]+)?)"

        support_val, support_match = _extract_first_price(support_pattern, md)
        resistance_val, resistance_match = _extract_first_price(resistance_pattern, md)

        if support_val is None and resistance_val is None:
            return report

        new_md = md

        if support_val is not None and support_val >= current_price:
            safe_support = round(current_price * 0.92, 2)
            if support_match:
                orig = support_match.group(0)
                replacement = orig.replace(support_match.group(1), str(safe_support))
                new_md = (
                    new_md[: support_match.start()] + replacement + new_md[support_match.end() :]
                )
                logging.info(
                    "[sanity_support] 支撑位 %.2f >= 现价 %.2f，已修正为 %.2f",
                    support_val,
                    current_price,
                    safe_support,
                )

        if resistance_val is not None and resistance_val <= current_price:
            safe_resistance = round(current_price * 1.08, 2)
            if resistance_match:
                orig = resistance_match.group(0)
                replacement = orig.replace(resistance_match.group(1), str(safe_resistance))
                resistance_match_new = re.search(resistance_pattern, new_md, re.IGNORECASE)
                if resistance_match_new:
                    new_md = (
                        new_md[: resistance_match_new.start()]
                        + replacement
                        + new_md[resistance_match_new.end() :]
                    )
                logging.info(
                    "[sanity_resistance] 阻力位 %.2f <= 现价 %.2f，已修正为 %.2f",
                    resistance_val,
                    current_price,
                    safe_resistance,
                )

        if new_md != md:
            report = report.model_copy(update={"report_text": new_md})

        return report
