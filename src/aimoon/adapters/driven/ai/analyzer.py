"""DeepSeek AI analysis engine.

DeepSeekAIAnalyzer — orchestrates data -> scoring -> AnalysisReport.
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
from .data_cleaner import clean_social_texts
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .web_search_tool import execute_web_search, get_tool_definitions

_MAX_TOOL_ROUNDS = 5


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

    async def analyze(
        self,
        stock_info: StockAnalysis,
        reports: dict | None = None,
        financial_md_path: Path | None = None,
    ) -> AnalysisReport:
        """AI analysis entry point - receives domain entity, returns AnalysisReport."""
        if self._mock:
            from ..collectors.mock import mock_analysis_report

            return mock_analysis_report(stock_info.symbol, stock_info.name)

        collected_data = self._build_data_dict(stock_info, reports, financial_md_path)

        try:
            md = await self._call_deepseek(stock_info.symbol, stock_info.name, collected_data)
        except Exception as e:
            logging.warning("[ai_analyze_stock] %s: %s", type(e).__name__, e)
            md = "AI分析暂不可用，以下为基础数据汇总。"

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

    async def _call_deepseek(self, stock_code: str, stock_name: str, collected_data: dict) -> str:
        """Call DeepSeek API with streaming + tool calling."""
        user_msg = self._build_user_message(stock_code, stock_name, collected_data)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        tools = get_tool_definitions()

        for _round_idx in range(_MAX_TOOL_ROUNDS):
            tool_call_result = await self._call_with_tools(messages, tools)
            if tool_call_result is None:
                break
            messages, should_break = tool_call_result
            if should_break:
                break

        return await self._stream_final_response(messages)

    async def _call_with_tools(
        self, messages: list[dict], tools: list[dict]
    ) -> tuple[list[dict], bool] | None:
        """Send a non-streaming request; if model requests tool calls,
        execute them and append results to messages.

        Returns:
            (updated_messages, should_break) if tool calls were processed,
            None if model returned a final text response (no tool calls).
        """
        settings = self._provided_settings or self._settings
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
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
                    "tools": tools,
                    "tool_choice": "auto",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        if not message.get("tool_calls"):
            return None

        messages.append(message)

        for tc in message["tool_calls"]:
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
        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream(
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
            delta = chunk.get("choices", [{}])[0].get("delta", {})
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
        # financial dict keys are Chinese (e.g. "报告期", "营收(亿)")
        # built by _build_data_dict, used directly for prompt display
        if financial and financial.get("报告期"):
            sections.append(f"\n\n【已采集财务数据（{financial.get('报告期', '')}）】")
            for k, v in financial.items():
                if v and v != 0:
                    sections.append(f"- {k}: {v}")

        # Quarterly/semi-annual financial data
        quarterly = data.get("quarterly_financial", {})
        if quarterly and quarterly.get("报告期"):
            sections.append(
                f"\n\n【最近一期季报/中报（{quarterly.get('报告期', '')}，"
                f"{quarterly.get('report_type', '')}）】"
            )
            for k, v in quarterly.items():
                if v and v != 0 and k not in ("报告期",):
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
            ("toutiao", "今日头条"),
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

        texts: dict[str, list[str]] = {
            "xueqiu": [],
            "eastmoney": [],
            "toutiao": [],
            "wechat": [],
        }
        for p in info.social_posts:
            line = f"- {p.title} (赞{p.likes} 评{p.comments})"
            plat = p.platform
            if "雪球" in plat:
                texts["xueqiu"].append(line)
            elif "股吧" in plat:
                texts["eastmoney"].append(line)
            elif "头条" in plat:
                texts["toutiao"].append(line)
            elif "微信" in plat or "公众号" in plat:
                texts["wechat"].append(line)

        def _join(key: str) -> str:
            return "\n".join(texts[key]) if texts[key] else "暂无数据"

        raw_social = {k: _join(k) for k in texts}
        cleaned_social = clean_social_texts(raw_social)

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
                "营收(亿)": (round(financial.revenue / 1e8, 2) if financial.revenue else 0),
                "营收同比%": financial.revenue_yoy,
                "净利润(亿)": (round(financial.net_profit / 1e8, 2) if financial.net_profit else 0),
                "净利润同比%": financial.net_profit_yoy,
                "ROE%": financial.roe,
                "EPS": financial.eps,
                "总资产(亿)": (
                    round(financial.total_assets / 1e8, 2) if financial.total_assets else 0
                ),
                "总负债(亿)": (
                    round(financial.total_liabilities / 1e8, 2)
                    if financial.total_liabilities
                    else 0
                ),
                "经营现金流(亿)": (
                    round(financial.operating_cf / 1e8, 2) if financial.operating_cf else 0
                ),
                "报告期": financial.report_period,
            },
            "quarterly_financial": {
                "报告期": quarterly.report_period,
                "报告类型": quarterly.report_type,
                "营收(亿)": (round(quarterly.revenue / 1e8, 2) if quarterly.revenue else 0),
                "营收同比%": quarterly.revenue_yoy,
                "净利润(亿)": (round(quarterly.net_profit / 1e8, 2) if quarterly.net_profit else 0),
                "净利润同比%": quarterly.net_profit_yoy,
            },
            "capital_flow": capital_flow_dict,
            "annual_report": info.annual_report,
            "semi_annual_report": info.semi_annual_report,
            "quarterly_report": info.quarterly_report,
            "financial_md_path": str(financial_md_path) if financial_md_path else None,
            "xueqiu": cleaned_social["xueqiu"],
            "eastmoney": cleaned_social["eastmoney"],
            "toutiao": cleaned_social["toutiao"],
            "wechat": cleaned_social["wechat"],
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
                new_md = (
                    new_md[: resistance_match.start()]
                    + replacement
                    + new_md[resistance_match.end() :]
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
