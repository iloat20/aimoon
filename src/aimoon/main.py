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

from . import __version__
from .config.settings import get_settings
from .pipeline import PipelineOrchestrator
from .utils import resolve_symbol


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
    parser.add_argument("--version", action="version", version=f"aimoon {__version__}")

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

    parsed_symbol, market, name = resolve_symbol(raw)

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
    print(f"  aimoon v{__version__}")
    print(f"  股票: {name} ({parsed_symbol}.{market})")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式: {mode_label}")
    print(f"{'=' * 60}\n")

    try:
        orchestrator = PipelineOrchestrator(output_dir=args.output)
        if args.mock or settings.mock_mode:
            out = asyncio.run(
                orchestrator.run_mock(parsed_symbol, name)
            )
        else:
            out = asyncio.run(
                orchestrator.run(parsed_symbol, name, skip_ai=args.test)
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
