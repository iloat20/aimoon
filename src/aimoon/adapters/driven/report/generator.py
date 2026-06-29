"""HTML report generator using Jinja2 templates."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from aimoon.core.application.ports import ReportGenerator as ReportGeneratorPort
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.value_objects.analysis_report import AnalysisReport
from aimoon.core.domain.value_objects.collect_result import CollectResult

from .... import __version__
from ..config.settings import get_settings

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _md_to_html(md_text: str) -> Markup:
    """Convert markdown text to safe HTML with XSS sanitization."""
    import re

    import bleach
    import markdown as md_lib

    html = md_lib.markdown(md_text, extensions=["extra", "nl2br"])
    html = bleach.clean(
        html,
        tags=[
            "p",
            "br",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "strong",
            "em",
            "ul",
            "ol",
            "li",
            "code",
            "pre",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "blockquote",
            "hr",
            "del",
            "sub",
            "sup",
            "span",
            "a",
        ],
        attributes={"a": ["href", "title"], "*": ["class"]},
        strip=True,
    )
    html = re.sub(r"<br\s*/?>\s*<br\s*/?>", "<br>", html)
    return Markup(html)


def _cn_number(n: int) -> str:
    if n >= 1e8:
        return f"{n / 1e8:.1f}亿"
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


class HtmlReportGenerator(ReportGeneratorPort):
    """Generates HTML stock analysis reports."""

    def __init__(self) -> None:
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
        env.filters["md_to_html"] = _md_to_html
        self._env = env
        self._css_content = (_TEMPLATE_DIR / "style.css").read_text(encoding="utf-8")

    def generate(
        self,
        stock_info: StockAnalysis,
        analysis: AnalysisReport,
        collect_results: list[CollectResult],
        output_dir: str | None = None,
    ) -> Path:
        """Generate HTML report and save to output directory.

        Implements ReportGenerator port. Accepts domain entities as input.
        """
        settings = get_settings()

        if output_dir is None:
            output_dir = str(settings.output_path)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(output_dir) / f"{stock_info.symbol}_{ts}.html"
        out.parent.mkdir(parents=True, exist_ok=True)

        ctx = self._build_context(stock_info, analysis, collect_results)

        template = self._env.get_template("index.html")
        html = template.render(**ctx)

        out.write_text(html, encoding="utf-8")
        return out

    def _build_context(
        self,
        stock_info: StockAnalysis,
        analysis: AnalysisReport,
        collect_results: list[CollectResult],
    ) -> dict:
        q = stock_info.quote
        # 产品决策：平盘（涨跌幅=0）归为 up 类，使用红色显示
        # 符合A股市场习惯：平盘不跌即为"不弱"，用红色表示
        change_class = "up" if q.change_pct >= 0 else "down"

        all_posts = stock_info.social_posts
        platform_stats: dict[str, dict] = {}
        for p in all_posts:
            if p.platform not in platform_stats:
                platform_stats[p.platform] = {
                    "count": 0,
                    "top_posts": [],
                }
            s = platform_stats[p.platform]
            s["count"] += 1
            s["top_posts"].append(p)

        for s in platform_stats.values():
            s["top_posts"].sort(key=lambda x: x.likes or 0, reverse=True)
            s["top_posts"] = s["top_posts"][:20]

        return {
            "symbol": stock_info.symbol,
            "name": stock_info.name,
            "market": stock_info.market,
            "version": __version__,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "quote": q,
            "financial": stock_info.financial,
            "change_class": change_class,
            "change_sign": "+" if q.change_pct >= 0 else "",
            "analysis": analysis,
            "platform_stats": platform_stats,
            "collect_results": collect_results,
            "total_posts": len(all_posts),
            "total_failed": sum(1 for r in collect_results if r.status == "failed"),
            "research": stock_info.research,
            "capital_flow": stock_info.capital_flow,
            "kline": stock_info.kline,
            "cn_number": _cn_number,
            "report_text": analysis.report_text,
            "css_content": Markup(self._css_content),
        }
