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
from aimoon.output import OutputFormatter
from aimoon.scoring.rps import compute_rps
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
    p.add_argument(
        "--no-alpha", action="store_true", default=None, help="禁用 Alpha Zoo 截面因子（默认启用）"
    )
    sub = p.add_subparsers(dest="command")

    # backtest
    bt = sub.add_parser("backtest")
    bt.add_argument("--stocks", type=str, default="000001")
    bt.add_argument("--hold-days", type=int, default=22)
    bt.add_argument("--max-positions", type=int, default=5)
    bt.add_argument("--commission", type=float, default=0.0003)
    bt.add_argument("--slippage", type=float, default=0.001)
    bt.add_argument("--stamp-tax", type=float, default=0.0005)
    bt.add_argument("--top", type=int, default=10)
    bt.add_argument("--stop-loss", type=float, default=0.05)
    bt.add_argument("--take-profit", type=float, default=0.20)
    bt.add_argument("--benchmark", type=str, default="000300")
    bt.add_argument("--walk-forward", action="store_true")
    bt.add_argument(
        "--forward-days", type=int, default=22, help="IC 评估的前瞻天数（5=周度, 22=月度, 默认22）"
    )
    bt.add_argument("--no-alpha", action="store_true", default=None, help="禁用 Alpha Zoo 截面因子")
    bt.add_argument("--no-qf-lib", action="store_true", default=False, help="使用旧版回测引擎（非 QF-Lib）")

    # walk-forward defaults to True when NOT using QF-Lib, but only if explicitly set

    # optimize
    opt = sub.add_parser("optimize")
    opt.add_argument("--params", type=str, default="stop_loss_pct,hold_days")
    opt.add_argument(
        "--metric", type=str, default="sharpe", choices=["sharpe", "sortino", "return"]
    )
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

    # train-model
    tm = sub.add_parser("train-model", help="Train ML model with advanced options")
    tm.add_argument("--force", action="store_true", help="Force full retrain (ignore cache)")
    tm.add_argument("--no-warm-start", action="store_true", help="Disable warm-start")
    tm.add_argument(
        "--early-stop",
        action="store_true",
        help="Enable consecutive-fold early stopping + overfit auto-recovery",
    )
    tm.add_argument(
        "--overfit-threshold",
        type=float,
        default=1.5,
        help="Train/val IC ratio threshold for complexity reduction (default 1.5)",
    )
    tm.add_argument(
        "--optuna", action="store_true", help="Run Optuna hyperparameter search before training"
    )
    tm.add_argument(
        "--optuna-trials", type=int, default=80, help="Number of Optuna trials (default 80)"
    )
    tm.add_argument(
        "--optuna-timeout",
        type=int,
        default=None,
        help="Max seconds for Optuna search (default: no limit)",
    )
    tm.add_argument(
        "--n-dates",
        type=int,
        default=300,
        help="Number of historical dates for training (default 300)",
    )
    tm.add_argument(
        "--forward-days",
        type=int,
        default=22,
        help="Forward return horizon for labels (5=weekly, 22=monthly, default 22)",
    )
    tm.add_argument(
        "--smart-incremental",
        action="store_true",
        help="Enable smart incremental learning (A/B dual model + EWC)",
    )

    return p.parse_args()


def _run_train_model(
    args: argparse.Namespace,
    cfg: Config,
    fmt: OutputFormatter,
) -> None:
    """Handle `aimoon train-model` subcommand."""
    from aimoon.factors.panel import build_panel
    from aimoon.factors.registry import get_default_registry
    from aimoon.ml.trainer import train_ensemble

    fmt.console.print("[bold]=== ML Model Training ===[/bold]")

    # Load data
    if cfg.demo:
        from aimoon.demo import generate_demo

        fmt.console.print("[dim]Using demo data[/dim]")
        spot_df, klines = generate_demo(n_stocks=50)
    else:
        fmt.console.print("[dim]Loading holdings pool stocks...[/dim]")
        pool = get_holdings_pool(cfg, cache_dir=Path(cfg.cache_dir))
        if not pool:
            fmt.console.print("[red]持仓池为空！无法训练。[/red]")
            return

        spot_result = get_spot_for_codes(pool, cfg)
        if spot_result.is_err():
            fmt.console.print(f"[red]Failed to load spot data: {spot_result.unwrap_err()}[/red]")
            return
        spot_df = spot_result.unwrap()

        from aimoon.data.history import get_kline

        cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
        klines = {}
        for code in spot_df["stock_code"].tolist():
            r = get_kline(code, cfg.history_days, cache)
            if r.is_ok():
                klines[code] = r.unwrap()

    if not klines:
        fmt.console.print("[red]No valid kline data.[/red]")
        return

    panel = build_panel(klines, min_rows=60)
    if panel is None:
        fmt.console.print("[red]Cannot build panel data.[/red]")
        return

    registry = get_default_registry()
    save_dir = cfg.cache_dir + "/ml"

    fmt.console.print(f"  Stocks: {len(klines)}, Panel: {panel['close'].shape[0]} days")
    fmt.console.print(
        f"  Options: early_stop={args.early_stop}, optuna={args.optuna}, "
        f"smart_incremental={args.smart_incremental}"
    )

    result = train_ensemble(
        panel,
        klines,
        registry,
        n_dates=args.n_dates,
        forward_days=getattr(args, "forward_days", 22),
        save_dir=save_dir,
        warm_start=not args.no_warm_start,
        smart_incremental=getattr(args, "smart_incremental", False),
        use_early_stop=args.early_stop,
        overfit_threshold=args.overfit_threshold,
        use_optuna=args.optuna,
        optuna_trials=args.optuna_trials,
        optuna_timeout=args.optuna_timeout,
    )

    fmt.console.print("\n[bold]=== Training Complete ===[/bold]")
    fmt.console.print(f"  XGBoost IC: {result['xgb_result'].ic:.4f}")
    fmt.console.print(f"  LightGBM IC: {result['lgbm_result'].ic:.4f}")
    fmt.console.print(f"  Elastic Net IC: {result['en_result'].ic:.4f}")
    w_xgb = result["xgb_weight"]
    w_lgbm = result["lgbm_weight"]
    fmt.console.print(f"  Weights: XGB={w_xgb:.2f}, LGBM={w_lgbm:.2f}")
    fmt.console.print(f"  Model saved to: {save_dir}")


def _run_evaluate(args: argparse.Namespace, cfg: Config, fmt: OutputFormatter) -> None:
    """Factor evaluation sub-command."""
    from aimoon.data.history import get_kline
    from aimoon.factor_eval import evaluate_all_scorers

    cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)

    pool = get_holdings_pool(cfg, cache_dir=Path(cfg.cache_dir))
    if not pool:
        fmt.console.print("[red]持仓池为空！无法进行因子评估。[/red]")
        return

    codes = sorted(pool)
    klines: dict = {}

    fmt.console.print(f"[dim]Loading klines for {len(codes)} pool stocks...[/dim]")
    for code in codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()

    if not klines:
        fmt.console.print("[red]No valid stock data.[/red]")
        return

    fmt.console.print(
        f"[dim]Evaluating factors ({args.eval_days} days, forward={args.forward_days}d)...[/dim]"
    )
    t0 = time.time()
    evals = evaluate_all_scorers(klines, args.forward_days, args.eval_days)
    fmt.console.print(f"[dim]Done in {time.time() - t0:.1f}s[/dim]")

    if not evals:
        fmt.console.print("[yellow]Not enough data for factor evaluation.[/yellow]")
        return

    fmt.display_factor_eval(evals)


def _run_backtest(args: argparse.Namespace, cfg: Config, fmt: OutputFormatter) -> None:
    """Backtest sub-command -- 先筛选 top 股票，再做回测（默认 QF-Lib 事件驱动引擎）。"""
    from aimoon.data.history import get_kline
    from aimoon.scoring.rps import compute_rps
    from aimoon.qf_backtest import run_qf_backtest, is_qf_lib_available, precompute_ml_scores_by_date
    from aimoon.qf_backtest.models import QFBacktestConfig

    if not _check_network():
        fmt.console.print(
            "[red]Network required for backtest. Check connection or try --demo.[/red]"
        )
        sys.exit(1)

    use_qf = not getattr(args, "no_qf_lib", False)
    max_pos = getattr(args, "max_positions", cfg.max_positions)
    commission = getattr(args, "commission", 0.0003)
    slippage = getattr(args, "slippage", 0.001)
    stamp_tax = getattr(args, "stamp_tax", 0.0005)
    bt_top = getattr(args, "top", 10)
    stop_loss = cfg.stop_loss_pct
    take_profit = cfg.take_profit_pct
    benchmark_code = cfg.benchmark_code

    engine_name = "QF-Lib" if use_qf else "Enhanced"
    fmt.console.print(
        f"[bold blue]=== {engine_name} Backtest: top {bt_top}, hold {cfg.hold_days}d,"
        f" SL {stop_loss:.0%}, TP {take_profit:.0%} ===[/bold blue]"
    )

    # Check QF-Lib availability
    if use_qf and not is_qf_lib_available():
        fmt.console.print("[yellow]QF-Lib not installed. Install: uv pip install qf-lib[/yellow]")
        fmt.console.print("[yellow]Falling back to Enhanced engine.[/yellow]")
        use_qf = False

    stock_codes = _resolve_backtest_stocks(args, cfg, fmt)
    universe = _load_screening_data(cfg, fmt)
    cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)

    fmt.console.print(f"[dim]Scoring {len(universe)} stocks...[/dim]")
    t0 = time.time()
    results, tails, all_klines = screen_universe(universe, cfg, cache)
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

        wf = walk_forward_validate(
            klines, names, cfg, cache, train_pct=cfg.train_pct, n_splits=cfg.n_splits
        )
        fmt.display_walk_forward(wf)
        return

    # ------------------------------------------------------------------
    # QF-Lib engine (default)
    # ------------------------------------------------------------------
    if use_qf:
        all_dates = sorted({
            pd.Timestamp(d)
            for k in klines.values() if not k.empty
            for d in k.index
        })
        valid_dates = [d for d in all_dates if d >= pd.Timestamp(cfg.backtest_start_date)] if all_dates else all_dates
        sorted_dates = valid_dates if valid_dates else all_dates[-60:] if len(all_dates) >= 60 else all_dates
        stock_scores = {s.code: s.total_score for s in top_stocks}
        ml_scores_by_date = {}
        try:
            ml_scores_by_date = precompute_ml_scores_by_date(
                klines, sorted_dates, cache_dir=f"{cfg.cache_dir}/ml", stock_scores=stock_scores,
            )
        except Exception as exc:
            logger.warning("ML score pre-computation failed: %s", exc, exc_info=True)

        if not ml_scores_by_date:
            fmt.console.print("[yellow]No per-date scores generated. Using score-based fallback.[/yellow]")
            base_scores = {s.code: s.total_score for s in top_stocks}
            min_s = min(base_scores.values()) if base_scores else 50
            max_s = max(base_scores.values()) if base_scores else 50
            spread = max(max_s - min_s, 10)
            for idx, d in enumerate(sorted_dates):
                scores = {}
                for code, sc in base_scores.items():
                    jitter = int((idx % 5 - 2) * 2)
                    scores[code] = max(30, min(100, sc + jitter))
                ml_scores_by_date[str(d)[:10]] = scores

        qf_cfg = QFBacktestConfig(
            start_date=cfg.backtest_start_date,
            initial_cash=1_000_000.0,
            hold_days=cfg.hold_days,
            max_positions=max_pos,
            commission_pct=commission,
            slippage_pct=slippage,
            stamp_tax_pct=stamp_tax,
            entry_threshold=50,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            benchmark_code=benchmark_code,
            regime="sideways",
            backtest_name="aimoon QF-Lib Backtest",
        )
        result = run_qf_backtest(
            klines=klines,
            ml_scores_by_date=ml_scores_by_date,
            names=names,
            benchmark_code=benchmark_code,
            config=qf_cfg,
        )
        fmt.display_qf_backtest(result)
        return

    # ------------------------------------------------------------------
    # Legacy Enhanced engine (when --no-qf-lib)
    # ------------------------------------------------------------------
    from aimoon.enhanced_backtest import EnhancedBacktestEngine

    engine = EnhancedBacktestEngine(
        hold_days=cfg.hold_days,
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
        use_alpha=cfg.use_alpha if getattr(args, "no_alpha", None) is not True else False,
        use_kelly=False,
        exit_ratio=0.5,
        forward_days=getattr(args, "forward_days", 22),
    )
    result = engine.run_portfolio(klines, names)
    fmt.display_enhanced_backtest(result)

    # Charts (optional matplotlib)
    try:
        from aimoon.charts import plot_drawdown, plot_equity_curve, plot_monthly_returns

        os.makedirs(cfg.output_dir, exist_ok=True)
        eq_path = os.path.join(cfg.output_dir, "equity_curve.png")
        dd_path = os.path.join(cfg.output_dir, "drawdown.png")
        mr_path = os.path.join(cfg.output_dir, "monthly_returns.png")
        plot_equity_curve(
            list(result.equity_curve) if hasattr(result, "equity_curve") else [],
            title="Portfolio Equity Curve", filepath=eq_path,
        )
        plot_drawdown(
            result.drawdown_curve if hasattr(result, "drawdown_curve") else [],
            filepath=dd_path,
        )
        plot_monthly_returns(
            result.trades if hasattr(result, "trades") else [], filepath=mr_path,
        )
        fmt.console.print(f"[dim]Charts: {eq_path}, {dd_path}, {mr_path}[/dim]")
    except ImportError:
        pass

    # Backtest report
    report_path = fmt.export_backtest_report(result, top_stocks, cfg)
    fmt.console.print(f"[dim]Report: {report_path}[/dim]")


def _resolve_backtest_stocks(
    args: argparse.Namespace,
    cfg: Config,
    fmt: OutputFormatter,
) -> list[str] | None:
    """解析--stocks参数。用户指定时返回股票列表，否则返回None使用池排序。"""
    import sys as _sys

    # 检测用户是否显式传了 --stocks（通过检查命令行参数）
    has_stocks_arg = any("--stocks" in a for a in _sys.argv)
    if not has_stocks_arg:
        return None
    stocks_str = getattr(args, "stocks", "")
    if not stocks_str or stocks_str.strip() == "000001":
        return None
    codes = [c.strip() for c in stocks_str.split(",") if c.strip()]
    if codes:
        fmt.console.print(f"[dim]Backtest stocks: {', '.join(codes)}[/dim]")
        return codes
    return None


def _load_screening_data(
    cfg: Config,
    fmt: OutputFormatter,
) -> pd.DataFrame:
    """Load spot data for screening. Returns spot_df.

    强制使用机构持仓池（北向+基金+ROE），不回退到全市场。
    回测必须基于持仓池股票，确保选股质量约束。
    """
    fmt.console.print("[dim]Loading holdings pool (cached)...[/dim]")
    pool = get_holdings_pool(cfg, cache_dir=Path(cfg.cache_dir))
    if not pool:
        fmt.console.print(
            "[red]持仓池为空！无法进行回测。[/red]\n"
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
            f"[red]过滤后只剩 {len(universe)} 只持仓池股票，数量不足无法回测。[/red]\n"
            "[yellow]请放宽过滤参数 (如 max_pe_ttm, min_dividend_yield) 或刷新持仓池[/yellow]"
        )
        sys.exit(1)

    return universe


def _run_optimize(args: argparse.Namespace, cfg: Config, fmt: OutputFormatter) -> None:
    """Parameter optimization sub-command."""
    from aimoon.data.history import get_kline
    from aimoon.optimizer import _PARAM_RANGES, grid_search
    from aimoon.scoring.rps import compute_rps

    bt_top = getattr(args, "top", cfg.top_n)
    metric = getattr(args, "metric", "sharpe")
    max_trials = getattr(args, "trials", 50)
    param_names = getattr(args, "params", "stop_loss_pct,hold_days").split(",")
    param_ranges = {k: v for k, v in _PARAM_RANGES.items() if k in param_names}

    fmt.console.print(
        f"[bold blue]=== Optimize: metric={metric}, trials={max_trials},"
        f" params={list(param_ranges.keys())} ===[/bold blue]"
    )

    universe = _load_screening_data(cfg, fmt)
    cache = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)

    fmt.console.print(f"[dim]Scoring {len(universe)} stocks...[/dim]")
    results, tails, all_klines = screen_universe(universe, cfg, cache)
    if not results:
        fmt.console.print("[red]No stocks passed screening.[/red]")
        return

    results = compute_rps(results, tails)
    top_stocks = sorted(results, key=lambda s: s.total_score, reverse=True)[:bt_top]
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
    opt_results = grid_search(
        klines, names, cfg, cache, param_ranges=param_ranges, metric=metric, max_trials=max_trials
    )
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
        from aimoon.scoring.rps import compute_rps

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

            results = compute_rps(results, tails)
            top = sorted(results, key=lambda s: s.total_score, reverse=True)[: cfg.top_n]

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

    # Factor evaluation
    if cfg.command == "evaluate":
        _run_evaluate(args, cfg, fmt)
        return

    # Train-model
    if cfg.command == "train-model":
        _run_train_model(args, cfg, fmt)
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
        from aimoon.data.history import get_kline as _get_kline
        from aimoon.regime_enhanced import detect_regime
        from aimoon.scoring.adaptive_weight import apply_regime_to_list

        _c = DataCache.get_global(cfg.cache_dir, cfg.cache_ttl_hours)
        bench_code = "000001"
        br = _get_kline(bench_code, cfg.history_days, _c)
        if br.is_ok():
            regime = detect_regime(br.unwrap())
            fmt.console.print(f"[dim]Market regime: {regime}[/dim]")
            results = apply_regime_to_list(results, regime)

    # ── Super Turtle 策略信号 ──
    from aimoon.risk import PortfolioState, Position, RiskLimits, check_risk_limits
    from aimoon.scoring.turtle import generate_turtle_plan

    turtle_plans: dict = {}
    for r in results:
        kdf = all_klines.get(r.code)
        if kdf is not None and len(kdf) >= 25:
            plan = generate_turtle_plan(kdf, r.code, r.name)
            if plan is not None:
                turtle_plans[r.code] = plan

    # Risk warnings
    limits = RiskLimits(
        max_position_pct=cfg.max_position_pct,
        max_sector_pct=cfg.max_sector_pct,
        max_drawdown_limit=cfg.max_drawdown_limit,
        target_volatility=cfg.target_volatility,
    )
    ps = PortfolioState()
    weight_each = 1.0 / max(len(results), 1)
    for r in results[: cfg.top_n]:
        ps.positions[r.code] = Position(
            code=r.code,
            name=r.name,
            weight=weight_each,
            entry_price=r.price,
            sector="",
        )
    violations = check_risk_limits(ps, limits)
    if violations:
        fmt.console.print(f"[yellow]Risk warnings ({len(violations)}):[/yellow]")
        for v in violations:
            fmt.console.print(f"  [dim]{v[0]}: {v[1:]}[/dim]")

    top = sorted(results, key=lambda s: s.total_score, reverse=True)[: cfg.top_n]
    fmt.display(top, turtle_plans=turtle_plans)
    if turtle_plans:
        fmt.display_turtle_plans(turtle_plans)
    if not cfg.no_csv and top:
        fmt.console.print(f"[dim]Exported: {fmt.export_csv(top)}[/dim]")
        regime_str = str(regime) if regime else None
        fmt.console.print(f"[dim]Exported: {fmt.export_markdown(top, regime=regime_str)}[/dim]")


if __name__ == "__main__":
    main()
