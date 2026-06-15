"""CLI entry -- thin pipeline."""
from __future__ import annotations

import warnings

# Suppress pkg_resources deprecation warning from third-party libs (py_mini_racer etc.)
# Must be set before any import that transitively imports pkg_resources
warnings.filterwarnings("ignore", message=".*pkg_resources.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="pkg_resources")

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import pandas as pd

# Windows 终端 UTF-8 支持 — 必须在 Rich 导入之前设置
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.system("")  # 激活 VT100 控制序列（ANSI 颜色/Unicode 框线）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

from aimoon.cache import DataCache
from aimoon.config import Config, load_config
from aimoon.data import get_spot_for_codes, filter_universe
from aimoon.data.holdings_pool import get_holdings_pool
from aimoon.data.spot import get_spot
from aimoon.output import OutputFormatter
from aimoon.scoring.rps import compute_rps
from aimoon.scoring import hybrid_score
from aimoon.screener import screen_universe

logging.basicConfig(level=logging.WARNING)
logging.getLogger("aimoon.factors.registry").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)



def _check_network(timeout: int = 5) -> bool:
    """Quick network connectivity check. Returns True if reachable."""
    import httpx
    try:
        r = httpx.get(
            "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
            params={"fltt": "2", "invt": "2", "fields": "f2,f3,f12,f14", "secids": "0.399001"},
            timeout=timeout,
        )
        data = r.json()
        diff = data.get("data", {}).get("diff", [])
        return len(diff) > 0
    except Exception:
        return False

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A-share quant screener")
    p.add_argument("--config", type=str, default=None)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--workers", type=int, default=20)
    p.add_argument("--no-csv", action="store_true")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--reversal", action="store_true", help="启用中期反转因子")
    p.add_argument("--no-alpha", action="store_true", default=None, help="禁用 Alpha Zoo 截面因子（默认启用）")
    sub = p.add_subparsers(dest="command")

    # backtest
    bt = sub.add_parser("backtest")
    bt.add_argument("--stocks", type=str, default="000001")
    bt.add_argument("--hold-days", type=int, default=10)
    bt.add_argument("--max-positions", type=int, default=5)
    bt.add_argument("--commission", type=float, default=0.0003)
    bt.add_argument("--slippage", type=float, default=0.001)
    bt.add_argument("--stamp-tax", type=float, default=0.0005)
    bt.add_argument("--top", type=int, default=10)
    bt.add_argument("--stop-loss", type=float, default=0.05)
    bt.add_argument("--take-profit", type=float, default=0.20)
    bt.add_argument("--benchmark", type=str, default="000300")
    bt.add_argument("--walk-forward", action="store_true")

    # optimize
    opt = sub.add_parser("optimize")
    opt.add_argument("--params", type=str, default="stop_loss_pct,hold_days")
    opt.add_argument("--metric", type=str, default="sharpe", choices=["sharpe", "sortino", "return"])
    opt.add_argument("--trials", type=int, default=50)

    # schedule
    sched = sub.add_parser("schedule")
    sched.add_argument("--time", type=str, default="09:30")
    sched.add_argument("--output-dir", type=str, default="daily_picks/")
    sched.add_argument("--notify", action="store_true")

    # evaluate
    ev = sub.add_parser("evaluate")
    ev.add_argument("--stocks", type=str, required=True, help="Comma-separated stock codes")
    ev.add_argument("--forward-days", type=int, default=5)
    ev.add_argument("--eval-days", type=int, default=60)

    # cache
    cp = sub.add_parser("cache")
    cs = cp.add_subparsers(dest="cache_action", required=True)
    cs.add_parser("clear")
    sub.add_parser("update", help="Clear all caches and re-fetch data")
    sub.add_parser("refresh-pool", help="Force refresh institutional holdings pool")

    # watchlist
    wl = sub.add_parser("watchlist")
    wl_sub = wl.add_subparsers(dest="watchlist_action", required=True)
    wl_add = wl_sub.add_parser("add")
    wl_add.add_argument("codes", type=str, help="逗号分隔的股票代码")
    wl_rm = wl_sub.add_parser("remove")
    wl_rm.add_argument("codes", type=str, help="逗号分隔的股票代码")
    wl_sub.add_parser("list")
    wl_sub.add_parser("clear")
    return p.parse_args()


def _run_evaluate(args: argparse.Namespace, cfg: Config, fmt: OutputFormatter) -> None:
    """Factor evaluation sub-command."""
    from aimoon.data.history import get_kline
    from aimoon.factor_eval import evaluate_all_scorers

    cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
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
    """Backtest sub-command -- 先筛选 top 股票，再做增强组合回测。"""
    from aimoon.data.history import get_kline
    from aimoon.enhanced_backtest import EnhancedBacktestEngine

    if not _check_network():
        fmt.console.print("[red]Network required for backtest. Check connection or try --demo.[/red]")
        sys.exit(1)

    max_pos = getattr(args, "max_positions", cfg.max_positions)
    commission = getattr(args, "commission", 0.0003)
    slippage = getattr(args, "slippage", 0.001)
    stamp_tax = getattr(args, "stamp_tax", 0.0005)
    bt_top = getattr(args, "top", 10)
    stop_loss = cfg.stop_loss_pct
    take_profit = cfg.take_profit_pct
    benchmark_code = cfg.benchmark_code

    # Step 1: Run screening pipeline to get top stocks
    fmt.console.print(f"[bold blue]=== Backtest: top {bt_top}, hold {cfg.hold_days}d, SL {stop_loss:.0%}, TP {take_profit:.0%} ===[/bold blue]")
    universe = _load_screening_data(cfg, fmt)
    cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)

    fmt.console.print(f"[dim]Scoring {len(universe)} stocks...[/dim]")
    t0 = time.time()
    results, tails, all_klines = screen_universe(universe, cfg, cache)
    fmt.console.print(f"[dim]Scored in {time.time() - t0:.1f}s[/dim]")

    if not results:
        fmt.console.print("[red]No stocks passed screening.[/red]")
        return

    from aimoon.scoring.rps import compute_rps
    results = compute_rps(results, tails)
    top_stocks = sorted(results, key=lambda s: hybrid_score(list(s.signals)), reverse=True)[:bt_top]
    codes = [s.code for s in top_stocks]
    names = {s.code: s.name for s in top_stocks}
    stock_list = ", ".join(f"{s.code}({s.name})" for s in top_stocks)
    fmt.console.print(f"[dim]Top {len(top_stocks)} stocks: {stock_list}[/dim]")

    # Step 2: Fetch klines + benchmark
    klines: dict[str, pd.DataFrame] = {}
    for code in codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()

    if benchmark_code:
        br = get_kline(benchmark_code, cfg.history_days, cache)
        if br.is_ok():
            klines[benchmark_code] = br.unwrap()

    if not klines:
        fmt.console.print("[red]No valid kline data.[/red]")
        return

    # Walk-forward branch
    if getattr(args, "walk_forward", False):
        from aimoon.optimizer import walk_forward_validate
        wf = walk_forward_validate(klines, names, cfg, cache,
                                   train_pct=cfg.train_pct, n_splits=cfg.n_splits)
        fmt.display_walk_forward(wf)
        return

    # Step 3: Enhanced portfolio backtest — momentum-driven, full data range
    engine = EnhancedBacktestEngine(
        hold_days=5,
        max_positions=max_pos,
        commission=commission,
        slippage=slippage,
        stamp_tax=stamp_tax,
        stop_loss_pct=stop_loss,
        take_profit_pct=take_profit,
        benchmark_code=benchmark_code,
        entry_threshold=50,
        max_sector_pct=cfg.max_sector_pct,
        use_reversal=cfg.use_reversal,
        use_alpha=cfg.use_alpha,
        use_kelly=False,
        exit_ratio=0.5,
    )
    result = engine.run_portfolio(klines, names)
    fmt.display_enhanced_backtest(result)

    # Charts (optional matplotlib)
    try:
        from aimoon.charts import plot_equity_curve, plot_drawdown, plot_monthly_returns
        os.makedirs(cfg.output_dir, exist_ok=True)
        eq_path = os.path.join(cfg.output_dir, "equity_curve.png")
        dd_path = os.path.join(cfg.output_dir, "drawdown.png")
        mr_path = os.path.join(cfg.output_dir, "monthly_returns.png")
        plot_equity_curve(result.equity_curve, title="Portfolio Equity Curve", filepath=eq_path)
        plot_drawdown(result.drawdown_curve, filepath=dd_path)
        plot_monthly_returns(result.trades, filepath=mr_path)
        fmt.console.print(f"[dim]Charts: {eq_path}, {dd_path}, {mr_path}[/dim]")
    except ImportError:
        pass

    # Backtest report
    report_path = fmt.export_backtest_report(result, top_stocks, cfg)
    fmt.console.print(f"[dim]Report: {report_path}[/dim]")


def _load_screening_data(
    cfg: Config, fmt: OutputFormatter,
) -> pd.DataFrame:
    """Load spot data for screening. Returns spot_df.

    Strategy:
    1. Try institutional holdings pool (northbound + fund + ROE).
    2. If pool is empty, fall back to full market spot data.
    """
    fmt.console.print("[dim]Loading holdings pool (cached)...[/dim]")
    pool = get_holdings_pool(cfg, cache_dir=Path(cfg.cache_dir))
    fmt.console.print(f"[dim]Holdings pool: {len(pool)} stocks[/dim]")

    if pool:
        sr = get_spot_for_codes(pool, cfg)
        if sr.is_ok():
            spot = sr.unwrap()
            fmt.console.print(f"[dim]Spot data for {len(spot)} stocks[/dim]")
            universe = filter_universe(spot, cfg)
            fmt.console.print(f"[dim]Universe after filter: {len(universe)} stocks[/dim]")
            if len(universe) >= 5:
                return universe

    # Fallback: full market
    fmt.console.print("[yellow]Holdings pool unavailable, loading full market...[/yellow]")
    full = get_spot(cfg, cache_dir=Path(cfg.cache_dir))
    if full.is_err():
        fmt.console.print(f"[red]Failed: {full.error}[/red]")
        fmt.console.print("[yellow]Try: python -m aimoon --demo[/yellow]")
        sys.exit(1)
    spot = full.unwrap()
    fmt.console.print(f"[dim]Full market: {len(spot)} stocks[/dim]")

    universe = filter_universe(spot, cfg)
    fmt.console.print(f"[dim]Universe after filter: {len(universe)} stocks[/dim]")

    if universe.empty:
        fmt.console.print("[red]No stocks pass the filter. Try relaxing parameters.[/red]")
        fmt.console.print("[yellow]Try: python -m aimoon --demo[/yellow]")
        sys.exit(1)

    return universe


def _run_optimize(args: argparse.Namespace, cfg: Config, fmt: OutputFormatter) -> None:
    """Parameter optimization sub-command."""
    from aimoon.data.history import get_kline
    from aimoon.optimizer import grid_search, _PARAM_RANGES

    bt_top = getattr(args, "top", cfg.top_n)
    metric = getattr(args, "metric", "sharpe")
    max_trials = getattr(args, "trials", 50)
    param_names = getattr(args, "params", "stop_loss_pct,hold_days").split(",")
    param_ranges = {k: v for k, v in _PARAM_RANGES.items() if k in param_names}

    fmt.console.print(f"[bold blue]=== Optimize: metric={metric}, trials={max_trials}, params={list(param_ranges.keys())} ===[/bold blue]")

    universe = _load_screening_data(cfg, fmt)
    cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)

    fmt.console.print(f"[dim]Scoring {len(universe)} stocks...[/dim]")
    results, tails, all_klines = screen_universe(universe, cfg, cache)
    if not results:
        fmt.console.print("[red]No stocks passed screening.[/red]")
        return

    from aimoon.scoring.rps import compute_rps
    results = compute_rps(results, tails)
    top_stocks = sorted(results, key=lambda s: hybrid_score(list(s.signals)), reverse=True)[:bt_top]
    codes = [s.code for s in top_stocks]
    names = {s.code: s.name for s in top_stocks}

    klines: dict[str, pd.DataFrame] = {}
    for code in codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()

    if not klines:
        fmt.console.print("[red]No valid kline data.[/red]")
        return

    fmt.console.print(f"[dim]Running grid search ({max_trials} trials)...[/dim]")
    t0 = time.time()
    opt_results = grid_search(klines, names, cfg, cache,
                              param_ranges=param_ranges, metric=metric,
                              max_trials=max_trials)
    fmt.console.print(f"[dim]Done in {time.time() - t0:.1f}s[/dim]")
    fmt.display_optimize(opt_results)


def _run_schedule(args: argparse.Namespace, cfg: Config, fmt: OutputFormatter) -> None:
    """Daily scheduled screening using sched module."""
    import datetime as _dt
    import sched

    target_time = getattr(args, "time", "09:30")
    output_dir = getattr(args, "output_dir", "daily_picks/")
    notify = getattr(args, "notify", False)
    scheduler = sched.scheduler(time.time, time.sleep)

    fmt.console.print(f"[bold]Scheduling daily screen at {target_time}[/bold]")
    fmt.console.print("[dim]Press Ctrl+C to stop[/dim]")

    def _run_once() -> None:
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            if cfg.demo:
                from aimoon.demo import generate_demo
                spot_df, klines = generate_demo()
                cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
                results, tails, all_klines = screen_universe(spot_df, cfg, cache, klines=klines)
            else:
                universe = _load_screening_data(cfg, fmt)
                cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
                results, tails, all_klines = screen_universe(universe, cfg, cache)

            from aimoon.scoring.rps import compute_rps
            results = compute_rps(results, tails)
            top = sorted(results, key=lambda s: hybrid_score(list(s.signals)), reverse=True)[:cfg.top_n]

            os.makedirs(output_dir, exist_ok=True)
            saved = fmt.export_csv(top, filename=f"aimoon_{ts}.csv")
            fmt.console.print(f"[green]Screen completed {ts}: {len(top)} stocks -> {saved}[/green]")

            if notify:
                summary = ", ".join(f"{s.name}({s.total_score})" for s in top[:5])
                fmt.console.print(f"[dim]Top 5: {summary}[/dim]")

            h, m = map(int, target_time.split(":"))
            tomorrow = _dt.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
            if _dt.datetime.now() >= tomorrow:
                tomorrow += _dt.timedelta(days=1)
            wait = (tomorrow - _dt.datetime.now()).total_seconds()
            scheduler.enter(wait, 1, _run_once)
        except Exception as e:
            logger.error("Scheduled screen failed: %s", e)
            scheduler.enter(300, 1, _run_once)  # retry in 5 min

    now = _dt.datetime.now()
    h, m = map(int, target_time.split(":"))
    first_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now >= first_run:
        first_run += _dt.timedelta(days=1)
    initial_wait = (first_run - now).total_seconds()
    scheduler.enter(initial_wait, 1, _run_once)

    try:
        scheduler.run()
    except KeyboardInterrupt:
        fmt.console.print("[yellow]Schedule stopped.[/yellow]")


def main() -> None:
    args = parse_args()
    cfg = load_config(args, path=getattr(args, "config", None))
    fmt = OutputFormatter(cfg)

    # Cache management
    if cfg.command == "cache":
        cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
        print(f"Cleared {cache.clear()} cached files")
        return

    # Update: clear all caches then re-run
    if cfg.command == "update":
        import shutil
        cache_dir = Path(cfg.cache_dir)
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            print(f"Cleared cache dir: {cache_dir}")

    # Refresh holdings pool
    if cfg.command == "refresh-pool":
        from aimoon.data.holdings_pool import get_holdings_pool, save_shipped_pool
        fmt.console.print("[bold blue]=== Refreshing holdings pool ===[/bold blue]")
        fmt.console.print("[dim]Fetching northbound + fund + ROE data...[/dim]")
        pool = get_holdings_pool(cfg, force=True, cache_dir=Path(cfg.cache_dir))
        if pool:
            save_shipped_pool(pool)
            fmt.console.print(f"[green]Holdings pool refreshed: {len(pool)} stocks[/green]")
        else:
            fmt.console.print("[red]Failed to refresh pool (network error?)[/red]")
        return

    # Watchlist management
    if cfg.command == "watchlist":
        from aimoon.watchlist import add_watchlist, remove_watchlist, list_watchlist, clear_watchlist
        action = cfg.watchlist_action
        if action == "add":
            codes = [c.strip() for c in cfg.watchlist_codes.split(",") if c.strip()]
            ok, msg = add_watchlist(codes)
            print(msg)
            if not ok:
                sys.exit(1)
        elif action == "remove":
            codes = [c.strip() for c in cfg.watchlist_codes.split(",") if c.strip()]
            ok, msg = remove_watchlist(codes)
            print(msg)
            if not ok:
                sys.exit(1)
        elif action == "list":
            ok, result = list_watchlist()
            if ok:
                if result:
                    print(f"Watchlist ({len(result)} stocks): {', '.join(result)}")
                else:
                    print("Watchlist is empty")
            else:
                print(result)
                sys.exit(1)
        elif action == "clear":
            ok, msg = clear_watchlist()
            print(msg)
            if not ok:
                sys.exit(1)
        return

    # Factor evaluation
    if cfg.command == "evaluate":
        _run_evaluate(args, cfg, fmt)
        return

    # Backtest
    if cfg.command == "backtest":
        _run_backtest(args, cfg, fmt)
        return

    # Optimize
    if cfg.command == "optimize":
        _run_optimize(args, cfg, fmt)
        return

    # Schedule
    if cfg.command == "schedule":
        _run_schedule(args, cfg, fmt)
        return

    # Demo mode
    if cfg.demo:
        from aimoon.demo import generate_demo
        spot_df, klines = generate_demo()
        cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
        results, tails, all_klines = screen_universe(spot_df, cfg, cache, klines=klines)
    else:
        # Real screening pipeline with automatic fallback
        universe = _load_screening_data(cfg, fmt)
        cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
        fmt.console.print(f"[dim]Analyzing {len(universe)} stocks...[/dim]")
        t0 = time.time()
        results, tails, all_klines = screen_universe(universe, cfg, cache)
        fmt.console.print(f"[dim]Done in {time.time() - t0:.1f}s[/dim]")

    # RPS + sort + output
    results = compute_rps(results, tails)
    regime = None
    if cfg.command is None and not cfg.demo:
        from aimoon.regime_enhanced import detect_regime
        from aimoon.scoring.adaptive_weight import apply_regime_to_list
        from aimoon.data.history import get_kline as _get_kline
        _c = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
        bench_code = '000001'
        br = _get_kline(bench_code, cfg.history_days, _c)
        if br.is_ok():
            regime = detect_regime(br.unwrap())
            fmt.console.print(f'[dim]Market regime: {regime}[/dim]')
            results = apply_regime_to_list(results, regime)

    # ── Super Turtle 策略信号 ──
    from aimoon.scoring.turtle import generate_turtle_plan
    turtle_plans: dict = {}
    for r in results:
        kdf = all_klines.get(r.code)
        if kdf is not None and len(kdf) >= 25:
            plan = generate_turtle_plan(kdf, r.code, r.name)
            if plan is not None:
                turtle_plans[r.code] = plan

    # Risk warnings
    from aimoon.risk import RiskLimits, Position, PortfolioState, check_risk_limits
    limits = RiskLimits(
        max_position_pct=cfg.max_position_pct,
        max_sector_pct=cfg.max_sector_pct,
        max_drawdown_limit=cfg.max_drawdown_limit,
        target_volatility=cfg.target_volatility,
    )
    ps = PortfolioState()
    weight_each = 1.0 / max(len(results), 1)
    for r in results[:cfg.top_n]:
        ps.positions[r.code] = Position(
            code=r.code, name=r.name, weight=weight_each,
            entry_price=r.price, sector="",
        )
    violations = check_risk_limits(ps, limits)
    if violations:
        fmt.console.print(f"[yellow]Risk warnings ({len(violations)}):[/yellow]")
        for v in violations:
            fmt.console.print(f"  [dim]{v[0]}: {v[1:]}[/dim]")

    top = sorted(results, key=lambda s: hybrid_score(list(s.signals)), reverse=True)[:cfg.top_n]
    fmt.display(top, turtle_plans=turtle_plans)
    if turtle_plans:
        fmt.display_turtle_plans(turtle_plans)
    if not cfg.no_csv and top:
        fmt.console.print(f"[dim]Exported: {fmt.export_csv(top)}[/dim]")
        regime_str = str(regime) if regime else None
        fmt.console.print(f"[dim]Exported: {fmt.export_markdown(top, regime=regime_str)}[/dim]")


if __name__ == "__main__":
    main()