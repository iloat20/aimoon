"""Mock data generators for testing without real APIs."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from ..models.report import AnalysisReport, DimensionScore
from ..models.social import SocialPost
from ..models.stock import (
    FinancialData,
    ResearchReport,
    ResearchReportData,
    StockInfo,
    StockQuote,
)
from ..utils import resolve_market


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


def mock_social_posts(
    platform: str, symbol: str, name: str, count: int = 10
) -> list[SocialPost]:
    """Generate mock social media posts."""
    templates = [
        f"【{name}】这只股票最近走势不错，可以关注",
        f"大家对{symbol}怎么看？最近成交量明显放大",
        f"{name}的财报超出预期，值得长期持有",
        f"注意风险，{symbol}短期涨幅过大",
        f"{name}在行业内的竞争力很强，是个好标的",
        f"短线来看{symbol}有回调压力，长线问题不大",
        f"今天{name}的走势很关键，突破前高就是机会",
        f"{name}发布新公告，市场反应积极",
        f"机构看好{symbol}，目标价上调",
        f"{name}的ROE持续提升，基本面改善",
        f"北向资金增持{symbol}，外资看好",
        f"{name}的估值处于历史低位，安全边际高",
        f"行业政策利好，{name}有望受益",
        f"{symbol}的股息率超过5%，适合长期配置",
        f"{name}的研发投入持续增加，创新能力强",
        f"市场情绪回暖，{symbol}放量上涨",
        f"{name}的管理层变动，关注后续战略调整",
        f"供应链改善，{name}的成本控制成效显著",
        f"{symbol}的技术面出现金叉信号",
        f"{name}的分红方案超预期，股东回报提升",
    ]

    posts = []
    now = datetime.now()
    for i in range(min(count, len(templates))):
        posts.append(
            SocialPost(
                platform=platform,
                title=templates[i],
                content=templates[i]
                + f"\n\n以上内容为mock数据，仅供测试用。平台：{platform}",
                url=f"https://{platform}.com/mock/post/{i}",
                author=f"mock_user_{i}",
                published_at=(now - timedelta(hours=random.randint(0, 48))).isoformat(),
                likes=random.randint(0, 2000),
                comments=random.randint(0, 200),
                shares=random.randint(0, 100),
                views=random.randint(100, 10000),
            )
        )
    return posts


def mock_analysis_report(symbol: str, name: str) -> AnalysisReport:
    """Generate mock AI analysis report."""
    return AnalysisReport(
        symbol=symbol,
        name=name,
        summary=f"综合来看，{name}({symbol})基本面表现稳健，资金面偏暖，"
        f"建议投资者保持关注，逢低布局。",
        fundamental=DimensionScore(
            name="基本面",
            score=4,
            weight=0.30,
            analysis="营收和净利润双增长，ROE保持在较高水平，现金流充裕，基本面扎实。",
        ),
        capital_flow=DimensionScore(
            name="资金面",
            score=3,
            weight=0.15,
            analysis="近期主力资金呈净流入状态，北向资金小幅增持，资金面偏暖。",
        ),
        news=DimensionScore(
            name="新闻舆情",
            score=3,
            weight=0.15,
            analysis="近期相关新闻报道以中性偏正面为主，无重大负面事件。",
        ),
        fundamental_detail="PE处于行业中等水平，ROE约15%，成长性良好。",
        capital_flow_detail="主力资金近5日净流入约2亿元，北向持股比例微增。",
        news_detail="近期无重大利好或利空消息，市场关注度一般。",
        main_force="小幅流入",
        investment_advice=(
            "【免责声明】本报告由AI自动生成，仅供参考，不构成任何投资建议。"
            "投资有风险，入市需谨慎。请结合自身情况独立决策。"
        ),
        report_text=(
            f"## 一、公司概况与业务分析\n\n"
            f"{name}（{symbol}）是行业内的龙头企业，主营业务涵盖多个领域。"
            f"公司在行业内具有较强的竞争优势和品牌影响力。\n\n"
            f"## 二、财务健康度评估\n\n"
            f"公司财务状况良好，营收和净利润保持稳定增长。ROE处于较高水平，"
            f"现金流充裕，资产负债率在合理范围内。\n\n"
            f"## 三、资金面分析\n\n"
            f"近期主力资金呈净流入状态，北向资金小幅增持。"
            f"资金面整体偏暖，有助于股价企稳回升。\n\n"
            f"## 四、行业与市场前景\n\n"
            f"行业整体处于稳定增长阶段，公司作为龙头企业有望持续受益。"
            f"市场关注度适中，机构持仓比例合理。\n\n"
            f"## 五、投资建议与风险提示\n\n"
            f"评级：【中性持有】\n"
            f"建议投资者保持关注，在回调时逢低布局。\n"
        ),
    )


def mock_stock_info(symbol: str) -> StockInfo:
    """Generate complete mock stock info."""
    name = f"测试股{symbol}"
    return StockInfo(
        symbol=symbol,
        name=name,
        market=resolve_market(symbol),
        quote=mock_quote(symbol, name),
        financial=mock_financial(symbol),
        social_posts=[
            *mock_social_posts("雪球", symbol, name, 5),
            *mock_social_posts("东方财富股吧", symbol, name, 5),
            *mock_social_posts("今日头条", symbol, name, 3),
            *mock_social_posts("微信公众号", symbol, name, 3),
        ],
        research=ResearchReportData(
            symbol=symbol,
            reports=[
                ResearchReport(
                    title=f"{name}深度报告：业绩稳健增长",
                    institution="中信证券",
                    rating="买入",
                    industry="消费",
                    date="2026-06-15",
                    eps_this_yr=12.5,
                    pe_this_yr=18.3,
                    eps_next_yr=14.2,
                    pe_next_yr=16.1,
                ),
                ResearchReport(
                    title=f"{name}季报点评：符合预期",
                    institution="国泰君安",
                    rating="增持",
                    industry="消费",
                    date="2026-05-20",
                    eps_this_yr=12.0,
                    pe_this_yr=19.0,
                    eps_next_yr=13.8,
                    pe_next_yr=16.5,
                ),
            ],
            source="Mock数据",
            total_count=2,
            buy_count=1,
            hold_count=1,
            neutral_count=0,
            avg_eps_this_yr=12.25,
            avg_pe_this_yr=18.65,
        ),
    )
