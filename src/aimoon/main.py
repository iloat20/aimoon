"""Stock AI Analyst - CLI entry point.

Usage:
    uv run stock-analyst 000001              # Basic analysis
    uv run stock-analyst 000001 --mock       # Mock mode (no API needed)
    uv run stock-analyst 000001 -o ./reports # Custom output dir
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from .ai.analyzer import AIAnalyzer
from .collectors.base import CollectorRegistry
from .config.settings import get_settings
from .models.stock import StockInfo
from .report.generator import ReportGenerator


def _resolve_symbol(raw: str) -> tuple[str, str, str]:
    """Resolve stock code to symbol, market, and name."""
    symbol = raw.strip().zfill(6)
    if symbol.startswith("6"):
        market = "SH"
    elif symbol.startswith(("0", "3")):
        market = "SZ"
    elif symbol.startswith(("4", "8")):
        market = "BJ"
    else:
        market = "SZ"
    return symbol, market, f"{symbol}"


async def _run_mock(symbol: str, name: str, output_dir: str | None = None) -> Path:
    """Run full pipeline with mock data."""
    from .collectors.mock import mock_analysis_report, mock_stock_info

    print(f" [Mock] 生成 {name}({symbol}) 的模拟分析报告...")

    stock_info = mock_stock_info(symbol)
    stock_info.name = name

    analysis = mock_analysis_report(symbol, name)

    # Mock collect results
    from .models.social import CollectResult
    collect_results = [
        CollectResult(platform=p, status="success", count=len(
            [x for x in stock_info.social_posts if x.platform == p]
        ), elapsed_ms=50)
        for p in ["雪球", "东方财富股吧", "今日头条", "小红书", "抖音", "微信公众号"]
    ]

    gen = ReportGenerator()
    out = gen.generate(stock_info, analysis, collect_results, output_dir)
    print(f" [Mock] 报告已生成: {out}")
    return out


async def _run_real(symbol: str, name: str, output_dir: str | None = None) -> Path:
    """Run full pipeline with real data collection."""
    from .collectors.mock import mock_social_posts
    from .collectors.quote import QuoteCollector
    from .collectors.eastmoney_guba import EastMoneyGubaCollector
    from .collectors.xueqiu import XueqiuCollector
    from .models.social import CollectResult
    from .models.stock import StockInfo

    settings = get_settings()
    all_posts: list = []
    collect_results: list[CollectResult] = []

    # === Real-time quote (Xueqiu preferred if cookie available, contains PE) ===
    print(f" 采集实时行情...")
    xq = XueqiuCollector()
    xq_quote = await xq.fetch_quote(symbol)
    if xq_quote and xq_quote.pe > 0:
        quote = xq_quote
        print(f"   {quote.name}: {quote.price} ({quote.change_pct:+.2f}%) PE={quote.pe} [来源: 雪球]")
    else:
        qc = QuoteCollector()
        quote = await qc.fetch(symbol, name)
        print(f"   {quote.name}: {quote.price} ({quote.change_pct:+.2f}%) [来源: {quote.source}]")
    if quote.name:
        name = quote.name

    # === Financial data (pysnowball) ===
    print(f" 采集财务数据...")
    from .financial.pysnowball_adapter import PysnowballAdapter
    adapter = PysnowballAdapter()
    financial = await adapter.fetch(symbol)
    if financial.report_period:
        print(f"   报告期: {financial.report_period} | ROE: {financial.roe}% [来源: {financial.source}]")
    else:
        print(f"   财报获取失败，使用空数据。可配置 XUEQIU_TOKEN 启用。")

    # === Social media ===
    print(f" 采集社交媒体舆情...")

    # 雪球 (XueqiuCollector → AgentReach fallback → mock)
    xq = XueqiuCollector()
    xq_result = await xq.collect(symbol, name)
    if xq_result.status == "success":
        all_posts.extend(xq_result.posts)
        print(f"   雪球: {xq_result.count}条 [真实数据]")
    else:
        # Try Agent Reach as fallback
        from .collectors.agent_reach_wrapper import AgentReachWrapper
        if AgentReachWrapper.is_installed():
            ar_posts = AgentReachWrapper.fetch_xueqiu_hot(symbol, name)
            if ar_posts:
                all_posts.extend(ar_posts)
                xq_result = CollectResult(platform="雪球(AgentReach)", status="success", count=len(ar_posts), elapsed_ms=0)
                print(f"   雪球: {len(ar_posts)}条 [AgentReach]")
            else:
                mock = mock_social_posts("雪球", symbol, name)
                all_posts.extend(mock)
                xq_result = CollectResult(platform="雪球", status="success (mock)", count=len(mock), elapsed_ms=100)
                print(f"   雪球: {len(mock)}条 (mock) [需配置XUEQIU_COOKIE]")
        else:
            mock = mock_social_posts("雪球", symbol, name)
            all_posts.extend(mock)
            xq_result = CollectResult(platform="雪球", status="success (mock)", count=len(mock), elapsed_ms=100)
            print(f"   雪球: {len(mock)}条 (mock) [需配置XUEQIU_COOKIE]")
    collect_results.append(xq_result)

    # 东方财富股吧 (Selenium → akshare → mock)
    from .collectors.eastmoney_selenium import SeleniumGubaCollector
    guba = SeleniumGubaCollector(headless=True)
    try:
        guba_result = await guba.collect(symbol, name)
        if guba_result.status == "success" and guba_result.count > 0:
            all_posts.extend(guba_result.posts)
            print(f"   东方财富股吧: {guba_result.count}条 [Selenium]")
            collect_results.append(guba_result)
        else:
            raise RuntimeError(guba_result.error or "empty")
    except Exception:
        # Fallback to akshare
        guba_ak = EastMoneyGubaCollector()
        guba_result_ak = await guba_ak.collect(symbol, name)
        if guba_result_ak.status == "success" and guba_result_ak.count > 0:
            all_posts.extend(guba_result_ak.posts)
            print(f"   东方财富股吧: {guba_result_ak.count}条 [akshare]")
            collect_results.append(guba_result_ak)
        else:
            mock = mock_social_posts("东方财富股吧", symbol, name)
            all_posts.extend(mock)
            print(f"   东方财富股吧: {len(mock)}条 (mock)")
            collect_results.append(CollectResult(platform="东方财富股吧", status="success (mock)", count=len(mock), elapsed_ms=100))
    finally:
        await guba.close()

    # === 巨潮资讯公告 (公司公告) ===
    print(f" 采集巨潮资讯公告...")
    from .collectors.cninfo import CninfoCollector
    cninfo = CninfoCollector()
    cninfo_result = await cninfo.collect(symbol, name)
    if cninfo_result.status == "success" and cninfo_result.count > 0:
        all_posts.extend(cninfo_result.posts)
        print(f"   巨潮资讯: {cninfo_result.count}条 [真实数据]")
    else:
        print(f"   巨潮资讯: 0条 ({cninfo_result.error})")
    collect_results.append(cninfo_result)

    # Other platforms (real with fallback)
    collectors_to_try = [
        ("今日头条", ".collectors.toutiao", "ToutiaoCollector"),
        ("微信公众号", ".collectors.wechat", "WechatCollector"),
        ("小红书", ".collectors.xiaohongshu", "XiaohongshuCollector"),
        ("抖音", ".collectors.douyin", "DouyinCollector"),
    ]

    for p_name, module_path, cls_name in collectors_to_try:
        try:
            import importlib
            mod = importlib.import_module(module_path, "aimoon")
            cls = getattr(mod, cls_name)
            collector = cls()
            result = await collector.collect(symbol, name)
            if result.status == "success" and result.count > 0:
                all_posts.extend(result.posts)
                print(f"   {p_name}: {result.count}条 [真实数据]")
            elif result.status == "skipped":
                # Try Agent Reach for 小红书
                if p_name == "小红书":
                    from .collectors.agent_reach_wrapper import AgentReachWrapper
                    if AgentReachWrapper.is_installed():
                        keyword = f"{name} 股票" if name else f"{symbol} 股票"
                        ar_posts = AgentReachWrapper.fetch_xiaohongshu(keyword)
                        if ar_posts:
                            all_posts.extend(ar_posts)
                            result = CollectResult(platform="小红书(AgentReach)", status="success", count=len(ar_posts), elapsed_ms=0)
                            print(f"   {p_name}: {len(ar_posts)}条 [AgentReach]")
                            collect_results.append(result)
                            continue
                mock = mock_social_posts(p_name, symbol, name)
                all_posts.extend(mock)
                result = CollectResult(platform=p_name, status="success (mock)", count=len(mock), elapsed_ms=100)
                print(f"   {p_name}: {len(mock)}条 (mock) [{result.error}]")
            else:
                mock = mock_social_posts(p_name, symbol, name)
                all_posts.extend(mock)
                result = CollectResult(platform=p_name, status="success (mock)", count=len(mock), elapsed_ms=100)
                print(f"   {p_name}: {len(mock)}条 (mock)")
            collect_results.append(result)
        except Exception:
            mock = mock_social_posts(p_name, symbol, name)
            all_posts.extend(mock)
            collect_results.append(CollectResult(
                platform=p_name, status="success (mock)", count=len(mock), elapsed_ms=100
            ))
            print(f"   {p_name}: {len(mock)}条 (mock)")

    stock_info = StockInfo(
        symbol=symbol, name=name,
        market="SH" if symbol.startswith("6") else "SZ",
        quote=quote, financial=financial,
        social_posts=all_posts,
    )

    # === AI analysis ===
    use_ai = settings.deepseek_api_key and not settings.mock_mode
    if use_ai:
        print(f" DeepSeek AI 分析中...")
    else:
        print(f" AI分析跳过（未配置DEEPSEEK_API_KEY，使用基础报告）")
    analyzer = AIAnalyzer(mock=not use_ai)
    analysis = await analyzer.analyze(stock_info)

    # === Generate report ===
    print(f" 生成 HTML 报告...")
    gen = ReportGenerator()
    out = gen.generate(stock_info, analysis, collect_results, output_dir)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stock AI Analyst - A股AI分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  stock-analyst 000001             分析平安银行
  stock-analyst 600519 --mock      使用模拟数据（无需API Key）
  stock-analyst 000858 -o ./reports  指定输出目录
        """,
    )
    parser.add_argument("symbol", help="A股股票代码，如 000001, 600519")
    parser.add_argument("--mock", action="store_true", help="使用模拟数据模式（无需真实API）")
    parser.add_argument("-o", "--output", help="HTML报告输出目录")
    parser.add_argument("--version", action="version", version="stock-ai-analyst 0.1.0")

    args = parser.parse_args()

    symbol, market, name = _resolve_symbol(args.symbol)
    output_dir = args.output

    # Override mock mode from args
    if args.mock:
        settings = get_settings()
        settings.mock_mode = True

    print(f"\n{'='*60}")
    print(f"  Stock AI Analyst v0.1.0")
    print(f"  股票: {name} ({symbol}.{market})")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式: {'Mock模拟' if (args.mock or get_settings().mock_mode) else '真实数据'}")
    print(f"{'='*60}\n")

    try:
        if args.mock or get_settings().mock_mode:
            out = asyncio.run(_run_mock(symbol, name, output_dir))
        else:
            out = asyncio.run(_run_real(symbol, name, output_dir))

        print(f"\n{'='*60}")
        print(f"  分析完成!")
        print(f"  报告路径: {out}")
        print(f"{'='*60}\n")
    except KeyboardInterrupt:
        print("\n  用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n  运行出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
