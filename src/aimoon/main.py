"""aimoon - CLI entry point.

Usage:
    aimoon 000001                 # Real data + AI analysis
    aimoon 600519 --mock          # Mock mode (no API needed)
    aimoon 600519 --test          # Real data, skip AI (testing)
    aimoon test 600519            # Same as --test (convenience)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from .ai.analyzer import AIAnalyzer
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


async def _run_mock(
    symbol: str, name: str, market: str = "SZ", output_dir: str | None = None
) -> Path:
    """Run full pipeline with mock data."""
    from .collectors.mock import mock_analysis_report, mock_stock_info

    print(f" [Mock] 生成 {name}({symbol}) 的模拟分析报告...")

    stock_info = mock_stock_info(symbol)
    stock_info.name = name

    analysis = mock_analysis_report(symbol, name)

    # Mock collect results
    from .models.social import CollectResult

    collect_results = [
        CollectResult(
            platform=p,
            status="success",
            count=len([x for x in stock_info.social_posts if x.platform == p]),
            elapsed_ms=50,
        )
        for p in ["雪球", "东方财富股吧", "今日头条", "微信公众号"]
    ]

    gen = ReportGenerator()
    out = gen.generate(stock_info, analysis, collect_results, output_dir)
    print(f" [Mock] 报告已生成: {out}")
    return out


async def _run_real(
    symbol: str,
    name: str,
    market: str = "SZ",
    output_dir: str | None = None,
    *,
    skip_ai: bool = False,
) -> Path:
    """Run full pipeline with real data collection."""
    from .collectors.eastmoney_guba import EastMoneyGubaCollector
    from .collectors.mock import mock_social_posts
    from .collectors.quote import QuoteCollector
    from .collectors.xueqiu import XueqiuCollector
    from .models.social import CollectResult

    settings = get_settings()
    all_posts: list = []
    collect_results: list[CollectResult] = []

    # === Real-time quote (Xueqiu preferred if cookie available, contains PE) ===
    print(" 采集实时行情...")
    xq = XueqiuCollector()
    xq_quote = await xq.fetch_quote(symbol)
    if xq_quote and xq_quote.pe > 0:
        quote = xq_quote
        print(
            f"   {quote.name}: {quote.price}"
            f" ({quote.change_pct:+.2f}%)"
            f" PE={quote.pe} [来源: 雪球]"
        )
    else:
        qc = QuoteCollector()
        quote = await qc.fetch(symbol, name)
        print(
            f"   {quote.name}: {quote.price}"
            f" ({quote.change_pct:+.2f}%)"
            f" [来源: {quote.source}]"
        )
    if quote.name:
        name = quote.name

    # === Financial data (pysnowball) ===
    print(" 采集财务数据...")
    from .financial.pysnowball_adapter import PysnowballAdapter

    adapter = PysnowballAdapter()
    financial = await adapter.fetch(symbol)
    if financial.report_period:
        print(
            f"   报告期: {financial.report_period} | ROE: {financial.roe}%"
            f" [来源: {financial.source}]"
        )
    else:
        print("   财报获取失败，使用空数据。可配置 XUEQIU_TOKEN 启用。")

    # === K-line history (for technical analysis) ===
    print(" 采集K线历史...")
    from .collectors.kline import KlineCollector

    kline = await KlineCollector().fetch(symbol)
    if kline.bars:
        print(f"   K线: {len(kline.bars)}根 [{kline.source}]")
    else:
        print("   K线获取失败，技术分析将使用基础数据。")

    # === Capital flow (资金面) ===
    print(" 采集资金流向...")
    from .collectors.fund_flow import FundFlowCollector

    capital_flow = await FundFlowCollector().fetch(symbol)
    if capital_flow.source != "all_failed":
        print(
            f"   主力5日: {capital_flow.main_net_5d / 1e8:.2f}亿"
            f" [{capital_flow.source}]"
        )
    else:
        print("   资金面数据获取失败。")

    # === Research reports (研报) ===
    print(" 采集机构研报...")
    from .collectors.research_report import ResearchReportCollector

    research = await ResearchReportCollector().fetch(symbol)
    if research.total_count > 0:
        print(f"   研报: {research.total_count}条 [来源: {research.source}]")
    else:
        print("   研报数据获取失败。")

    # === Social media ===
    print(" 采集社交媒体舆情...")

    # 东方财富股吧 (Playwright → akshare → mock)
    from .collectors.eastmoney_playwright import GubaCollector

    guba = GubaCollector()
    try:
        guba_result = await guba.collect(symbol, name)
        if guba_result.status == "success" and guba_result.count > 0:
            all_posts.extend(guba_result.posts)
            print(f"   东方财富股吧: {guba_result.count}条 [Playwright]")
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
            collect_results.append(
                CollectResult(
                    platform="东方财富股吧",
                    status="success (mock)",
                    count=len(mock),
                    elapsed_ms=100,
                )
            )

    # === 巨潮资讯公告 (公司公告) ===
    print(" 采集巨潮资讯公告...")
    from .collectors.cninfo import CninfoCollector

    cninfo = CninfoCollector()
    cninfo_result = await cninfo.collect(symbol, name)
    if cninfo_result.status == "success" and cninfo_result.count > 0:
        all_posts.extend(cninfo_result.posts)
        print(f"   巨潮资讯: {cninfo_result.count}条 [真实数据]")
    else:
        print(f"   巨潮资讯: 0条 ({cninfo_result.error})")

    # === 最新财务报告 (年报/半年报/季报, 缓存30天) ===
    print(" 获取最新财务报告...")
    from .financial.annual_report import fetch_reports

    reports = await fetch_reports(symbol)
    collect_results.append(cninfo_result)

    # Other platforms (real with mock fallback)
    collectors_to_try = [
        ("今日头条", ".collectors.toutiao", "ToutiaoCollector"),
        ("微信公众号", ".collectors.wechat", "WechatCollector"),
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
                mock = mock_social_posts(p_name, symbol, name)
                all_posts.extend(mock)
                result = CollectResult(
                    platform=p_name,
                    status="success (mock)",
                    count=len(mock),
                    elapsed_ms=100,
                )
                print(f"   {p_name}: {len(mock)}条 (mock) [{result.error}]")
            else:
                mock = mock_social_posts(p_name, symbol, name)
                all_posts.extend(mock)
                result = CollectResult(
                    platform=p_name,
                    status="success (mock)",
                    count=len(mock),
                    elapsed_ms=100,
                )
                print(f"   {p_name}: {len(mock)}条 (mock)")
            collect_results.append(result)
        except Exception:
            mock = mock_social_posts(p_name, symbol, name)
            all_posts.extend(mock)
            collect_results.append(
                CollectResult(
                    platform=p_name,
                    status="success (mock)",
                    count=len(mock),
                    elapsed_ms=100,
                )
            )
            print(f"   {p_name}: {len(mock)}条 (mock)")

    from .models.stock import FinancialReportData

    stock_info = StockInfo(
        symbol=symbol,
        name=name,
        market=market,
        quote=quote,
        financial=financial,
        kline=kline,
        capital_flow=capital_flow,
        social_posts=all_posts,
        research=research,
        annual_report=FinancialReportData(**(reports.get("annual") or {})),
        semi_annual_report=FinancialReportData(**(reports.get("semi_annual") or {})),
        quarterly_report=FinancialReportData(**(reports.get("quarterly") or {})),
    )

    # === Data integrity validation ===
    from .validation.integrity_checker import check_data_integrity

    data_warnings, data_confidence = check_data_integrity(stock_info)
    if data_warnings:
        print(" 数据质量校验:")
        for w in data_warnings:
            print(f"   ⚠ {w}")
    else:
        print(" 数据质量校验: 通过")

    # === AI analysis ===
    use_ai = settings.deepseek_api_key and not settings.mock_mode and not skip_ai
    if use_ai:
        print(" DeepSeek AI 分析中...")
    elif skip_ai:
        print(" === 测试模式：跳过AI分析 ===")
    else:
        print(" AI分析跳过（未配置DEEPSEEK_API_KEY，使用基础报告）")
    analyzer = AIAnalyzer(mock=not use_ai)
    analysis = await analyzer.analyze(stock_info)

    # Attach data quality info
    analysis.data_warnings = data_warnings
    analysis.data_confidence = data_confidence

    # === Generate report ===
    print(" 生成 HTML 报告...")
    gen = ReportGenerator()
    out = gen.generate(stock_info, analysis, collect_results, output_dir)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="aimoon - A股AI分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  aimoon 000001             分析平安银行（真实数据+AI）
  aimoon 600519 --mock      使用全模拟数据（无需API Key）
  aimoon 600519 --test      测试模式：采集真实数据但跳过AI分析
  aimoon 000858 -o ./reports  指定输出目录
        """,
    )
    parser.add_argument("symbol", nargs="*", help="A股股票代码，如 000001, 600519")
    parser.add_argument(
        "--mock", action="store_true", help="使用模拟数据模式（无需真实API）"
    )
    parser.add_argument(
        "--test", action="store_true", help="测试模式：采集真实数据但跳过AI分析"
    )
    parser.add_argument("-o", "--output", help="HTML报告输出目录")
    parser.add_argument("--version", action="version", version="aimoon 0.3.2")

    args = parser.parse_args()
    raw_args: list[str] = args.symbol or []

    # Support: aimoon test <symbol>
    is_test_cmd = len(raw_args) >= 2 and raw_args[0] == "test"
    if is_test_cmd:
        args.test = True
        raw = raw_args[1]
    elif raw_args:
        raw = raw_args[0]
    else:
        raw = ""

    if not raw:
        parser.print_help()
        print("\n错误: 请提供股票代码")
        sys.exit(1)

    parsed_symbol, market, name = _resolve_symbol(raw)
    output_dir = args.output

    # Override mock/test mode from args
    settings = get_settings()
    if args.mock:
        settings.mock_mode = True
    elif args.test:
        settings.mock_mode = False

    mode_label = (
        "Mock模拟"
        if (args.mock or settings.mock_mode)
        else "测试(无AI)" if args.test else "真实数据"
    )
    print(f"\n{'=' * 60}")
    print("  aimoon v0.3.2")
    print(f"  股票: {name} ({parsed_symbol}.{market})")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式: {mode_label}")
    print(f"{'=' * 60}\n")

    try:
        if args.mock or get_settings().mock_mode:
            out = asyncio.run(_run_mock(parsed_symbol, name, market, output_dir))
        else:
            out = asyncio.run(
                _run_real(parsed_symbol, name, market, output_dir, skip_ai=args.test)
            )

        print(f"\n{'=' * 60}")
        print("  分析完成!")
        print(f"  报告路径: {out}")
        print(f"{'=' * 60}\n")
    except KeyboardInterrupt:
        print("\n  用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n  运行出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
