"""Prompt construction for the AI analyzer.

Flattens a :class:`StockAnalysis` aggregate into a :class:`PromptContext`
and renders the user message. Pure (no IO, no settings access).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.financial import FinancialData, QuarterlyFinancialData
from aimoon.core.domain.entities.quote import StockQuote

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


def detect_industry(symbol: str, name: str) -> str:
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


@dataclass(frozen=True)
class PromptContext:
    """Flattened view of a StockAnalysis, ready for prompt building."""

    symbol: str
    name: str
    current_time: str
    quote: dict[str, Any]
    financial: dict[str, Any]
    quarterly_financial: dict[str, Any]
    capital_flow: dict[str, Any]
    annual_report: Any
    semi_annual_report: Any
    quarterly_report: Any
    financial_md_path: str | None
    industry: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_time": self.current_time,
            "quote": self.quote,
            "financial": self.financial,
            "quarterly_financial": self.quarterly_financial,
            "capital_flow": self.capital_flow,
            "annual_report": self.annual_report,
            "semi_annual_report": self.semi_annual_report,
            "quarterly_report": self.quarterly_report,
            "financial_md_path": self.financial_md_path,
            "industry": self.industry,
        }


def build_data_dict(
    info: StockAnalysis,
    reports: dict | None = None,
    financial_md_path: Path | None = None,
) -> PromptContext:
    """Flatten a StockAnalysis into a PromptContext."""
    quote = info.quote or StockQuote()
    financial = info.financial or FinancialData()
    quarterly = info.quarterly_financial or QuarterlyFinancialData()
    capital_flow = info.capital_flow or CapitalFlowData()

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

    financial_dict = {
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
    }

    quarterly_dict = {
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
    }

    return PromptContext(
        symbol=info.symbol,
        name=info.name,
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        quote={
            "price": quote.price or None,
            "change_pct": quote.change_pct,
            "open": quote.open,
            "high": quote.high,
            "low": quote.low,
            "prev_close": quote.prev_close,
            "pe": quote.pe,
            "source": quote.source,
        },
        financial=financial_dict,
        quarterly_financial=quarterly_dict,
        capital_flow=capital_flow_dict,
        annual_report=info.annual_report,
        semi_annual_report=info.semi_annual_report,
        quarterly_report=info.quarterly_report,
        financial_md_path=str(financial_md_path) if financial_md_path else None,
        industry=detect_industry(info.symbol, info.name),
    )


def build_user_message(stock_code: str, stock_name: str, data: PromptContext) -> str:
    """Render the user message from a flattened PromptContext."""
    from .prompts import USER_PROMPT_TEMPLATE

    d = data.to_dict()
    quote = d.get("quote", {})
    quote_data = (
        f"价格{quote.get('price', 'N/A')}元 "
        f"涨跌{quote.get('change_pct', 'N/A')}% "
        f"PE={quote.get('pe', 'N/A')}"
    )
    current_time = d.get("current_time", "")
    base = USER_PROMPT_TEMPLATE.format(
        stock_code=stock_code,
        stock_name=stock_name or stock_code,
        quote_data=quote_data,
        current_time=current_time,
    )

    sections = [base]

    financial = d.get("financial", {})
    # financial dict keys are short (e.g. "period", "rev")
    # built by build_data_dict, used directly for prompt display
    if financial and financial.get("period"):
        sections.append(f"\n\n【已采集财务数据（{financial.get('period', '')}）】")
        for k, v in financial.items():
            if v and v != 0:
                sections.append(f"- {k}: {v}")

    # Quarterly/semi-annual financial data
    quarterly = d.get("quarterly_financial", {})
    if quarterly and quarterly.get("period"):
        sections.append(
            f"\n\n【最近一期季报/中报（{quarterly.get('period', '')}，"
            f"{quarterly.get('type', '')}）】"
        )
        for k, v in quarterly.items():
            if v and v != 0 and k not in ("period",):
                sections.append(f"- {k}: {v}")

    md_path = d.get("financial_md_path")
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
            report = d.get(rkey)
            if report and report.content:
                sections.append(f"\n\n【{rlabel}原文摘要（{report.year}年）】")
                sections.append(report.content)

    sections.extend(_format_capital_flow(d))
    sections.extend(_format_social_kline(d))

    return "".join(sections)


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


def _format_social_kline(data: dict) -> list[str]:
    """Format social media data sections (K-line is intentionally excluded)."""
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
    return parts
