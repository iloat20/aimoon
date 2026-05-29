"""CLI 入口 — 薄管道"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from aimoon.cache import DataCache
from aimoon.config import Config, load_config
from aimoon.data import get_spot_for_codes, filter_universe, get_sector_context
from aimoon.data.filters import get_holdings_pool
from aimoon.output import OutputFormatter
from aimoon.scoring.rps import compute_rps
from aimoon.screener import screen_universe

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A-share quant screener")
    p.add_argument("--config", type=str, default=None)
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--no-csv", action="store_true")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--refresh", action="store_true")
    sub = p.add_subparsers(dest="command")
    bt = sub.add_parser("backtest")
    bt.add_argument("--stocks", type=str, default="000001")
    bt.add_argument("--hold-days", type=int, default=5)
    cp = sub.add_parser("cache")
    cs = cp.add_subparsers(dest="cache_action")
    cs.add_parser("clear")
    sub.add_parser("update", help="Clear all caches and re-fetch data")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args, path=getattr(args, "config", None))
    fmt = OutputFormatter(cfg)

    # 缓存管理
    if cfg.command == "cache":
        cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
        print(f"Cleared {cache.clear()} cached files")
        return

    # update：清除所有缓存后重新运行
    if cfg.command == "update":
        import shutil
        cache_dir = Path(cfg.cache_dir)
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            print(f"Cleared cache dir: {cache_dir}")
        # 继续执行正常管道（会重新获取所有数据）

    # 回测
    if cfg.command == "backtest":
        from aimoon.backtest import BacktestEngine
        from aimoon.data.history import get_kline
        engine = BacktestEngine(cfg, hold_days=cfg.hold_days)
        cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
        fmt.console.print(f"[bold blue]=== Backtest (hold {cfg.hold_days}d) ===[/bold blue]")
        for code in cfg.stocks.split(","):
            code = code.strip()
            r = get_kline(code, cfg.history_days, cache)
            if r.is_ok():
                result = engine.run(code, code, r.unwrap())
                color = "green" if result.total_return > 0 else "red"
                fmt.console.print(
                    f"  {result.code}: [{color}]{result.total_return:+.2f}%[/{color}] "
                    f"胜率={result.win_rate:.0%} 交易={result.trade_count}次 "
                    f"最大回撤={result.max_drawdown:.2%}"
                )
        return

    # Demo 模式
    if cfg.demo:
        from aimoon.demo import generate_demo
        spot_df, klines = generate_demo()
        cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
        results, tails = screen_universe(spot_df, cfg, cache, klines=klines)
    else:
        # 实时筛选管道 — 持仓池先行，减少行情请求量
        fmt.console.print("[dim]Loading holdings pool (cached)...[/dim]")
        pool = get_holdings_pool(cfg)
        fmt.console.print(f"[dim]Holdings pool: {len(pool)} stocks[/dim]")

        sr = get_spot_for_codes(pool, cfg)
        if sr.is_err():
            fmt.console.print(f"[red]Failed: {sr.error}[/red]")
            fmt.console.print("[yellow]Try: python -m aimoon --demo[/yellow]")
            sys.exit(1)
        spot = sr.unwrap()
        fmt.console.print(f"[dim]Spot data for {len(spot)} stocks[/dim]")

        fmt.console.print("[dim]Filtering universe...[/dim]")
        universe = filter_universe(spot, cfg)

        fmt.console.print("[dim]Building sector context...[/dim]")
        ctx = get_sector_context(spot)

        cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
        fmt.console.print(f"[dim]Analyzing {len(universe)} stocks...[/dim]")
        t0 = time.time()
        results, tails = screen_universe(universe, cfg, cache, ctx)
        fmt.console.print(f"[dim]Done in {time.time() - t0:.1f}s[/dim]")

    # RPS + 排序 + 输出
    results = compute_rps(results, tails)
    top = sorted(results, key=lambda s: s.total_score, reverse=True)[:cfg.top_n]
    fmt.display(top)
    if not cfg.no_csv and top:
        fmt.console.print(f"[dim]Exported: {fmt.export_csv(top)}[/dim]")
        fmt.console.print(f"[dim]Exported: {fmt.export_markdown(top)}[/dim]")


if __name__ == "__main__":
    main()
