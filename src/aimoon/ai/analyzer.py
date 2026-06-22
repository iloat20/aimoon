"""DeepSeek AI analysis engine.

Two modes:
1. DeepSeekAnalyzer — uses PROMPT_TEMPLATE to call DeepSeek API directly
2. AIAnalyzer — unified interface for HTML template compatibility
"""

from __future__ import annotations

import json

import httpx

from ..config.settings import get_settings
from ..models.report import AnalysisReport, DimensionScore
from ..models.stock import StockInfo

PROMPT_TEMPLATE = """你是一位拥有20年实战经验的资深A股策略分析师，擅长基本面分析、技术面分析及市场情绪量化。

【核心规则：禁止编造数据】
- 每个数据来源均已标注：(采集时间: {timestamp})
- 对于标记为"暂无数据"的板块，你必须写"因数据源暂不可用，该部分无法分析"，严禁自行编造或猜测
- 所有引用数据必须来自上面【输入数据】中提供的内容，不得添加任何外部信息
- 如果财务数据中某项指标缺失（值为0或null），标注"暂无"，不要估算
- 引用舆情时必须注明具体平台来源

【当前分析标的】
股票代码：{stock_code}
股票名称：{stock_name}
当前价格/估值：{quote_data}

【输入数据】（采集时间: {timestamp}）
1. 财务报告核心数据：
{financial_data}

2. 技术面数据（基于{tech_bars}根日K线计算）：
{technical_data}

3. 资金面数据：
{capital_flow_data}

4. 全网舆情信息摘要：
- 雪球网讨论摘要：{xueqiu_data}
- 东方财富股吧热帖摘要：{eastmoney_data}
- 抖音/快手短视频舆论摘要：{douyin_data}
- 今日头条及新闻文章摘要：{toutiao_data}
- 微信公众号文章摘要：{wechat_data}

【输出要求】
请严格按照以下Markdown格式输出分析报告，不要输出任何多余的解释或开场白。每条结论必须与输入数据对应，不允许自行推算未提供的数据。

## 一、公司概况与业务分析

## 二、财务健康度评估

## 三、市场情绪与舆情分析
- 雪球网：
- 东方财富股吧：
- 其他社交媒体（抖音、公众号等）：

## 四、技术面分析
（基于输入数据中的技术面K线指标进行分析，包括均线趋势、MACD、RSI、布林带位置、支撑阻力位等。如果技术面数据为"暂无数据"，请说明"技术面数据暂不可用"）

## 五、资金面分析
（基于输入数据中的资金流向数据进行分析，包括主力资金净流入/流出、超大单大单动向、北向资金变化等。如果资金面数据为"暂无数据"，请说明"资金面数据暂不可用"）

## 六、综合投资建议与评级
评级：【强力买入】/【买入】/【中性持有】/【减持】/【卖出】
建仓参考区间：（综合技术面支撑位和基本面给出具体价格区间）
止损参考位：（基于技术面支撑位下方给出具体价格）
止盈参考位：（基于技术面阻力位上方给出具体价格）

【逻辑一致性检查清单（输出前请逐条自查）】
1. 如果财务数据显示营收/利润下滑，评级不得为【强力买入】
2. 如果市场情绪分析为普遍悲观，评级不得为【买入】以上
3. 如果技术面显示下降趋势且资金面持续流出，评级不得为【买入】以上
4. 评级与正文中的分析结论必须一致

【写作风格要求】
语言简洁明了，通俗易懂，逻辑严密。避免使用过度复杂的金融模型术语，让普通投资者也能清晰理解。每个部分控制在150-200字左右，整体报告总字数控制在1500-2000字。
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

    def _build_prompt(self, stock_code: str, stock_name: str, data: dict) -> str:
        quote = data.get("quote", {})
        quote_data = (
            f"价格{quote.get('price', 'N/A')}元 "
            f"涨跌{quote.get('change_pct', 'N/A')}% "
            f"PE={quote.get('pe', 'N/A')}"
        )
        financial = data.get("financial", {})
        financial_data = json.dumps(financial, ensure_ascii=False, indent=2)

        # Technical indicators (pre-computed from K-line)
        tech_raw = data.get("technical", {})
        tech_bars = tech_raw.get("bars", 0)
        if tech_raw and tech_bars >= 10:
            tech_lines = []
            for k in (
                "price",
                "ma5",
                "ma10",
                "ma20",
                "ma60",
                "macd_dif",
                "macd_dea",
                "macd_hist",
                "rsi6",
                "rsi14",
                "boll_upper",
                "boll_mid",
                "boll_lower",
                "support",
                "resistance",
                "volume_ratio",
            ):
                if k in tech_raw:
                    tech_lines.append(f"- {k}: {tech_raw[k]}")
            for k in ("trend", "boll_position"):
                if k in tech_raw:
                    tech_lines.append(f"- {k}: {tech_raw[k]}")
            for k in ("ret_5d", "ret_20d", "ret_60d"):
                if k in tech_raw:
                    tech_lines.append(f"- {k}%: {tech_raw[k]}")
            technical_data = "\n".join(tech_lines) if tech_lines else "暂无数据"
        else:
            technical_data = "暂无数据（K线数据不足）"

        # Capital flow data
        cf = data.get("capital_flow", {})
        if cf and cf.get("main_net_5d") != 0:
            cf_lines = [
                f"- 近5日主力净流入: {cf['main_net_5d'] / 1e8:.2f}亿元",
                f"- 今日主力净流入: {cf['main_net_today'] / 1e8:.2f}亿元",
                f"- 超大单净流入: {cf.get('super_large_net', 0) / 1e8:.2f}亿元",
                f"- 大单净流入: {cf.get('large_net', 0) / 1e8:.2f}亿元",
                f"- 中单净流入: {cf.get('medium_net', 0) / 1e8:.2f}亿元",
                f"- 小单净流入: {cf.get('small_net', 0) / 1e8:.2f}亿元",
            ]
            if cf.get("northbound_chg"):
                cf_lines.append(
                    f"- 北向资金变化: {cf['northbound_chg'] / 1e8:+.2f}亿元"
                )
            if cf.get("lhb_date"):
                cf_lines.append(
                    f"- 龙虎榜({cf['lhb_date']}): {cf.get('lhb_reason', '')}"
                    f" 净买入{cf['lhb_net_buy'] / 1e8:.2f}亿元"
                )
            capital_flow_data = "\n".join(cf_lines)
        else:
            capital_flow_data = "暂无数据"

        from datetime import datetime

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        return PROMPT_TEMPLATE.format(
            timestamp=timestamp,
            stock_code=stock_code,
            stock_name=stock_name or stock_code,
            quote_data=quote_data,
            financial_data=financial_data,
            technical_data=technical_data,
            tech_bars=tech_bars,
            capital_flow_data=capital_flow_data,
            xueqiu_data=data.get("xueqiu", "暂无数据")[:2000],
            eastmoney_data=data.get("eastmoney", "暂无数据")[:2000],
            douyin_data=data.get("douyin", "暂无数据")[:1000],
            toutiao_data=data.get("toutiao", "暂无数据")[:1000],
            wechat_data=data.get("wechat", "暂无数据")[:1000],
        )


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
        q = info.quote
        f = info.financial
        cf = info.capital_flow

        texts: dict[str, list[str]] = {
            "xueqiu": [],
            "eastmoney": [],
            "douyin": [],
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
            elif "抖音" in plat:
                texts["douyin"].append(line)
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
            "main_net_today": cf.main_net_today,
            "super_large_net": cf.super_large_net,
            "large_net": cf.large_net,
            "medium_net": cf.medium_net,
            "small_net": cf.small_net,
            "northbound_chg": cf.northbound_chg,
            "lhb_date": cf.lhb_date,
            "lhb_reason": cf.lhb_reason,
            "lhb_net_buy": cf.lhb_net_buy,
        }

        return {
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
            "xueqiu": _join("xueqiu"),
            "eastmoney": _join("eastmoney"),
            "douyin": _join("douyin"),
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
