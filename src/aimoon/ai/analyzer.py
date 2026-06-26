"""DeepSeek AI analysis engine.

Two modes:
1. DeepSeekAnalyzer — calls DeepSeek API with structured prompt
2. AIAnalyzer — orchestrates data → scoring → AnalysisReport
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from ..config.settings import get_settings
from ..models.report import AnalysisReport, DimensionScore
from ..models.stock import StockInfo
from .prompts import PROMPT_TEMPLATE


class DeepSeekAnalyzer:
    """DeepSeek AI分析器 — 直接调用 DeepSeek API（不依赖 openai SDK）。
    使用 PROMPT_TEMPLATE 构建结构化提示词。
    """

    def __init__(self, api_key: str = "", api_url: str = ""):
        self._settings = get_settings()
        self.api_key = api_key or self._settings.deepseek_api_key
        base = self._settings.deepseek_base_url.rstrip("/")
        self.api_url = api_url or f"{base}/v1/chat/completions"

    async def analyze_stock(
        self, stock_code: str, stock_name: str, collected_data: dict
    ) -> str:
        """调用DeepSeek进行综合分析，返回Markdown格式报告。"""
        from datetime import datetime as _dt

        prompt = self._build_prompt(stock_code, stock_name, collected_data)
        now_str = _dt.now().strftime("%Y-%m-%d")
        sys_msg = f"现在是 {now_str}，请基于这个时间回答用户问题。"

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._settings.deepseek_model,
                    "messages": [
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": self._settings.deepseek_temperature,
                    "max_tokens": self._settings.deepseek_max_tokens,
                },
            )
            resp.raise_for_status()
            result = resp.json()
            return result["choices"][0]["message"]["content"]

    def _build_prompt(self, stock_code: str, stock_name: str, data: dict) -> str:
        quote = data.get("quote", {})
        quote_data = (
            f"价格{quote.get('price', 'N/A')}元 "
            f"涨跌{quote.get('change_pct', 'N/A')}% "
            f"PE={quote.get('pe', 'N/A')}"
        )
        base = PROMPT_TEMPLATE.format(
            stock_code=stock_code,
            stock_name=stock_name or stock_code,
            quote_data=quote_data,
        )

        sections = [base]

        # Current time
        current_time = data.get("current_time", "")
        if current_time:
            sections.append(f"\n\n【当前时间】{current_time}")
            sections.append("请基于当前时间进行分析，注意数据的时效性。")

        financial = data.get("financial", {})
        if financial and financial.get("报告期"):
            sections.append(f"\n\n【已采集财务数据（{financial.get('报告期', '')}）】")
            for k, v in financial.items():
                if v and v != 0:
                    sections.append(f"- {k}: {v}")

        # Read saved financial report MD file if present
        md_path = data.get("financial_md_path")
        if md_path:
            md_file = Path(md_path)
            if md_file.exists():
                md_content = md_file.read_text(encoding="utf-8")
                sections.append(f"\n\n【财务数据提取（来自 {md_file.name}）】")
                sections.append(md_content)
            else:
                # Fallback: include report content from dict
                for rkey, rlabel in [
                    ("annual_report", "年报"),
                    ("semi_annual_report", "半年报"),
                    ("quarterly_report", "季报"),
                ]:
                    report = data.get(rkey)
                    if report and report.get("content"):
                        year = report.get("year", "")
                        sections.append(f"\n\n【{rlabel}原文摘要（{year}年）】")
                        sections.append(report["content"])
        else:
            # No MD path provided — fall back to inline report content
            for rkey, rlabel in [
                ("annual_report", "年报"),
                ("semi_annual_report", "半年报"),
                ("quarterly_report", "季报"),
            ]:
                report = data.get(rkey)
                if report and report.get("content"):
                    year = report.get("year", "")
                    sections.append(f"\n\n【{rlabel}原文摘要（{year}年）】")
                    sections.append(report["content"])

        cf = data.get("capital_flow", {})
        if cf and cf.get("main_net_5d") is not None and cf.get("main_net_5d") != 0:
            sections.append("\n\n【已采集资金面数据】")
            sections.append(
                f"- 近5日主力净流入: {cf.get('main_net_5d', 0) / 1e8:.2f}亿元"
            )
            sections.append(
                f"- 3日净流入: {cf.get('net_3d', 0) / 1e8:.2f}亿元"
            )
            sections.append(
                f"- 10日净流入: {cf.get('net_10d', 0) / 1e8:.2f}亿元"
            )
            sections.append(
                f"- 20日净流入: {cf.get('net_20d', 0) / 1e8:.2f}亿元"
            )
            if cf.get("northbound_chg"):
                val = cf["northbound_chg"] / 1e8
                sections.append(f"- 北向资金变化: {val:+.2f}亿元")
            if cf.get("lhb_date"):
                net = cf.get("lhb_net_buy", 0) / 1e8
                sections.append(
                    f"- 龙虎榜({cf['lhb_date']}): 净买入{net:.2f}亿元"
                )
                if cf.get("lhb_reason"):
                    sections.append(f"- 龙虎榜原因: {cf['lhb_reason']}")

        social_labels = [
            ("xueqiu", "雪球"),
            ("eastmoney", "东方财富股吧"),
            ("toutiao", "今日头条"),
            ("wechat", "微信公众号"),
        ]
        for key, label in social_labels:
            text = data.get(key, "")
            if text and text != "暂无数据":
                sections.append(f"\n\n【已采集{label}舆情摘要】")
                sections.append(text[:1500])

        kline_summary = data.get("kline_summary", {})
        if kline_summary:
            sections.append("\n\n【已采集K线数据】")
            sections.append(
                f"- 最新收盘: {kline_summary['latest_close']} "
                f"({kline_summary['latest_date']})"
            )
            sections.append(f"- 区间最高: {kline_summary['period_high']}")
            sections.append(f"- 区间最低: {kline_summary['period_low']}")
            sections.append(
                f"- K线条数: {kline_summary['bar_count']} "
                f"[{kline_summary['source']}]"
            )

        return "".join(sections)


class AIAnalyzer:
    """AIAnalyzer 统一接口 — 兼容HTML模板的 AnalysisReport 输出。"""

    def __init__(self, mock: bool = False) -> None:
        settings = get_settings()
        self._mock = mock or settings.mock_mode
        self._settings = settings

    async def analyze(
        self, stock_info: StockInfo, reports: dict | None = None,
        financial_md_path: Path | None = None,
    ) -> AnalysisReport:
        if self._mock:
            from ..collectors.mock import mock_analysis_report

            return mock_analysis_report(stock_info.symbol, stock_info.name)

        collected_data = self._build_data_dict(stock_info, reports, financial_md_path)

        try:
            analyzer = DeepSeekAnalyzer(api_key=self._settings.deepseek_api_key)
            md = await analyzer.analyze_stock(
                stock_info.symbol, stock_info.name, collected_data
            )
        except Exception as e:
            logging.warning("[ai_analyze_stock] %s: %s", type(e).__name__, e)
            md = "AI分析暂不可用，以下为基础数据汇总。"

        return self._compute_dimension_scores(stock_info, md)

    def _build_data_dict(
        self, info: StockInfo, reports: dict | None = None,
        financial_md_path: Path | None = None,
    ) -> dict:
        from datetime import datetime

        quote = info.quote
        financial = info.financial
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

        # Capital flow as flat dict for prompt formatting
        capital_flow_dict = {
            "main_net_5d": capital_flow.main_net_5d,
            "net_3d": capital_flow.net_3d,
            "net_10d": capital_flow.net_10d,
            "net_20d": capital_flow.net_20d,
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
                "营收(亿)": (
                    round(financial.revenue / 1e8, 2) if financial.revenue else 0
                ),
                "营收同比%": financial.revenue_yoy,
                "净利润(亿)": (
                    round(financial.net_profit / 1e8, 2)
                    if financial.net_profit
                    else 0
                ),
                "净利润同比%": financial.net_profit_yoy,
                "ROE%": financial.roe,
                "EPS": financial.eps,
                "总资产(亿)": (
                    round(financial.total_assets / 1e8, 2)
                    if financial.total_assets
                    else 0
                ),
                "总负债(亿)": (
                    round(financial.total_liabilities / 1e8, 2)
                    if financial.total_liabilities
                    else 0
                ),
                "经营现金流(亿)": (
                    round(financial.operating_cf / 1e8, 2)
                    if financial.operating_cf
                    else 0
                ),
                "报告期": financial.report_period,
            },
            "capital_flow": capital_flow_dict,
            "annual_report": reports.get("annual") if reports else None,
            "semi_annual_report": reports.get("semi_annual") if reports else None,
            "quarterly_report": reports.get("quarterly") if reports else None,
            "financial_md_path": str(financial_md_path) if financial_md_path else None,
            "xueqiu": _join("xueqiu"),
            "eastmoney": _join("eastmoney"),
            "toutiao": _join("toutiao"),
            "wechat": _join("wechat"),
            "kline_summary": self._kline_summary(info.kline),
        }

    @staticmethod
    def _kline_summary(kline: Any) -> dict:
        """Extract summary stats from K-line data."""
        if not kline or not kline.bars:
            return {}
        bars = kline.bars
        latest = bars[-1]
        highs = [b.high for b in bars if b.high > 0]
        lows = [b.low for b in bars if b.low > 0]
        return {
            "latest_close": latest.close,
            "latest_date": latest.date,
            "period_high": max(highs) if highs else 0,
            "period_low": min(lows) if lows else 0,
            "bar_count": len(bars),
            "source": kline.source,
        }

    def _compute_dimension_scores(
        self, info: StockInfo, md: str
    ) -> AnalysisReport:
        import re as _re

        from ..scoring import fundamental_score as _fund_score
        from ..scoring import news_score as _news_score
        from ..scoring.constants import (
            WEIGHT_CAPITAL_FLOW,
            WEIGHT_FUNDAMENTAL,
            WEIGHT_NEWS,
        )

        short = md[:200]
        short = _re.sub(r"\*\*(.*?)\*\*", r"\1", short)
        short = _re.sub(r"##?\s*", "", short)
        short = _re.sub(r"\* ", "• ", short)
        if len(md) > 200:
            short += "..."

        # Compute capital flow score
        cap_score = 3
        cap_detail = "详见报告正文（资金面分析）。"
        main_force = "持平"
        if info.capital_flow and info.capital_flow.source != "all_failed":
            try:
                from ..scoring import capital_flow_score as _cfs

                cap_score, cap_detail, main_force = _cfs(info.capital_flow)
            except Exception as e:
                logging.warning("[capital_flow_score_calc] %s: %s", type(e).__name__, e)
        # Override capital flow when turnover is extremely low (< 0.1%)
        turnover = info.quote.turnover if info.quote else 0.0
        if 0 <= turnover < 0.1:
            cap_score = 3
            cap_detail = "今日交投清淡，主力资金无明显动向，呈观望态势"
            main_force = "持平"

        # Compute fundamental score from financial data
        fund_score, fund_detail = _fund_score(info.financial)

        # Compute news score from research reports
        news_score, news_detail = _news_score(info.research)

        return AnalysisReport(
            symbol=info.symbol,
            name=info.name,
            summary=short,
            report_text=md,
            investment_advice=(
                "本报告由DeepSeek AI自动生成，仅供参考，不构成投资建议。"
            ),
            fundamental=DimensionScore(
                name="基本面", score=fund_score, weight=WEIGHT_FUNDAMENTAL
            ),
            capital_flow=DimensionScore(
                name="资金面", score=cap_score, weight=WEIGHT_CAPITAL_FLOW
            ),
            news=DimensionScore(
                name="新闻舆情", score=news_score, weight=WEIGHT_NEWS
            ),
            capital_flow_detail=cap_detail,
            fundamental_detail=fund_detail,
            news_detail=news_detail,
            main_force=main_force,
        )
