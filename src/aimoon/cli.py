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

    # backtest
    bt = sub.add_parser("backtest")
    bt.add_argument("--stocks", type=str, default="000001")
    bt.add_argument("--hold-days", type=int, default=20)
    bt.add_argument("--max-positions", type=int, default=2)
    bt.add_argument("--commission", type=float, default=0.0003)
    bt.add_argument("--slippage", type=float, default=0.001)
    bt.add_argument("--stamp-tax", type=float, default=0.0005)
    bt.add_argument("--top", type=int, default=10)

    # evaluate
    ev = sub.add_parser("evaluate")
    ev.add_argument("--stocks", type=str, required=True, help="Comma-separated stock codes")
    ev.add_argument("--forward-days", type=int, default=5)
    ev.add_argument("--eval-days", type=int, default=60)

    # cache
    cp = sub.add_parser("cache")
    cs = cp.add_subparsers(dest="cache_action")
    cs.add_parser("clear")
    sub.add_parser("update", help="Clear all caches and re-fetch data")
    return p.parse_args()


def _run_evaluate(args: argparse.Namespace, cfg: Config, fmt: OutputFormatter) -> None:
    """因子评估子命令。"""
    from aimoon.data.history import get_kline
    from aimoon.factor_eval import evaluate_all_scorers

    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
    codes = [c.strip() for c in args.stocks.split(",")]
    klines: dict = {}

    fmt.console.print(f"[dim]Loading klines for {len(codes)} stocks...[/dim]")
    for code in codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()

    if not klines:
        fmt.console.print("[red]No valid stock data.[/red]")
        return

    fmt.console.print(f"[dim]Evaluating factors ({args.eval_days} days, forward={args.forward_days}d)...[/dim]")
    t0 = time.time()
    evals = evaluate_all_scorers(klines, args.forward_days, args.eval_days)
    fmt.console.print(f"[dim]Done in {time.time() - t0:.1f}s[/dim]")

    if not evals:
        fmt.console.print("[yellow]Not enough data for factor evaluation.[/yellow]")
        return

    fmt.display_factor_eval(evals)


def _run_backtest(args: argparse.Namespace, cfg: Config, fmt: OutputFormatter) -> None:
    """回测子命令。"""
    from aimoon.backtest import BacktestEngine
    from aimoon.data.history import get_kline

    max_pos = getattr(args, "max_positions", 10)
    commission = getattr(args, "commission", 0.0003)
    slippage = getattr(args, "slippage", 0.001)
    stamp_tax = getattr(args, "stamp_tax", 0.0005)
    bt_top = getattr(args, "top", 10)
    codes = [c.strip() for c in cfg.stocks.split(",")]

    engine = BacktestEngine(
        hold_days=cfg.hold_days,
        max_positions=max_pos,
        commission=commission,
        slippage=slippage,
        stamp_tax=stamp_tax,
    )
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    if len(codes) > 1:
        # 多股 → 组合回测
        fmt.console.print(f"[bold blue]=== Portfolio Backtest (hold {cfg.hold_days}d, top {bt_top}) ===[/bold blue]")
        klines: dict = {}
        names: dict = {}
        for code in codes:
            r = get_kline(code, cfg.history_days, cache)
            if r.is_ok():
                klines[code] = r.unwrap()
                names[code] = code

        if not klines:
            fmt.console.print("[red]No valid stock data.[/red]")
            return

        result = engine.run_portfolio(klines, names)
        fmt.display_portfolio_backtest(result)
    else:
        # 单股回测（兼容）
        code = codes[0]
        fmt.console.print(f"[bold blue]=== Backtest: {code} (hold {cfg.hold_days}d) ===[/bold blue]")
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            result = engine.run_single(code, code, r.unwrap())
            color = "green" if result.total_return > 0 else "red"
            fmt.console.print(
                f"  {result.code}: [{color}]{result.total_return:+.2f}%[/{color}] "
                f"胜率={result.win_rate:.0%} 交易={result.trade_count}次 "
                f"最大回撤={result.max_drawdown:.2%}"
            )
        else:
            fmt.console.print(f"[red]Failed to load kline for {code}[/red]")


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

    # 因子评估
    if cfg.command == "evaluate":
        _run_evaluate(args, cfg, fmt)
        return

    # 回测
    if cfg.command == "backtest":
        _run_backtest(args, cfg, fmt)
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
