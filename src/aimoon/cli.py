"""CLI entry point for aimoon"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import aimoon.config as _config_mod
from aimoon.data import (
    filter_by_spot,
    filter_stock_list,
    get_history_kline,
    get_spot_data,
    get_stock_list,
)
from aimoon.output.formatter import OutputFormatter
from aimoon.strategies.backtester import BacktestEngine
from aimoon.strategies.screener import StockScreener
from aimoon.strategies.technical import TechnicalStrategy

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

def parse_args():
    p = argparse.ArgumentParser(description="A-share quant screener")
    p.add_argument("--config", type=str, default=None, help="YAML config file path")
    p.add_argument("--top", type=int, default=_config_mod.CONFIG.top_n)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--no-csv", action="store_true")
    p.add_argument("--demo", action="store_true")
    sub = p.add_subparsers(dest="command")
    bt_p = sub.add_parser("backtest", help="Backtest strategies on historical data")
    bt_p.add_argument("--stocks", type=str, default="000001", help="Comma-separated stock codes")
    bt_p.add_argument("--hold-days", type=int, default=5, help="Hold period in days")
    cache_p = sub.add_parser("cache", help="Cache management")
    cache_sub = cache_p.add_subparsers(dest="cache_action")
    cache_sub.add_parser("clear", help="Clear all cached data")
    return p.parse_args()

def generate_demo():
    import numpy as np
    import pandas as pd
    np.random.seed(42)
    stocks = [
        ("000001", "PingAnBank"), ("000002", "VankeA"),
        ("000858", "Wuliangye"), ("000725", "BOE"),
        ("002415", "Hikvision"), ("002594", "BYD"),
        ("300750", "CATL"), ("600036", "CMB"),
        ("600519", "Moutai"), ("600887", "Yili"),
        ("601318", "PingAn"), ("601398", "ICBC"),
        ("000333", "Midea"), ("002475", "Luxshare"),
        ("300059", "EastMoney"), ("002714", "Muyuan"),
        ("600276", "Hengrui"), ("601888", "ChinaTour"),
        ("000568", "Luzhou"), ("002304", "Yanghe"),
        ("600309", "Wanhua"), ("601166", "CIB"),
        ("600030", "CITIC"), ("000651", "Gree"),
        ("002049", "Unigroup"), ("300124", "Inovance"),
        ("002230", "iFlytek"), ("600585", "Conch"),
        ("601012", "LONGi"), ("000100", "TCL"),
    ]
    rows = []
    for code, name in stocks:
        price = float(np.random.uniform(10, 200))
        rows.append({
            "stock_code": code,
            "stock_name": name,
            "price": price,
            "pct_change": float(np.random.uniform(-5, 5)),
            "turnover": float(np.random.uniform(1, 15)),
            "volume": float(np.random.randint(100000, 10000000)),
            "amount": float(np.random.randint(10000000, 1000000000)),
            "amplitude": float(np.random.uniform(1, 8)),
            "high": price * 1.02,
            "low": price * 0.98,
            "open": price * 1.001,
            "prev_close": price * 0.99,
            "volume_ratio": float(np.random.uniform(0.5, 3)),
            "pe": float(np.random.uniform(5, 50)),
            "pb": float(np.random.uniform(0.5, 10)),
            "total_market_cap": float(np.random.uniform(5e9, 3e12)),
            "float_market_cap": float(np.random.uniform(1e9, 2e12)),
            "pct_60d": float(np.random.uniform(-30, 30)),
            "pct_ytd": float(np.random.uniform(-20, 50)),
        })
    spot_df = pd.DataFrame(rows)
    klines = {}
    for code, name in stocks:
        n = 120
        dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
        c = np.random.uniform(10, 200)
        close = np.maximum(c + np.cumsum(np.random.randn(n) * c * 0.02), 1.0)
        high = close + np.abs(np.random.randn(n) * close * 0.02)
        low = close - np.abs(np.random.randn(n) * close * 0.02)
        open_ = close + np.random.randn(n) * close * 0.01
        vol = np.random.randint(100000, 10000000, n).astype(float)
        df = pd.DataFrame({
            "open": open_,
            "close": close,
            "high": high,
            "low": low,
            "volume": vol,
            "turnover": np.random.uniform(0.5, 15, n),
            "pct_change": np.random.randn(n) * 3,
        }, index=dates)
        df.index.name = "date"
        klines[code] = df
    return spot_df, klines

def process_stock(code, name, spot_row, screener, klines=None):
    if klines and code in klines:
        kdf = klines[code]
    else:
        r = get_history_kline(code, days=_config_mod.CONFIG.history_days)
        if r.is_err():
            return
        kdf = r.unwrap()
    s = screener.screen_stock(code, name, kdf, spot_row)

def main():
    args = parse_args()

    if hasattr(args, "config") and args.config:
        from aimoon.config import load_config

        _config_mod.CONFIG = load_config(args.config)

    if args.command == "cache":
        if args.cache_action == "clear":
            from aimoon.cache.provider import DataCache
            cache = DataCache()
            removed = cache.clear()
            print(f"Cleared {removed} cached files")
            return
        return

    if args.command == "backtest":
        strategy = TechnicalStrategy()
        engine = BacktestEngine(strategy, hold_days=args.hold_days)
        fmt = OutputFormatter()
        fmt.console.print(f"[bold blue]=== Backtest: {strategy.name} (hold {args.hold_days}d) ===[/bold blue]")

        if args.stocks:
            codes = [c.strip() for c in args.stocks.split(",")]
            fmt.console.print(f"[dim]Backtesting {len(codes)} stocks...[/dim]")
            for code in codes:
                r = get_history_kline(code, days=_config_mod.CONFIG.history_days)
                if r.is_ok():
                    result = engine.run(code, code, r.unwrap())
                    color = "green" if result.total_return > 0 else "red"
                    fmt.console.print(
                        f"  {result.stock_code}: [{color}]{result.total_return:+.2f}%[/{color}] "
                        f"胜率={result.win_rate:.0%} 交易={result.trade_count}次 "
                        f"最大回撤={result.max_drawdown:.2%}"
                    )
        return

    fmt = OutputFormatter()
    scr = StockScreener()
    fmt.console.print("[bold blue]=== A-Share Quant Screener ===[/bold blue]")
    if args.demo:
        fmt.console.print("[dim]DEMO mode - using simulated data[/dim]")
        spot_df, klines = generate_demo()
        for _, row in spot_df.iterrows():
            process_stock(row["stock_code"], row["stock_name"], row, scr, klines)
    else:
        fmt.console.print("[dim]Fetching real-time data...[/dim]")
        sr = get_spot_data()
        if sr.is_err():
            fmt.console.print(f"[red]Failed: {sr.error}[/red]")
            fmt.console.print("[yellow]Try: python -m aimoon --demo[/yellow]")
            sys.exit(1)
        spot_df = sr.unwrap()
        filtered = filter_by_spot(spot_df)
        fmt.console.print(f"[dim]Filtered: {len(filtered)} stocks[/dim]")
        sl = get_stock_list()
        if sl.is_ok():
            vc = set(filter_stock_list(sl.unwrap())["stock_code"].tolist())
            filtered = filtered[filtered["stock_code"].isin(vc)].reset_index(drop=True)
        fmt.console.print(f"[dim]Analyzing {len(filtered)} stocks...[/dim]")
        t0 = time.time()
        done, total = 0, len(filtered)
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            fs = {}
            for _, row in filtered.iterrows():
                sr2 = row if "pe" in row.index else None
                f = ex.submit(process_stock, row["stock_code"], row["stock_name"], sr2, scr, None)
                fs[f] = row["stock_code"]
            for fut in as_completed(fs):
                done += 1
                if done % 50 == 0 or done == total:
                    el = time.time() - t0
                    r2 = done / el if el > 0 else 0
                    print(f"\r  {done}/{total} ({r2:.1f}/s)", end="", flush=True)
                try:
                    fut.result()
                except Exception as e:
                    logger.warning("Stock processing failed: %s", e)
        el = time.time() - t0
        print(f"\n[dim]Done in {el:.1f}s[/dim]")
    picks = scr.get_top_picks(args.top)
    fmt.console.print("")
    fmt.display_results(picks)
    if not args.no_csv and picks:
        fp2 = fmt.export_csv(picks)
        fmt.console.print(f"[dim]Exported: {fp2}[/dim]")

if __name__ == "__main__":
    main()
