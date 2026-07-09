"""Mock data generators for testing without real APIs."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

# mock_analysis_report moved to common/mock.py (audit P2.3 — cross-adapter decoupling).
# Re-exported here for backward compatibility with existing imports.
from aimoon.adapters.driven.common.mock import mock_analysis_report  # noqa: E402,F401
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.financial import FinancialData
from aimoon.core.domain.entities.kline import KlineData
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.entities.research import ResearchReport, ResearchReportData
from aimoon.core.domain.entities.social import SocialPost
from aimoon.core.domain.services.symbols import resolve_market
from aimoon.core.domain.value_objects.financial_report import FinancialReportData
from aimoon.core.domain.value_objects.kline_bar import KlineBar


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
        accounts_receivable=round(revenue * random.uniform(0.05, 0.20), 2),
        inventory=round(revenue * random.uniform(0.03, 0.15), 2),
        dividend_paid=round(revenue * random.uniform(0.01, 0.08), 2),
        source="Mock数据",
    )


def mock_financial_report(symbol: str, report_type: str, year: str) -> FinancialReportData:
    """Generate mock financial report metadata."""
    type_names = {
        "annual": "年度报告",
        "semi_annual": "半年度报告",
        "quarterly": "季度报告",
    }
    type_name = type_names.get(report_type, "报告")
    return FinancialReportData(
        year=year,
        title=f"{year}年{type_name}",
        pdf_url=f"https://static.cninfo.com.cn/mock/{symbol}_{year}_{report_type}.pdf",
    )


def mock_social_posts(platform: str, symbol: str, name: str, count: int = 10) -> list[SocialPost]:
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
                content=templates[i] + f"\n\n以上内容为mock数据，仅供测试用。平台：{platform}",
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


def mock_kline(symbol: str, days: int = 120) -> KlineData:
    """Generate realistic mock K-line data."""
    base_price = random.uniform(10, 100)
    bars: list[KlineBar] = []
    current_price = base_price
    now = datetime.now()

    for i in range(days):
        date = (now - timedelta(days=days - i - 1)).strftime("%Y-%m-%d")
        change_pct = random.gauss(0, 2.5)
        open_price = round(current_price, 2)
        close_price = round(current_price * (1 + change_pct / 100), 2)
        high_price = round(max(open_price, close_price) * random.uniform(1.0, 1.03), 2)
        low_price = round(min(open_price, close_price) * random.uniform(0.97, 1.0), 2)
        volume = random.randint(5_000_000, 50_000_000)
        amount = round(volume * close_price / 100_000_000, 2)
        pct_change = round(change_pct, 2)

        bars.append(
            KlineBar(
                date=date,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
                amount=amount,
                pct_change=pct_change,
            )
        )
        current_price = close_price

    return KlineData(
        symbol=symbol,
        bars=bars,
        source="Mock数据",
        period="daily",
    )


def mock_capital_flow(symbol: str) -> CapitalFlowData:
    """Generate realistic mock capital flow data."""
    main_5d = random.uniform(-3e8, 5e8)
    net_3d = random.uniform(-2e8, 3e8)
    net_10d = random.uniform(-5e8, 8e8)
    net_20d = random.uniform(-10e8, 15e8)
    northbound_chg = random.uniform(-0.5e8, 2e8)

    return CapitalFlowData(
        symbol=symbol,
        main_net_5d=round(main_5d, 2),
        main_net_3d=round(net_3d, 2),
        main_net_10d=round(net_10d, 2),
        main_net_20d=round(net_20d, 2),
        northbound_chg=round(northbound_chg, 2),
        northbound_hold_ratio=round(random.uniform(0.5, 5.0), 2),
        northbound_date=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        source="Mock数据",
    )


def mock_stock_analysis(symbol: str) -> StockAnalysis:
    """Generate complete mock stock analysis."""
    name = f"测试股{symbol}"
    return StockAnalysis(
        symbol=symbol,
        name=name,
        market=resolve_market(symbol),
        quote=mock_quote(symbol, name),
        financial=mock_financial(symbol),
        kline=mock_kline(symbol),
        capital_flow=mock_capital_flow(symbol),
        social_posts=tuple(
            [
                *mock_social_posts("雪球", symbol, name, 5),
                *mock_social_posts("东方财富股吧", symbol, name, 5),
                *mock_social_posts("今日头条", symbol, name, 3),
                *mock_social_posts("微信公众号", symbol, name, 3),
            ]
        ),
        research=ResearchReportData(
            symbol=symbol,
            reports=[
                ResearchReport(
                    title=f"{name}深度报告：业绩稳健增长",
                    institution="中信证券",
                    rating="买入",
                    industry="消费",
                    date="2026-06-15",
                    pdf_url=f"https://example.com/research/{symbol}_1.pdf",
                    eps_this_yr=12.5,
                    pe_this_yr=18.3,
                    eps_next_yr=14.2,
                    pe_next_yr=16.1,
                    eps_future_yr=16.0,
                    pe_future_yr=14.3,
                ),
                ResearchReport(
                    title=f"{name}季报点评：符合预期",
                    institution="国泰君安",
                    rating="增持",
                    industry="消费",
                    date="2026-05-20",
                    pdf_url=f"https://example.com/research/{symbol}_2.pdf",
                    eps_this_yr=12.0,
                    pe_this_yr=19.0,
                    eps_next_yr=13.8,
                    pe_next_yr=16.5,
                    eps_future_yr=15.5,
                    pe_future_yr=14.7,
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
        annual_report=mock_financial_report(symbol, "annual", "2025"),
        semi_annual_report=mock_financial_report(symbol, "semi_annual", "2025"),
        quarterly_report=mock_financial_report(symbol, "quarterly", "2026Q1"),
    )
