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
from aimoon.data import filter_universe, get_spot_for_codes
from aimoon.data.holdings_pool import get_holdings_pool
from aimoon.ml.predictor import MLPredictor
from aimoon.output import OutputFormatter
from aimoon.scoring.rps import compute_rps
from aimoon.screener import screen_universe

logging.basicConfig(level=logging.WARNING)
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
    sub = p.add_subparsers(dest="command")

    # backtest
    bt = sub.add_parser("backtest")
    bt.add_argument("--stocks", type=str, default="000001")
    bt.add_argument("--hold-days", type=int, default=12)
    bt.add_argument("--max-positions", type=int, default=4)
    bt.add_argument("--top", type=int, default=10)
    bt.add_argument("--stop-loss", type=float, default=0.04)
    bt.add_argument("--take-profit", type=float, default=0.14)
    bt.add_argument("--benchmark", type=str, default="000300")

    # schedule
    sched = sub.add_parser("schedule")
    sched.add_argument("--time", type=str, default="09:30")
    sched.add_argument("--output-dir", type=str, default="daily_picks/")
    sched.add_argument("--notify", action="store_true")

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

    # train-model
    tm = sub.add_parser("train-model", help="Train LightGBM model")
    tm.add_argument("--force", action="store_true", help="Force full retrain")
    tm.add_argument(
        "--n-dates",
        type=int,
        default=120,
        help="Number of historical dates for training (default 120)",
    )
    tm.add_argument(
        "--forward-days",
        type=int,
        default=5,
        help="Forward return horizon for labels (5=weekly, default 5)",
    )

    return p.parse_args()


def _run_train_model(
    args: argparse.Namespace,
    cfg: Config,
    fmt: OutputFormatter,
) -> None:
    """Handle `aimoon train-model` subcommand — single LightGBM training."""
    from aimoon.data.history import get_kline
    from aimoon.factors.ashare import build_panel
    from aimoon.ml.trainer import train_model

    fmt.console.print("[bold]=== ML Model Training (LightGBM) ===[/bold]")

    fmt.console.print("[dim]Loading holdings pool stocks...[/dim]")
    pool = get_holdings_pool(cfg, cache_dir=Path(cfg.cache_dir))
    if not pool:
        fmt.console.print("[red]持仓池为空！无法训练。[/red]")
        return

    spot_result = get_spot_for_codes(pool, cfg)
    if spot_result.is_err():
        fmt.console.print(f"[red]Failed to load spot data: {spot_result.unwrap_err()}[/red]")
        return

    cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
    klines = {}
    for code in spot_result.unwrap()["stock_code"].tolist():
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()

    if not klines:
        fmt.console.print("[red]No valid kline data.[/red]")
        return

    save_dir = cfg.cache_dir + "/ml"

    result = train_model(
        klines,
        save_dir=save_dir,
        n_dates=args.n_dates,
        forward_days=args.forward_days,
        force=args.force,
    )

    if result is None:
        fmt.console.print("[red]Training failed.[/red]")
        return

    fmt.console.print("\n[bold]=== Training Complete ===[/bold]")
    fmt.console.print(f"  Mean IC: {result['cv_meta'].get('mean_ic', 0):.4f}")
    fmt.console.print(f"  Val IC:  {result['cv_meta'].get('val_ic', 0):.4f}")
    fmt.console.print(f"  Stocks:  {result['cv_meta'].get('n_stocks', 0)}")
    fmt.console.print(f"  Model saved to: {save_dir}")


def _run_backtest(args: argparse.Namespace, cfg: Config, fmt: OutputFormatter) -> None:
    """Backtest — 筛选 → 预计算 ML 分数 → 回测引擎。"""
    from aimoon.backtest import precompute_scores, run_backtest
    from aimoon.data.history import get_kline

    if not _check_network():
        fmt.console.print(
            "[red]Network required for backtest. Check connection or try --demo.[/red]"
        )
        sys.exit(1)

    hold_days = getattr(args, "hold_days", cfg.hold_days)
    max_pos = getattr(args, "max_positions", cfg.max_positions)
    bt_top = getattr(args, "top", 10)
    stop_loss = getattr(args, "stop_loss", cfg.stop_loss_pct)
    take_profit = getattr(args, "take_profit", cfg.take_profit_pct)
    benchmark_code = getattr(args, "benchmark", cfg.benchmark_code)

    fmt.console.print(
        f"[bold blue]=== ML-Driven Backtest: top {bt_top}, hold {hold_days}d,"
        f" SL {stop_loss:.0%}, TP {take_profit:.0%} ===[/bold blue]"
    )

    stock_codes = _resolve_backtest_stocks(args)
    universe = _load_screening_data(cfg, fmt)
    cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)

    # Load predictor (optional — without model, uses fallback ranking)
    predictor = MLPredictor.load(Path(cfg.cache_dir) / "ml")

    fmt.console.print(f"[dim]Scoring {len(universe)} stocks...[/dim]")
    t0 = time.time()
    results, tails, all_klines = screen_universe(
        universe, cfg, cache, predictor=predictor,
    )
    fmt.console.print(f"[dim]Scored in {time.time() - t0:.1f}s[/dim]")

    if not results:
        fmt.console.print("[red]No stocks passed screening.[/red]")
        return

    if stock_codes:
        code_set = set(stock_codes)
        results = [r for r in results if r.code in code_set]
        if not results:
            fmt.console.print(f"[red]Specified stocks not found in pool: {stock_codes}[/red]")
            return

    results = compute_rps(results, tails)
    top_stocks = sorted(results, key=lambda s: s.total_score, reverse=True)[:bt_top]
    codes = [s.code for s in top_stocks]
    names = {s.code: s.name for s in top_stocks}
    stock_list = ", ".join(f"{s.code}({s.name})" for s in top_stocks)
    fmt.console.print(f"[dim]Top {len(top_stocks)} stocks: {stock_list}[/dim]")

    # Fetch full klines for backtest
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

    # Precompute per-date ML scores
    fmt.console.print("[dim]Precomputing per-date ML scores...[/dim]")
    t1 = time.time()
    scores_by_date = precompute_scores(klines, predictor=predictor)
    fmt.console.print(
        f"[dim]Precomputed {len(scores_by_date)} days in {time.time() - t1:.1f}s[/dim]"
    )

    if not scores_by_date:
        fmt.console.print("[red]No scores could be precomputed.[/red]")
        return

    # Run backtest
    fmt.console.print("[dim]Running backtest...[/dim]")
    result = run_backtest(
        klines=klines,
        names=names,
        scores_by_date=scores_by_date,
        benchmark_code=benchmark_code,
        entry_threshold=60,
        stop_loss_pct=stop_loss,
        take_profit_pct=take_profit,
        hold_days=hold_days,
        max_positions=max_pos,
    )

    fmt.display_backtest(result)

    # Backtest report
    report_path = _export_backtest_report(result, top_stocks, cfg)
    if report_path:
        fmt.console.print(f"[dim]Report: {report_path}[/dim]")


def _export_backtest_report(
    result,
    top_stocks: list,
    cfg: Config,
    filename: str | None = None,
) -> str | None:
    """Generate simplified backtest report Markdown."""
    from datetime import datetime

    try:
        if not filename:
            filename = f"backtest_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        os.makedirs(cfg.output_dir, exist_ok=True)
        filepath = os.path.join(cfg.output_dir, filename)
        top_strs = [f"- {s.code} ({s.name}) ML={s.ml_score}" for s in top_stocks[:10]]
        lines = [
            f"# A股量化回测报告 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 回测参数",
            f"- history_days: {cfg.history_days}",
            f"- hold_days: {cfg.hold_days}",
            f"- max_positions: {cfg.max_positions}",
            f"- stop_loss: {cfg.stop_loss_pct:.0%}",
            f"- take_profit: {cfg.take_profit_pct:.0%}",
            f"- benchmark: {cfg.benchmark_code}",
            "",
            "## 筛选股票",
            *top_strs,
            "",
            "## 回测结果",
            f"- 总收益: {result.total_return:.2%}",
            f"- 年化收益: {result.annual_return:.2%}",
            f"- Sharpe: {result.sharpe_ratio:.2f}",
            f"- Sortino: {result.sortino_ratio:.2f}",
            f"- 最大回撤: {result.max_drawdown:.2%}",
            f"- 胜率: {result.win_rate:.1%}",
            f"- 盈亏比: {result.profit_factor:.2f}",
            f"- 交易次数: {result.trade_count}",
            f"- 平均持仓: {result.avg_hold_days:.0f}天",
            f"- 基准收益: {result.benchmark_return:.2%}",
            f"- 超额收益: {result.excess_return:.2%}",
            "",
            "## 交易记录",
            "| 股票 | 方向 | 买入日 | 卖出日 | 买入价 | 卖出价 | 收益 | 原因 | 持仓天数 |",
            "|------|------|--------|--------|--------|--------|------|------|----------|",
        ]
        for t in result.trades:
            lines.append(
                f"| {t.code}/{t.name} | 做多 | {t.entry_date} | {t.exit_date} | "
                f"{t.entry_price:.2f} | {t.exit_price:.2f} | {t.return_pct:+.2f}% | "
                f"{t.exit_reason} | {t.hold_days} |"
            )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return filepath
    except Exception as e:
        logger.warning("Failed to export backtest report: %s", e)
        return None


def _resolve_backtest_stocks(args: argparse.Namespace) -> list[str] | None:
    """Parse --stocks argument. Returns None if not explicitly set."""
    import sys as _sys

    has_stocks_arg = any("--stocks" in a for a in _sys.argv)
    if not has_stocks_arg:
        return None
    stocks_str = getattr(args, "stocks", "")
    if not stocks_str or stocks_str.strip() == "000001":
        return None
    codes = [c.strip() for c in stocks_str.split(",") if c.strip()]
    return codes if codes else None


def _load_screening_data(cfg: Config, fmt: OutputFormatter) -> pd.DataFrame:
    """Load spot data for screening. Returns spot_df."""
    fmt.console.print("[dim]Loading holdings pool (cached)...[/dim]")
    pool = get_holdings_pool(cfg, cache_dir=Path(cfg.cache_dir))
    if not pool:
        fmt.console.print(
            "[red]持仓池为空！无法进行筛选。[/red]\n"
            "[yellow]请先刷新持仓池: aimoon refresh-pool[/yellow]"
        )
        sys.exit(1)

    fmt.console.print(f"[dim]Holdings pool: {len(pool)} stocks[/dim]")
    sr = get_spot_for_codes(pool, cfg)
    if sr.is_err():
        fmt.console.print(
            f"[red]获取持仓池行情失败: {sr.error}[/red]\n" "[yellow]请检查网络连接后重试[/yellow]"
        )
        sys.exit(1)

    spot = sr.unwrap()
    fmt.console.print(f"[dim]Spot data for {len(spot)} stocks[/dim]")

    universe = filter_universe(spot, cfg)
    fmt.console.print(f"[dim]Universe after filter: {len(universe)} stocks[/dim]")

    if len(universe) < 5:
        fmt.console.print(
            f"[red]过滤后只剩 {len(universe)} 只持仓池股票，数量不足。[/red]\n"
            "[yellow]请放宽过滤参数或刷新持仓池[/yellow]"
        )
        sys.exit(1)

    return universe


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
            predictor = MLPredictor.load(Path(cfg.cache_dir) / "ml")
            universe = _load_screening_data(cfg, fmt)
            cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
            results, tails, all_klines = screen_universe(
                universe, cfg, cache, predictor=predictor,
            )
            results = compute_rps(results, tails)
            top = sorted(results, key=lambda s: s.total_score, reverse=True)[: cfg.top_n]

            os.makedirs(output_dir, exist_ok=True)
            saved = fmt.export_csv(top, filename=f"aimoon_{ts}.csv")
            fmt.console.print(f"[green]Screen completed {ts}: {len(top)} stocks -> {saved}[/green]")

            if notify:
                summary = ", ".join(f"{s.name}({s.total_score})" for s in top[:5])
                fmt.console.print(f"[dim]Top 5: {summary}[/dim]")
        except Exception as e:
            logger.error("Scheduled screen failed: %s", e)

        h, m = map(int, target_time.split(":"))
        tomorrow = _dt.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
        if _dt.datetime.now() >= tomorrow:
            tomorrow += _dt.timedelta(days=1)
        wait = (tomorrow - _dt.datetime.now()).total_seconds()
        scheduler.enter(wait, 1, _run_once)

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
        cache_dir.mkdir(parents=True, exist_ok=True)
        DataCache.reset_global()

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
        from aimoon.watchlist import (
            add_watchlist,
            clear_watchlist,
            list_watchlist,
            remove_watchlist,
        )

        action = args.watchlist_action
        if action == "add":
            codes = [c.strip() for c in args.codes.split(",") if c.strip()]
            ok, msg = add_watchlist(codes)
            print(msg)
            if not ok:
                sys.exit(1)
        elif action == "remove":
            codes = [c.strip() for c in args.codes.split(",") if c.strip()]
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

    # Train-model
    if cfg.command == "train-model":
        _run_train_model(args, cfg, fmt)
        return

    # Backtest
    if cfg.command == "backtest":
        _run_backtest(args, cfg, fmt)
        return

    # Schedule
    if cfg.command == "schedule":
        _run_schedule(args, cfg, fmt)
        return

    # Default: screening pipeline
    predictor = MLPredictor.load(Path(cfg.cache_dir) / "ml")
    universe = _load_screening_data(cfg, fmt)
    cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)

    fmt.console.print(f"[dim]Analyzing {len(universe)} stocks...[/dim]")
    t0 = time.time()
    results, tails, all_klines = screen_universe(
        universe, cfg, cache, predictor=predictor,
    )
    fmt.console.print(f"[dim]Done in {time.time() - t0:.1f}s[/dim]")

    if not results:
        fmt.console.print("[red]No stocks passed screening.[/red]")
        return

    # RPS + sort + output
    results = compute_rps(results, tails)
    top = sorted(results, key=lambda s: s.total_score, reverse=True)[: cfg.top_n]
    fmt.display(top)

    if not cfg.no_csv and top:
        fmt.console.print(f"[dim]Exported: {fmt.export_csv(top)}[/dim]")


if __name__ == "__main__":
    main()
