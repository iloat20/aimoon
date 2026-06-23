"""HTML report generator using Jinja2 templates."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..config.settings import get_settings
from ..models.report import AnalysisReport
from ..models.social import CollectResult
from ..models.stock import StockInfo

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _score_color(score: int) -> str:
    if score >= 4:
        return "#ef4444"  # red = strong
    if score >= 3:
        return "#f59e0b"  # amber = neutral
    return "#22c55e"  # green = weak


def _score_label(score: int) -> str:
    labels = {5: "强烈看好", 4: "看好", 3: "中性", 2: "偏弱", 1: "弱势", 0: "无评级"}
    return labels.get(score, "N/A")


def _md_to_html(md_text: str) -> str:
    """Convert markdown text to safe HTML."""
    import re

    import markdown as md_lib

    html = md_lib.markdown(md_text, extensions=["extra", "nl2br"])
    # Clean excessive line breaks
    html = re.sub(r"<br\s*/?>\s*<br\s*/?>", "<br>", html)
    return html


def _sentiment_emoji(sentiment: str) -> str:
    return {"positive": "😊", "negative": "😟", "neutral": "😐"}.get(sentiment, "")


def _cn_number(n: int) -> str:
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def _clamp(val: float, lo: float, hi: float) -> float:
    """Clamp value between lo and hi."""
    return max(lo, min(hi, val))


class ReportGenerator:
    """Generates HTML stock analysis reports."""

    def __init__(self) -> None:
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
        env.filters["md_to_html"] = _md_to_html
        env.globals["clamp"] = _clamp
        self._env = env

    def generate(
        self,
        stock_info: StockInfo,
        analysis: AnalysisReport,
        collect_results: list[CollectResult],
        output_dir: str | None = None,
    ) -> Path:
        """Generate HTML report and save to output directory."""
        settings = get_settings()

        if output_dir is None:
            output_dir = str(settings.output_path)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(output_dir) / f"{stock_info.symbol}_{ts}.html"
        out.parent.mkdir(parents=True, exist_ok=True)

        # Build context
        ctx = self._build_context(stock_info, analysis, collect_results)

        # Render
        template = self._env.get_template("index.html")
        html = template.render(**ctx)

        out.write_text(html, encoding="utf-8")
        return out

    def _build_context(
        self,
        stock_info: StockInfo,
        analysis: AnalysisReport,
        collect_results: list[CollectResult],
    ) -> dict:
        q = stock_info.quote
        change_class = "up" if q.change_pct >= 0 else "down"

        dimensions = [
            {
                "name": analysis.sentiment.name,
                "score": analysis.sentiment.score,
                "weight": analysis.sentiment.weight,
                "detail": analysis.sentiment_detail,
            },
            {
                "name": analysis.technical.name,
                "score": analysis.technical.score,
                "weight": analysis.technical.weight,
                "detail": analysis.technical_detail,
            },
            {
                "name": analysis.fundamental.name,
                "score": analysis.fundamental.score,
                "weight": analysis.fundamental.weight,
                "detail": analysis.fundamental_detail,
            },
            {
                "name": analysis.capital_flow.name,
                "score": analysis.capital_flow.score,
                "weight": analysis.capital_flow.weight,
                "detail": analysis.capital_flow_detail,
            },
            {
                "name": analysis.news.name,
                "score": analysis.news.score,
                "weight": analysis.news.weight,
                "detail": analysis.news_detail,
            },
        ]

        # Social stats
        all_posts = stock_info.social_posts
        platform_stats: dict[str, dict] = {}
        for p in all_posts:
            if p.platform not in platform_stats:
                platform_stats[p.platform] = {
                    "count": 0,
                    "pos": 0,
                    "neg": 0,
                    "neu": 0,
                    "top_posts": [],
                }
            s = platform_stats[p.platform]
            s["count"] += 1
            s["pos"] += 1 if p.sentiment == "positive" else 0
            s["neg"] += 1 if p.sentiment == "negative" else 0
            s["neu"] += 1 if p.sentiment == "neutral" else 0
            s["top_posts"].append(p)

        # Sort top_posts by likes (descending) for each platform
        for s in platform_stats.values():
            s["top_posts"].sort(key=lambda x: x.likes or 0, reverse=True)
            s["top_posts"] = s["top_posts"][:20]

        return {
            "symbol": stock_info.symbol,
            "name": stock_info.name,
            "market": stock_info.market,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "quote": q,
            "financial": stock_info.financial,
            "change_class": change_class,
            "change_sign": "+" if q.change_pct >= 0 else "",
            "analysis": analysis,
            "dimensions": dimensions,
            "overall_rating": analysis.overall_rating,
            "overall_color": _score_color(analysis.overall_rating),
            "overall_label": _score_label(analysis.overall_rating),
            "bul_ratio": int(analysis.bullish_ratio * 100),
            "bear_ratio": int((1 - analysis.bullish_ratio) * 100),
            # Sentiment distribution (estimated from news sentiment + bullish ratio)
            "senti_pos_pct": max(10, min(80, int(analysis.bullish_ratio * 100))),
            "senti_neu_pct": 30,
            "senti_neg_pct": max(
                10, min(80, int((1 - analysis.bullish_ratio) * 100 - 10))
            ),
            "platform_stats": platform_stats,
            "collect_results": collect_results,
            "total_posts": len(all_posts),
            "total_failed": sum(1 for r in collect_results if r.status == "failed"),
            "research": stock_info.research,
            "capital_flow": stock_info.capital_flow,
            "kline": stock_info.kline,
            "score_color": _score_color,
            "score_label": _score_label,
            "sentiment_emoji": _sentiment_emoji,
            "cn_number": _cn_number,
            "advice": analysis.investment_advice,
            "key_events": analysis.key_events,
            # AI report full text (rendered as HTML)
            "report_text": analysis.report_text,
            "report_summary": analysis.summary,
        }
