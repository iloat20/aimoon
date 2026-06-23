"""DeepSeek AI analysis engine.

Two modes:
1. DeepSeekAnalyzer — uses PROMPT_TEMPLATE to call DeepSeek API directly
2. AIAnalyzer — unified interface for HTML template compatibility
"""

from __future__ import annotations

import httpx

from ..config.settings import get_settings
from ..models.report import AnalysisReport, DimensionScore
from ..models.stock import StockInfo

PROMPT_TEMPLATE = """
【当前分析标的】
股票代码：{stock_code}
股票名称：{stock_name}
当前价格/估值：{quote_data}

请以资深A股证券分析师的身份，请站在逆向投资者/成长股猎手的角度对【{stock_code}】进行一次深入的基本面与技术面分析。

**核心要求：**
- 获取最新的、准确的2026年的数据，最可靠的方法是直接从官方和权威的财经数据平台查询www.cninfo.com.cn
- 优先使用巨潮资讯网获取最权威的公告原文，再结合东方财富网或新浪财经等平台查看整理后的数据，这样可以确保信息的准确和及时。
- 分析需结构清晰，使用小标题分段
- 请确保所有分析都基于最新可得的数据，并注明关键数据的时间节点。
- 关键结论或数据请用粗体标出
- 避免泛泛而谈，所有观点都需有数据或逻辑支撑
- 最后必须给出明确的投资逻辑总结和风险警示
- 如果某个回答泛泛，可以立刻追问
- 涉及未来预测的部分。需要独立核实关键数据
- 在你得出结论后，请扮演反对者，用最有力的论据攻击你的结论，然后再回应。
- 在回答前，请先执行以下思考流程，并把每一步的推理都写出来：1. 拆解问题：列出要分析这只股票需要解决的子问题。2. 逐一推理：对每个子问题，写出假设、分析步骤和中间结论。3. 自我反驳：对你的核心结论，主动提出一个反对论点并回应。4. 最终整合：完成以上步骤后，给出最终答案。
**请按以下六个维度展开分析：**

**一、商业模式与护城河（定性分析）**
1. 用通俗的语言，解释这家公司是如何赚钱的。业务的核心环节是什么？它的收入来源和利润来源分别是什么？国外收入占比多少？未来增长的主要驱动力是什么？
2. 它的核心竞争力是什么？请用巴菲特的要求简要评估其护城河深浅。
3. 请列出看空这只股票最有力的三个理由。

**二、财务健康度（定量分析）**
1. 请获取并解读最新的年报核心财务数据，优先使用巨潮资讯网获取最权威的公告原文，再结合东方财富网或新浪财经等平台查看整理后的数据，重点关注：
2. 营收与净利润的复合增长率，毛利率的变化趋势，变化原因，净利率的变化以及原因。
3. 现金流状况：经营现金流、投资现金流、筹资现金流的变化趋势，是否存在大额非经常性损益。
4. 资产负债表健康度：资产负债率、短期偿债能力、自由现金流是否充裕。
5. 如果存在报表异常项（如增收不增利、应收账款激增等），请重点提示。

**三、估值水位与市场预期**
1. 当前市盈率、市净率所处历史分位，并与同行业可比公司进行横向对比。
2. 做出折现率15%，永续1%的DCF估值测算，并给出合理的目标价区间。

**四、综合研判**
1. 用一句话总结核心投资逻辑。
2. 给出三种情景假设（乐观/中性/悲观），并分别给出对应的目标价区间及触发条件。

【写作风格要求】
语言简洁明了，通俗易懂，逻辑严密。避免使用过度复杂的金融模型术语，让普通投资者也能清晰理解。
"""  # noqa: E501


class DeepSeekAnalyzer:
    """DeepSeek AI分析器 — 直接调用 DeepSeek API（不依赖 openai SDK）。
    使用 PROMPT_TEMPLATE 构建结构化提示词。
    """

    def __init__(self, api_key: str = "", api_url: str = ""):
        settings = get_settings()
        self.api_key = api_key or settings.deepseek_api_key
        base = settings.deepseek_base_url.rstrip("/")
        self.api_url = api_url or f"{base}/v1/chat/completions"

    async def analyze_stock(
        self, stock_code: str, stock_name: str, collected_data: dict
    ) -> str:
        """调用DeepSeek进行综合分析，返回Markdown格式报告。"""
        prompt = self._build_prompt(stock_code, stock_name, collected_data)

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [{"role": "user", "content": prompt}, {"role": "system", "content": "现在是 2026-06-23 ，请基于这个时间回答用户问题。"}],
                    "temperature": 0.3,
                    "max_tokens": 16384,
                    "thinking": {"type": "enabled"},
                    "reasoning_effort": "high",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            return result["choices"][0]["message"]["content"]

    @staticmethod
    def _get_tools() -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "搜索互联网获取最新信息，如财报、新闻、公告、研报等。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词，如'格力电器2025年财报'、'神火股份最新公告'",
                            }
                        },
                        "required": ["query"],
                    },
                },
            }
        ]

    @staticmethod
    async def _web_search(query: str) -> str:
        """Search web via Baidu and return top results."""
        import re as _re

        results = []

        # Strategy 1: Baidu search
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(
                    "https://www.baidu.com/s",
                    params={"wd": query, "rn": 5},
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept-Language": "zh-CN,zh;q=0.9",
                    },
                )
                if resp.status_code == 200:
                    html = resp.text
                    # Extract titles and snippets from Baidu results
                    titles = _re.findall(r'<h3[^>]*>.*?<a[^>]*>(.*?)</a>', html, _re.DOTALL)
                    snippets = _re.findall(r'<span class="content-right_[^"]*">(.*?)</span>', html, _re.DOTALL)
                    for i, t in enumerate(titles[:5]):
                        title = _re.sub(r'<[^>]+>', '', t).strip()
                        snippet = _re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
                        if title:
                            results.append(f"[{i+1}] {title}\n{snippet}" if snippet else f"[{i+1}] {title}")
        except Exception:
            pass

        # Strategy 2: Fallback to Sogou
        if not results:
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(
                        "https://www.sogou.com/web",
                        params={"query": query},
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        },
                    )
                    if resp.status_code == 200:
                        titles = _re.findall(r'<h3[^>]*>.*?<a[^>]*>(.*?)</a>', resp.text, _re.DOTALL)
                        for i, t in enumerate(titles[:5]):
                            title = _re.sub(r'<[^>]+>', '', t).strip()
                            if title:
                                results.append(f"[{i+1}] {title}")
            except Exception:
                pass

        if results:
            return f"搜索「{query}」结果：\n" + "\n\n".join(results)
        return f"搜索「{query}」未获取到结果"

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

        # Financial reports info (cached)
        reports_section = []
        for key, label in [("annual_report", "年报"), ("semi_annual_report", "半年报"), ("quarterly_report", "季报")]:
            report = data.get(key)
            if report and report.get("year"):
                reports_section.append(f"- {label}: {report['year']}年 {report['title']}")
                if report.get("pdf_url"):
                    reports_section.append(f"  PDF: {report['pdf_url']}")

        if reports_section:
            sections.append("\n\n【已缓存财务报告】")
            sections.extend(reports_section)

        financial = data.get("financial", {})
        if financial and financial.get("报告期"):
            sections.append(f"\n\n【已采集财务数据（{financial.get('报告期', '')}）】")
            for k, v in financial.items():
                if v and v != 0:
                    sections.append(f"- {k}: {v}")

        tech_raw = data.get("technical", {})
        if tech_raw and tech_raw.get("bars", 0) >= 10:
            tech_lines = []
            for k in ("price", "ma5", "ma10", "ma20", "ma60", "macd_dif", "macd_dea",
                       "macd_hist", "rsi6", "rsi14", "boll_upper", "boll_mid", "boll_lower",
                       "support", "resistance", "volume_ratio", "trend", "boll_position",
                       "ret_5d", "ret_20d", "ret_60d"):
                if k in tech_raw:
                    tech_lines.append(f"- {k}: {tech_raw[k]}")
            if tech_lines:
                sections.append("\n\n【已采集技术面指标】")
                sections.extend(tech_lines)

        cf = data.get("capital_flow", {})
        if cf and cf.get("main_net_5d") != 0:
            sections.append("\n\n【已采集资金面数据】")
            sections.append(f"- 近5日主力净流入: {cf['main_net_5d'] / 1e8:.2f}亿元")
            sections.append(f"- 3日净流入: {cf.get('net_3d', 0) / 1e8:.2f}亿元")
            sections.append(f"- 10日净流入: {cf.get('net_10d', 0) / 1e8:.2f}亿元")
            sections.append(f"- 20日净流入: {cf.get('net_20d', 0) / 1e8:.2f}亿元")
            if cf.get("northbound_chg"):
                sections.append(f"- 北向资金变化: {cf['northbound_chg'] / 1e8:+.2f}亿元")
            if cf.get("lhb_date"):
                sections.append(f"- 龙虎榜({cf['lhb_date']}): 净买入{cf['lhb_net_buy'] / 1e8:.2f}亿元")

        for key, label in [("eastmoney", "东方财富股吧"), ("toutiao", "今日头条"), ("wechat", "微信公众号")]:
            text = data.get(key, "")
            if text and text != "暂无数据":
                sections.append(f"\n\n【已采集{label}舆情摘要】")
                sections.append(text[:1500])

        return "".join(sections)


class AIAnalyzer:
    """AIAnalyzer 统一接口 — 兼容HTML模板的 AnalysisReport 输出。"""

    def __init__(self, mock: bool = False) -> None:
        settings = get_settings()
        self._mock = mock or settings.mock_mode
        self._settings = settings

    async def analyze(self, stock_info: StockInfo) -> AnalysisReport:
        if self._mock:
            from ..collectors.mock import mock_analysis_report

            return mock_analysis_report(stock_info.symbol, stock_info.name)

        collected_data = self._build_data_dict(stock_info)

        try:
            analyzer = DeepSeekAnalyzer(api_key=self._settings.deepseek_api_key)
            md = await analyzer.analyze_stock(
                stock_info.symbol, stock_info.name, collected_data
            )
        except Exception:
            md = "AI分析暂不可用，以下为基础数据汇总。"

        return self._build_report_from_markdown(stock_info, md)

    def _build_data_dict(self, info: StockInfo) -> dict:
        from datetime import datetime

        q = info.quote
        f = info.financial
        cf = info.capital_flow

        def _report_dict(r):
            if r and r.year:
                return {"year": r.year, "title": r.title, "pdf_url": r.pdf_url}
            return None

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

        # Compute technical indicators from K-line data
        technical = {}
        if info.kline and info.kline.bars:
            try:
                from ..indicators.technical import compute_indicators

                technical = compute_indicators(info.kline)
            except Exception:
                technical = {"bars": len(info.kline.bars)}

        # Capital flow as flat dict for prompt formatting
        capital_flow = {
            "main_net_5d": cf.main_net_5d,
            "net_3d": cf.net_3d,
            "net_10d": cf.net_10d,
            "net_20d": cf.net_20d,
            "northbound_chg": cf.northbound_chg,
            "lhb_date": cf.lhb_date,
            "lhb_reason": cf.lhb_reason,
            "lhb_net_buy": cf.lhb_net_buy,
        }

        return {
            "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "quote": {
                "price": q.price,
                "change_pct": q.change_pct,
                "open": q.open,
                "high": q.high,
                "low": q.low,
                "prev_close": q.prev_close,
                "pe": q.pe,
                "source": q.source,
            },
            "financial": {
                "营收(亿)": round(f.revenue / 1e8, 2) if f.revenue else 0,
                "营收同比%": f.revenue_yoy,
                "净利润(亿)": round(f.net_profit / 1e8, 2) if f.net_profit else 0,
                "净利润同比%": f.net_profit_yoy,
                "ROE%": f.roe,
                "EPS": f.eps,
                "总资产(亿)": round(f.total_assets / 1e8, 2) if f.total_assets else 0,
                "总负债(亿)": (
                    round(f.total_liabilities / 1e8, 2) if f.total_liabilities else 0
                ),
                "经营现金流(亿)": (
                    round(f.operating_cf / 1e8, 2) if f.operating_cf else 0
                ),
                "报告期": f.report_period,
            },
            "technical": technical,
            "capital_flow": capital_flow,
            "annual_report": _report_dict(info.annual_report),
            "semi_annual_report": _report_dict(info.semi_annual_report),
            "quarterly_report": _report_dict(info.quarterly_report),
            "xueqiu": _join("xueqiu"),
            "eastmoney": _join("eastmoney"),
            "toutiao": _join("toutiao"),
            "wechat": _join("wechat"),
        }

    def _build_report_from_markdown(self, info: StockInfo, md: str) -> AnalysisReport:
        import re as _re

        short = md[:200]
        short = _re.sub(r"\*\*(.*?)\*\*", r"\1", short)
        short = _re.sub(r"##?\s*", "", short)
        short = _re.sub(r"\* ", "• ", short)
        if len(md) > 200:
            short += "..."

        # Compute technical score from K-line indicators
        tech_score = 3
        tech_detail = "详见报告正文（技术面分析）。"
        support_price = 0.0
        resistance_price = 0.0
        trend = ""
        if info.kline and info.kline.bars:
            try:
                from ..indicators.technical import compute_indicators
                from ..indicators.technical import technical_score as _ts

                ind = compute_indicators(info.kline)
                tech_score, tech_detail, support_price, resistance_price, trend = _ts(
                    ind
                )
                # Sanity: support < price < resistance
                price = ind.get("price", 0)
                bad_support = support_price >= price
                bad_resistance = resistance_price <= price
                bad_order = support_price >= resistance_price
                if price and (bad_support or bad_resistance or bad_order):
                    support_price = round(price * 0.92, 2)
                    resistance_price = round(price * 1.08, 2)
            except Exception:
                pass

        # Compute capital flow score
        cap_score = 3
        cap_detail = "详见报告正文（资金面分析）。"
        main_force = "持平"
        if info.capital_flow and info.capital_flow.source != "all_failed":
            try:
                from ..collectors.fund_flow import capital_flow_score as _cfs

                cap_score, cap_detail, main_force = _cfs(info.capital_flow)
            except Exception:
                pass
        # Override capital flow when turnover is extremely low (< 0.1%)
        turnover = info.quote.turnover if info.quote else 0.0
        if 0 < turnover < 0.1:
            cap_score = 3
            cap_detail = "今日交投清淡，主力资金无明显动向，呈观望态势"
            main_force = "持平"

        # Compute sentiment score from social posts
        sent_score = 3
        sent_detail = "详见报告正文（情绪分析）。"
        if info.social_posts:
            pos = sum(
                1 for p in info.social_posts
                if getattr(p, "sentiment", "") == "positive"
            )
            neg = sum(
                1 for p in info.social_posts
                if getattr(p, "sentiment", "") == "negative"
            )
            total = len(info.social_posts)
            if total > 0:
                bull_ratio = pos / total
                if bull_ratio >= 0.6:
                    sent_score = 4
                elif bull_ratio >= 0.5:
                    sent_score = 3
                elif bull_ratio >= 0.4:
                    sent_score = 3
                else:
                    sent_score = 2
                sent_detail = (
                    f"舆情样本{total}条，看多{pos}条"
                    f"({bull_ratio:.0%})，看空{neg}条。"
                )

        # Compute fundamental score from financial data
        fund_score = 3
        fund_detail = "详见报告正文（基本面分析）。"
        f = info.financial
        if f and f.report_period:
            fund_parts = []
            if f.roe > 15:
                fund_score += 1
                fund_parts.append(f"ROE {f.roe}%优秀")
            elif f.roe > 8:
                fund_parts.append(f"ROE {f.roe}%良好")
            elif f.roe > 0:
                fund_score -= 1
                fund_parts.append(f"ROE {f.roe}%偏低")
            if f.revenue_yoy > 10:
                fund_score += 1
                fund_parts.append(f"营收同比+{f.revenue_yoy:.1f}%")
            elif f.revenue_yoy < -5:
                fund_score -= 1
                fund_parts.append(f"营收同比{f.revenue_yoy:.1f}%")
            if f.net_profit_yoy > 10:
                fund_score += 1
            elif f.net_profit_yoy < -10:
                fund_score -= 1
            fund_score = max(1, min(5, fund_score))
            fund_detail = "；".join(fund_parts) if fund_parts else "详见报告正文。"

        # Compute news score from research reports + social
        news_score = 3
        news_detail = "详见报告正文（新闻分析）。"
        if info.research and info.research.total_count > 0:
            buy_ratio = (
                info.research.buy_count / info.research.total_count
                if info.research.total_count > 0
                else 0
            )
            if buy_ratio >= 0.6:
                news_score = 4
            elif buy_ratio <= 0.2:
                news_score = 2
            news_detail = (
                f"机构研报{info.research.total_count}份，"
                f"买入{info.research.buy_count}份，增持{info.research.hold_count}份。"
            )

        # Weighted overall rating from the 5 dimensions
        overall = round(
            sent_score * 0.25
            + tech_score * 0.15
            + fund_score * 0.20
            + cap_score * 0.15
            + news_score * 0.15
        )

        return AnalysisReport(
            symbol=info.symbol,
            name=info.name,
            summary=short,
            report_text=md,
            overall_rating=overall,
            investment_advice="本报告由DeepSeek AI自动生成，仅供参考，不构成投资建议。",
            sentiment=DimensionScore(name="市场情绪", score=sent_score, weight=0.25),
            technical=DimensionScore(name="技术面", score=tech_score, weight=0.15),
            fundamental=DimensionScore(name="基本面", score=fund_score, weight=0.20),
            capital_flow=DimensionScore(name="资金面", score=cap_score, weight=0.15),
            news=DimensionScore(name="新闻舆情", score=news_score, weight=0.15),
            technical_detail=tech_detail,
            capital_flow_detail=cap_detail,
            sentiment_detail=sent_detail,
            fundamental_detail=fund_detail,
            news_detail=news_detail,
            support_price=support_price,
            resistance_price=resistance_price,
            trend=trend,
            main_force=main_force,
        )
