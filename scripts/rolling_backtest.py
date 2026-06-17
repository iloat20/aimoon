"""Rolling window backtest - screening and trading time aligned."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.enhanced_backtest import EnhancedBacktestEngine
from aimoon.scoring import hybrid_score
from aimoon.scoring.rps import compute_rps
from aimoon.screener import screen_universe

logger = logging.getLogger(__name__)


def _nearest_trading_day(target, trading_dates):
    candidates = [d for d in trading_dates if d <= target]
    if candidates:
        return candidates[-1]
    after = [d for d in trading_dates if d >= target]
    return after[0] if after else target


def build_historical_universe(klines, trading_dates, as_of_date, min_turnover=10_000_000):
    screen_date = _nearest_trading_day(as_of_date, trading_dates)
    rows = []
    for code, kdf in klines.items():
        if screen_date not in kdf.index:
            continue
        loc = kdf.index.get_loc(screen_date)
        if loc < 20:
            continue
        row = kdf.loc[screen_date]
        close = float(row["close"])
        if close <= 0:
            continue
        window = kdf.iloc[max(0, loc - 19) : loc + 1]
        avg_amount = float(window["amount"].mean()) if "amount" in window.columns else 0.0
        if avg_amount < min_turnover:
            continue
        turnover = float(row["turnover"]) if "turnover" in row.index else 0.0
        pe = float(row["pe"]) if "pe" in row.index and not pd.isna(row.get("pe")) else 0.0
        pb = float(row["pb"]) if "pb" in row.index and not pd.isna(row.get("pb")) else 0.0
        pct = float(row["pct_change"]) if "pct_change" in row.index else 0.0
        rows.append({
            "stock_code": code, "stock_name": code, "price": close,
            "pct_change": pct, "turnover": turnover,
            "daily_turnover": avg_amount, "pe": pe, "pb": pb,
            "total_market_cap": 0.0, "float_market_cap": 0.0,
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).reset_index(drop=True)


def run_single_window(klines, trading_dates, window_start, window_end, cfg, cache, top_n=8):
    universe = build_historical_universe(klines, trading_dates, window_start)
    if universe.empty:
        return []
    results, tails, klines_screen = screen_universe(
        universe, cfg, cache,
        use_alpha=True, min_daily_turnover=0,
    )
    if not results:
        return []
    results = compute_rps(results, tails)
    scored = sorted(results, key=lambda s: hybrid_score(list(s.signals)), reverse=True)[:top_n]
    codes = [s.code for s in scored]
    window_names = {s.code: s.name for s in scored}

    # 计算warmup开始日期（窗口起始日前60个交易日）
    # 使用全量K线找到窗口起始日前的第60个交易日
    all_dates_set = set()
    for code in codes:
        if code in klines:
            all_dates_set.update(klines[code].index.tolist())
    all_dates = sorted(all_dates_set)
    warmup_start = window_start
    if window_start in all_dates:
        idx = all_dates.index(window_start)
        warmup_idx = max(0, idx - 60)
        warmup_start = all_dates[warmup_idx]
    else:
        # 找最近的日期
        before = [d for d in all_dates if d <= window_start]
        if before:
            idx = len(before) - 1
            warmup_idx = max(0, idx - 60)
            warmup_start = before[warmup_idx]

    # 截取 warmup_start 到 window_end 的K线数据给引擎
    trade_klines = {}
    for code in codes:
        if code not in klines:
            continue
        kdf = klines[code]
        mask = (kdf.index >= warmup_start) & (kdf.index <= window_end)
        wk = kdf[mask]
        if len(wk) >= 5:
            trade_klines[code] = wk
    if len(trade_klines) < 2:
        return []

    engine = EnhancedBacktestEngine(
        hold_days=20,
        max_positions=5,
        commission=0.0003,
        slippage=0.001,
        stamp_tax=0.0005,
        stop_loss_pct=0.08,
        take_profit_pct=0.50,
        stop_loss_atr_multiplier=0.0,
        take_profit_atr_multiplier=0.0,
        entry_threshold=35.0,
        max_sector_pct=cfg.max_sector_pct,
        use_reversal=False,
        use_alpha=True,
        use_kelly=True,
        backtest_start_date=str(window_start.date()),
        use_ml=True,
        exit_ratio=0.3,
        check_interval=1,
    )
    result = engine.run_portfolio(trade_klines, window_names)
    import sys
    sys.stderr.write(f" engine trades: {result.trade_count}, total_ret: {result.total_return:.2f}%\n")
    sys.stderr.flush()
    return list(result.trades)


def run_rolling_backtest():
    print("=" * 60)
    print("Rolling Window Backtest")
    print("=" * 60)

    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    print("\n1. Getting holdings pool...")
    t0 = time.time()
    pool = get_holdings_pool(cfg)
    t1 = time.time()
    print(f"   OK: {len(pool)} stocks ({t1 - t0:.2f}s)")

    print("\n2. Fetching K-line data...")
    t0 = time.time()
    klines = {}
    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        futures = {ex.submit(get_kline, code, cfg.history_days, cache): code for code in pool}
        for fut in as_completed(futures):
            code = futures[fut]
            r = fut.result()
            if r.is_ok():
                kdf = r.unwrap()
                if len(kdf) >= 60:
                    klines[code] = kdf
    t1 = time.time()
    print(f"   OK: {len(klines)} stocks ({t1 - t0:.2f}s)")

    if len(klines) < 5:
        print("   FAIL: insufficient data")
        return

    all_dates = set()
    for kdf in klines.values():
        all_dates.update(kdf.index.tolist())
    trading_dates = sorted(all_dates)

    end_date = trading_dates[-1]
    start_date = end_date - timedelta(days=180)
    window_size = 90

    windows = []
    d = start_date
    while d < end_date:
        w_end = min(d + timedelta(days=window_size), end_date)
        windows.append((d, w_end))
        d = w_end

    print(f"\n3. Backtest windows: {len(windows)}")
    print(f"   From: {start_date.date()} To: {end_date.date()}")

    print("\n4. Running rolling backtest...")
    all_trades = []
    t0 = time.time()
    for i, (w_start, w_end) in enumerate(windows):
        trades = run_single_window(klines, trading_dates, w_start, w_end, cfg, cache, top_n=8)
        all_trades.extend(trades)
        t_now = time.time()
        print(f"   Window {i + 1}/{len(windows)}: {w_start.date()}->{w_end.date()} -- {len(trades)} trades ({t_now - t0:.1f}s)")
        t0 = t_now

    if not all_trades:
        print("   No trades recorded")
        return

    print("\n" + "=" * 60)
    print("ROLLING WINDOW BACKTEST RESULTS")
    print("=" * 60)

    total_trades = len(all_trades)
    wins = [t for t in all_trades if t.return_pct > 0]
    losses = [t for t in all_trades if t.return_pct <= 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
    avg_win = np.mean([t.return_pct for t in wins]) if wins else 0.0
    avg_loss = np.mean([t.return_pct for t in losses]) if losses else 0.0
    pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

    equity = [100.0]
    for t in all_trades:
        equity.append(equity[-1] * (1 + t.return_pct / 100))
    total_ret = (equity[-1] / equity[0] - 1) * 100

    peak = max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak if peak > 0 else 0)

    returns = [(equity[i] / equity[i - 1] - 1) for i in range(1, len(equity))]
    if returns:
        mr = np.mean(returns)
        sr = np.std(returns)
        sharpe = mr / sr if sr > 0 else 0.0
        downside = [r for r in returns if r < 0]
        ds = np.std(downside) if downside else 0.0
        sortino = mr / ds if ds > 0 else 0.0
    else:
        sharpe = sortino = 0.0

    exit_stats = {}
    for t in all_trades:
        reason = t.exit_reason
        if reason not in exit_stats:
            exit_stats[reason] = {"n": 0, "pnl": 0.0}
        exit_stats[reason]["n"] += 1
        exit_stats[reason]["pnl"] += t.return_pct

    print(f"\nTotal Return: {total_ret:+.2f}%")
    print(f"Sharpe Ratio: {sharpe:.2f}")
    print(f"Sortino Ratio: {sortino:.2f}")
    print(f"Max Drawdown: {max_dd * 100:.2f}%")
    print(f"Win Rate: {win_rate * 100:.1f}%")
    print(f"Profit/Loss Ratio: {pl_ratio:.2f}")
    print(f"Total Trades: {total_trades}")
    print(f"Avg Win: {avg_win:+.2f}%")
    print(f"Avg Loss: {avg_loss:+.2f}%")
    print(f"Windows: {len(windows)}")

    print("\nExit Reason Stats:")
    for reason, st in sorted(exit_stats.items(), key=lambda x: -x[1]["n"]):
        avg = st["pnl"] / st["n"]
        print(f"   {reason}: {st['n']} times, avg {avg:+.2f}%")

    import os
    os.makedirs("output", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rp = f"output/rolling_backtest_{ts}.md"
    with open(rp, "w", encoding="utf-8") as f:
        f.write(f"# Rolling Window Backtest Report {ts}\n\n")
        f.write("## Parameters\n\n")
        f.write("| Param | Value |\n|-------|-------|\n")
        f.write(f"| Windows | {len(windows)} |\n")
        f.write(f"| Window Size | {window_size} days |\n")
        f.write(f"| Period | {start_date.date()} to {end_date.date()} |\n")
        f.write(f"| Pool | {len(pool)} stocks |\n")
        f.write(f"| Stop Loss | {cfg.stop_loss_pct * 100:.0f}% |\n")
        f.write(f"| Take Profit | {cfg.take_profit_pct * 100:.0f}% |\n")
        f.write(f"| Entry Threshold | {cfg.entry_threshold} |\n\n")
        f.write("## Results\n\n")
        f.write("| Metric | Value |\n|--------|-------|\n")
        f.write(f"| Total Return | {total_ret:+.2f}% |\n")
        f.write(f"| Sharpe | {sharpe:.2f} |\n")
        f.write(f"| Sortino | {sortino:.2f} |\n")
        f.write(f"| Max Drawdown | {max_dd * 100:.2f}% |\n")
        f.write(f"| Win Rate | {win_rate * 100:.1f}% |\n")
        f.write(f"| P/L Ratio | {pl_ratio:.2f} |\n")
        f.write(f"| Trades | {total_trades} |\n\n")
        f.write("## Exit Reasons\n\n")
        f.write("| Reason | Count | Avg PnL |\n|--------|-------|--------|\n")
        for reason, st in sorted(exit_stats.items(), key=lambda x: -x[1]["n"]):
            avg = st["pnl"] / st["n"]
            f.write(f"| {reason} | {st['n']} | {avg:+.2f}% |\n")
        f.write("\n## Trade Details\n\n")
        f.write("| Code | Name | Entry | Exit | PnL | Reason | Days |\n")
        f.write("|------|------|-------|------|------|--------|------|\n")
        for t in all_trades:
            f.write(f"| {t.code} | {t.name} | {t.entry_date} | {t.exit_date} | {t.return_pct:+.2f} | {t.exit_reason} | {t.hold_days} |\n")
    print(f"\nReport saved: {rp}")
    print("\nDone!")


if __name__ == "__main__":
    run_rolling_backtest()
