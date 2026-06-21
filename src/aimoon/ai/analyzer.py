"""DeepSeek AI analysis engine.

Two modes:
1. DeepSeekAnalyzer — 基于用户提供的代码设计，使用 httpx AsyncClient 直接调用 DeepSeek API
2. AIAnalyzer — 统一接口，兼容原有 HTML 模板的 AnalysisReport 输出
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import httpx

from ..config.settings import get_settings
from ..models.report import AnalysisReport, DimensionScore
from ..models.stock import StockInfo


class DeepSeekAnalyzer:
    """DeepSeek AI分析器 — 直接调用 DeepSeek API（不依赖 openai SDK）。

    用法:
        analyzer = DeepSeekAnalyzer(api_key="sk-xxx")
        report_md = await analyzer.analyze_stock("600519", collected_data)
    """

    def __init__(self, api_key: str = "", api_url: str = ""):
        settings = get_settings()
        self.api_key = api_key or settings.deepseek_api_key
        base = settings.deepseek_base_url.rstrip("/")
        self.api_url = api_url or f"{base}/v1/chat/completions"

    async def analyze_stock(self, stock_code: str, collected_data: Dict) -> str:
        """调用DeepSeek进行综合分析，返回Markdown格式报告。"""
        prompt = self._build_prompt(stock_code, collected_data)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            result = resp.json()
            return result["choices"][0]["message"]["content"]

    def _build_prompt(self, stock_code: str, data: Dict) -> str:
        """构建分析Prompt，结构清晰，来源透明。"""
        quote = data.get("quote", {})
        financial = data.get("financial", {})

        return f"""
你是一位专业的A股投资分析师。请基于以下信息，对股票 {stock_code} 进行全面分析：

## 实时行情
- 最新价: {quote.get("price", "N/A")} | 涨跌: {quote.get("change_pct", "N/A")}%
- 开盘: {quote.get("open", "N/A")} | 最高: {quote.get("high", "N/A")} | 最低: {quote.get("low", "N/A")}
- 昨收: {quote.get("prev_close", "N/A")} | PE: {quote.get("pe", "N/A")}
- 来源: {quote.get("source", "N/A")}

## 财务数据
{json.dumps(financial, ensure_ascii=False, indent=2)}

## 雪球网舆情
{data.get("xueqiu", "无数据")[:2000]}

## 东方财富股吧讨论
{data.get("eastmoney", "无数据")[:2000]}

## 巨潮资讯公告
{data.get("announcement", "无数据")[:2000]}

## 社交媒体舆情（微信公众号、今日头条、小红书等）
{data.get("social_media", "无数据")[:2000]}

请输出一份结构清晰的分析报告，包含：
1. 公司概况与业务分析
2. 财务健康度评估（营收、利润、现金流等）
3. 市场情绪分析（各平台舆情汇总）
4. 技术面简析（如有行情数据）
5. 综合投资建议（买入/持有/观望/卖出）
6. 风险提示

报告要简洁明了，适合普通投资者阅读。
注意：所有数据来源已在各章节标注，请确保引用时注明数据来源。
""".strip()


class AIAnalyzer:
    """AIAnalyzer 统一接口 — 兼容原有HTML模板的AnalysisReport输出。"""

    def __init__(self, mock: bool = False) -> None:
        settings = get_settings()
        self._mock = mock or settings.mock_mode
        self._settings = settings

    async def analyze(self, stock_info: StockInfo) -> AnalysisReport:
        """Run full analysis on stock info."""
        if self._mock:
            from ..collectors.mock import mock_analysis_report
            return mock_analysis_report(stock_info.symbol, stock_info.name)

        collected_data = self._build_data_dict(stock_info)

        try:
            analyzer = DeepSeekAnalyzer(
                api_key=self._settings.deepseek_api_key,
            )
            md = await analyzer.analyze_stock(stock_info.symbol, collected_data)
        except Exception:
            md = "AI分析暂不可用，以下为基础数据汇总。"

        return self._build_report_from_markdown(stock_info, md)

    def _build_data_dict(self, info: StockInfo) -> dict:
        """Convert StockInfo to the flat dict expected by DeepSeekAnalyzer."""
        q = info.quote
        f = info.financial

        xueqiu_text = ""
        eastmoney_text = ""
        announcement_text = ""
        social_parts: list[str] = []

        for p in info.social_posts:
            line = f"- {p.title} (赞{p.likes} 评{p.comments} | {p.platform})"
            if p.platform == "雪球":
                xueqiu_text += line + "\n"
            elif p.platform == "东方财富股吧":
                eastmoney_text += line + "\n"
            elif p.platform == "巨潮资讯":
                announcement_text += line + "\n"
            else:
                social_parts.append(line)

        return {
            "quote": {
                "price": q.price, "change_pct": q.change_pct,
                "open": q.open, "high": q.high, "low": q.low,
                "prev_close": q.prev_close, "pe": q.pe, "source": q.source,
            },
            "financial": {
                "营收": f.revenue, "营收同比%": f.revenue_yoy,
                "净利润": f.net_profit, "净利润同比%": f.net_profit_yoy,
                "ROE%": f.roe, "EPS": f.eps,
                "总资产": f.total_assets, "总负债": f.total_liabilities,
                "经营现金流": f.operating_cf, "报告期": f.report_period,
            },
            "xueqiu": xueqiu_text or "暂无雪球数据",
            "eastmoney": eastmoney_text or "暂无股吧数据",
            "announcement": announcement_text or "暂无公告数据",
            "social_media": "\n".join(social_parts) or "暂无社交媒体数据",
        }

    def _build_report_from_markdown(self, info: StockInfo, md: str) -> AnalysisReport:
        # Extract first 200 chars as short summary, strip markdown syntax
        import re as _re
        short_summary = md[:200]
        short_summary = _re.sub(r'\*\*(.*?)\*\*', r'\1', short_summary)  # **bold** → bold
        short_summary = _re.sub(r'###?\s*', '', short_summary)  # ### heading → text
        short_summary = _re.sub(r'\* ', '• ', short_summary)    # * list → • list
        if len(md) > 200:
            short_summary += "..."

        return AnalysisReport(
            symbol=info.symbol,
            name=info.name,
            summary=short_summary,  # 摘要（前200字）
            report_text=md,  # 完整Markdown报告（用于渲染）
            overall_rating=3,
            investment_advice="本报告由DeepSeek AI自动生成，仅供参考，不构成投资建议。",
            sentiment=DimensionScore(name="市场情绪", score=3, weight=0.25),
            technical=DimensionScore(name="技术面", score=3, weight=0.15),
            fundamental=DimensionScore(name="基本面", score=3, weight=0.20),
            capital_flow=DimensionScore(name="资金面", score=3, weight=0.15),
            news=DimensionScore(name="新闻舆情", score=3, weight=0.15),
            sentiment_detail="AI已基于多平台数据进行情绪分析，详见报告正文。",
            technical_detail="详见报告正文（技术面分析）。",
            fundamental_detail="详见报告正文（财务分析）。",
            capital_flow_detail="详见报告正文（资金面分析）。",
            news_detail="详见报告正文（新闻舆情分析）。",
        )
