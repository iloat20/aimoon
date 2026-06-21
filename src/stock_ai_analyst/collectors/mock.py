"""Mock data generators for testing without real APIs."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from ..models.stock import FinancialData, StockInfo, StockQuote
from ..models.social import SocialPost
from ..models.report import AnalysisReport, DimensionScore


def mock_quote(symbol: str, name: str = "") -> StockQuote:
    """Generate realistic mock quote data."""
    base_price = random.uniform(5, 200)
    change_pct = random.uniform(-5, 5)
    change = base_price * change_pct / 100

    return StockQuote(
        symbol=symbol,
        name=name or f"测试股票{symbol}",
        price=round(base_price + change, 2),
        change=round(change, 2),
        change_pct=round(change_pct, 2),
        volume=random.randint(1000000, 50000000),
        amount=round(random.uniform(100_000_000, 500_000_000), 2),
        high=round(base_price * random.uniform(1.01, 1.05), 2),
        low=round(base_price * random.uniform(0.95, 0.99), 2),
        open=round(base_price, 2),
        prev_close=round(base_price, 2),
        turnover=round(random.uniform(0.5, 5.0), 2),
        pe=round(random.uniform(10, 80), 2),
        source="Mock数据",
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def mock_financial(symbol: str) -> FinancialData:
    """Generate realistic mock financial data."""
    revenue = random.uniform(1e9, 1e11)
    return FinancialData(
        symbol=symbol,
        report_period="2025Q4",
        revenue=round(revenue, 2),
        revenue_yoy=round(random.uniform(-10, 30), 2),
        net_profit=round(revenue * random.uniform(0.05, 0.25), 2),
        net_profit_yoy=round(random.uniform(-15, 40), 2),
        total_assets=round(revenue * random.uniform(0.8, 3.0), 2),
        total_liabilities=round(revenue * random.uniform(0.3, 1.5), 2),
        equity=round(revenue * random.uniform(0.5, 2.0), 2),
        operating_cf=round(revenue * random.uniform(0.02, 0.15), 2),
        investing_cf=round(revenue * random.uniform(-0.2, 0.05), 2),
        financing_cf=round(revenue * random.uniform(-0.1, 0.1), 2),
        roe=round(random.uniform(5, 35), 2),
        eps=round(random.uniform(0.5, 10), 2),
        bvps=round(random.uniform(5, 50), 2),
        source="Mock数据",
    )


def mock_social_posts(platform: str, symbol: str, name: str, count: int = 10) -> list[SocialPost]:
    """Generate mock social media posts."""
    sentiments = ["positive", "neutral", "negative"]
    weights = [0.45, 0.35, 0.2]

    templates = [
        f"【{name}】这只股票最近走势不错，可以关注",
        f"大家对{symbol}怎么看？最近成交量明显放大",
        f"{name}的财报超出预期，值得长期持有",
        f"注意风险，{symbol}短期涨幅过大",
        f"{name}在行业内的竞争力很强，是个好标的",
        f"短线来看{symbol}有回调压力，长线问题不大",
        f"今天{name}的走势很关键，突破前高就是机会",
    ]

    posts = []
    now = datetime.now()
    for i in range(min(count, len(templates))):
        posts.append(SocialPost(
            platform=platform,
            title=templates[i],
            content=templates[i] + f"\n\n以上内容为mock数据，仅供测试用。平台：{platform}",
            url=f"https://{platform}.com/mock/post/{i}",
            author=f"mock_user_{i}",
            published_at=(now - timedelta(hours=random.randint(0, 48))).isoformat(),
            likes=random.randint(0, 2000),
            comments=random.randint(0, 200),
            shares=random.randint(0, 100),
            views=random.randint(100, 10000),
            sentiment=random.choices(sentiments, weights=weights, k=1)[0],
        ))
    return posts


def mock_analysis_report(symbol: str, name: str) -> AnalysisReport:
    """Generate mock AI analysis report."""
    return AnalysisReport(
        symbol=symbol,
        name=name,
        summary=f"综合来看，{name}({symbol})目前处于震荡整理阶段。市场情绪偏中性，"
                f"基本面表现稳健，技术面存在支撑。建议投资者保持关注，逢低布局。",
        sentiment=DimensionScore(
            name="市场情绪", score=3, weight=0.25,
            analysis="社交媒体讨论热度中等，看多与看空观点分歧明显，整体偏中性。"
        ),
        technical=DimensionScore(
            name="技术面", score=4, weight=0.15,
            analysis="股价处于上升通道中，均线多头排列，成交量配合良好，短期看好。"
        ),
        fundamental=DimensionScore(
            name="基本面", score=4, weight=0.20,
            analysis="营收和净利润双增长，ROE保持在较高水平，现金流充裕，基本面扎实。"
        ),
        capital_flow=DimensionScore(
            name="资金面", score=3, weight=0.15,
            analysis="近期主力资金呈净流入状态，北向资金小幅增持，资金面偏暖。"
        ),
        news=DimensionScore(
            name="新闻舆情", score=3, weight=0.15,
            analysis="近期相关新闻报道以中性偏正面为主，无重大负面事件。"
        ),
        overall_rating=4,
        sentiment_detail="社区讨论热度中等，多头与空头均有一定数量，观点分歧明显。",
        technical_detail="股价在20日均线上方运行，MACD金叉，短期趋势向好。",
        fundamental_detail="PE处于行业中等水平，ROE约15%，成长性良好。",
        capital_flow_detail="主力资金近5日净流入约2亿元，北向持股比例微增。",
        news_detail="近期无重大利好或利空消息，市场关注度一般。",
        bullish_ratio=0.55,
        trend="震荡偏多",
        support_price=round(random.uniform(5, 50), 2),
        resistance_price=round(random.uniform(6, 60), 2),
        main_force="小幅流入",
        news_sentiment="中性",
        key_topics=["财报发布", "行业政策", "机构调研", "分红方案"],
        key_events=["发布年报", "高管增持"],
        risk_warnings=[
            "市场整体波动风险",
            "行业政策变化风险",
            "公司业绩不达预期风险",
        ],
        investment_advice=(
            "【免责声明】本报告由AI自动生成，仅供参考，不构成任何投资建议。"
            "投资有风险，入市需谨慎。请结合自身情况独立决策。"
        ),
    )


def mock_stock_info(symbol: str) -> StockInfo:
    """Generate complete mock stock info."""
    name = f"测试股{symbol}"
    return StockInfo(
        symbol=symbol,
        name=name,
        market="SH" if symbol.startswith("6") else "SZ",
        quote=mock_quote(symbol, name),
        financial=mock_financial(symbol),
        social_posts=[
            *mock_social_posts("雪球", symbol, name, 5),
            *mock_social_posts("东方财富股吧", symbol, name, 5),
            *mock_social_posts("今日头条", symbol, name, 3),
            *mock_social_posts("小红书", symbol, name, 3),
            *mock_social_posts("抖音", symbol, name, 2),
            *mock_social_posts("微信公众号", symbol, name, 3),
        ],
    )
