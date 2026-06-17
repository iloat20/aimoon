"""Near-month backtest: screen at T-30, verify returns from T-30 to T.

Simple validation: pick top stocks at T-30, compute their actual returns
over the next 30 calendar days. This directly tests whether the screener
can identify stocks that go up in the near term.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import pandas as pd
import pendulum

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.scoring import hybrid_score
from aimoon.scoring.rps import compute_rps
from aimoon.screener import screen_universe

logger = logging.getLogger(__name__)


def _nearest_trading_day(target, trading_dates):
    """Find the latest trading day on or before target."""
    candidates = [d for d in trading_dates if d <= target]
    if candidates:
        return candidates[-1]
    after = [d for d in trading_dates if d >= target]
    return after[0] if after else target


def _build_universe(klines, trading_dates, screen_date, min_turnover=10_000_000):
    """Build a candidate pool from K-line data as of screen_date."""
    actual = _nearest_trading_day(screen_date, trading_dates)
    rows = []
    for code, kdf in klines.items():
        if actual not in kdf.index:
            continue
        loc = kdf.index.get_loc(actual)
        if loc < 20:
            continue
        row = kdf.loc[actual]
        close = float(row["close"])
        if close <= 0:
            continue
        window = kdf.iloc[max(0, loc - 19) : loc + 1]
        avg_amount = float(window["amount"].mean()) if "amount" in window.columns else 0.0
        if avg_amount < min_turnover:
            continue
        turnover = float(row.get("turnover", 0.0))
        pe = 0.0
        pb = 0.0
        pct = float(row.get("pct_change", 0.0))
        rows.append({
            "stock_code": code,
            "stock_name": code,
            "price": close,
            "pct_change": pct,
            "turnover": turnover,
            "daily_turnover": avg_amount,
            "pe": pe,
            "pb": pb,
            "total_market_cap": 0.0,
            "float_market_cap": 0.0,
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).reset_index(drop=True)


def run() -> None:
    print("=" * 60)
    print("Near-Month Backtest: screen T-30, verify T-30 -> T")
    print("=" * 60)

    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # Step 1: pool
    print("\n[1/6] Holdings pool ...")
    t0 = time.time()
    pool = get_holdings_pool(cfg)
    print(f"  {len(pool)} stocks  ({time.time() - t0:.1f}s)")
    if not pool:
        print("  FAIL: empty pool")
        return

    # Step 2: fetch 180d klines (covers warmup + 30d screening + return window)
    print("\n[2/6] Fetching 180d K-lines ...")
    t0 = time.time()
    klines: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        futs = {ex.submit(get_kline, c, 180, cache): c for c in pool}
        for f in as_completed(futs):
            code = futs[f]
            r = f.result()
            if r.is_ok():
                kdf = r.unwrap()
                if len(kdf) >= 60:
                    klines[code] = kdf
    print(f"  {len(klines)} stocks  ({time.time() - t0:.1f}s)")
    if len(klines) < 5:
        print("  FAIL: insufficient data")
        return

    # Step 3: timeline
    all_dates = sorted({d for kdf in klines.values() for d in kdf.index})
    today = all_dates[-1]
    screen_date = today - timedelta(days=30)
    actual_screen = _nearest_trading_day(screen_date, all_dates)

    print("\n[3/6] Timeline")
    print(f"  T (latest):    {today.date()}")
    print(f"  T-30 (screen): {actual_screen.date()}")
    print(f"  Trading days:  {len(all_dates)}")

    # Step 4: screen at T-30
    print(f"\n[4/6] Screening {len(pool)} stocks at {actual_screen.date()} ...")
    t0 = time.time()
    universe = _build_universe(klines, all_dates, actual_screen)
    if universe.empty:
        print("  FAIL: empty universe")
        return
    results, tails = screen_universe(universe, cfg, cache, use_alpha=cfg.use_alpha)
    if not results:
        print("  FAIL: no stocks passed screening")
        return
    results = compute_rps(results, tails)
    scored = sorted(results, key=lambda s: hybrid_score(list(s.signals)), reverse=True)
    print(f"  {len(scored)} stocks passed  ({time.time() - t0:.1f}s)")

    # Step 5: compute returns T-30 -> T for top N
    top_list = [5, 8, 10, 15, 20]
    max_top = max(top_list)

    # compute returns for all scored stocks (up to max_top)
    scored_top = scored[:max_top]
    codes = [s.code for s in scored_top]
    names = {s.code: s.name for s in scored_top}
    code_to_rank = {s.code: i + 1 for i, s in enumerate(scored_top)}
    code_to_score = {s.code: s.total_score for s in scored_top}

    # find T+30 trading day (actual last trading day)
    end_date = today

    print(f"\n[5/6] Computing returns {actual_screen.date()} -> {end_date.date()} ...")
    rows = []
    for s in scored_top:
        code = s.code
        kdf = klines.get(code)
        if kdf is None:
            continue
        if actual_screen not in kdf.index or end_date not in kdf.index:
            continue
        loc_start = kdf.index.get_loc(actual_screen)
        loc_end = kdf.index.get_loc(end_date)
        if loc_end <= loc_start:
            continue
        price_start = float(kdf["close"].iloc[loc_start])
        price_end = float(kdf["close"].iloc[loc_end])
        if price_start <= 0:
            continue
        ret = (price_end - price_start) / price_start * 100
        rows.append({
            "rank": code_to_rank[code],
            "code": code,
            "name": names.get(code, code),
            "score": code_to_score[code],
            "price_start": price_start,
            "price_end": price_end,
            "return_pct": round(ret, 2),
        })

    if not rows:
        print("  FAIL: no valid return data")
        return

    df = pd.DataFrame(rows).sort_values("rank")

    # Step 6: report
    print("\n[6/6] Results")
    print("=" * 60)

    # header
    print(f"{'Rank':>4} {'Code':>8} {'Name':<8} {'Score':>6} {'Start':>8} {'End':>8} {'Return%':>8}")
    print("-" * 56)
    for _, r in df.iterrows():
        print(f"{int(r['rank']):>4} {r['code']:>8} {r['name']:<8} {r['score']:>6.1f} "
              f"{r['price_start']:>8.2f} {r['price_end']:>8.2f} {r['return_pct']:>+8.2f}%")

    # summary by top-N
    print(f"\n{'=' * 60}")
    print("SUMMARY BY TOP-N")
    print(f"{'=' * 60}")
    print(f"{'Top-N':>6} {'Avg Return':>11} {'Median':>9} {'Win Rate':>9} {'Winners':>8} {'Losers':>8}")
    print("-" * 56)
    for n in top_list:
        sub = df.head(n)
        if sub.empty:
            continue
        avg_ret = sub["return_pct"].mean()
        med_ret = sub["return_pct"].median()
        winners = (sub["return_pct"] > 0).sum()
        losers = (sub["return_pct"] <= 0).sum()
        wr = winners / len(sub) * 100
        print(f"{n:>6} {avg_ret:>+10.2f}% {med_ret:>+8.2f}% {wr:>8.1f}% {winners:>8} {losers:>8}")

    # equal-weight portfolio return for each top-N
    print(f"\n{'=' * 60}")
    print("EQUAL-WEIGHT PORTFOLIO RETURN")
    print(f"{'=' * 60}")
    print(f"{'Top-N':>6} {'Port Return':>12} {'Best Stock':>12} {'Worst Stock':>12}")
    print("-" * 48)
    for n in top_list:
        sub = df.head(n)
        if sub.empty:
            continue
        port_ret = sub["return_pct"].mean()
        best = sub["return_pct"].max()
        worst = sub["return_pct"].min()
        print(f"{n:>6} {port_ret:>+11.2f}% {best:>+11.2f}% {worst:>+11.2f}%")

    # benchmark: average return of all screened stocks
    all_rets = df["return_pct"]
    print(f"\nAll {len(df)} screened stocks: avg={all_rets.mean():+.2f}%, "
          f"median={all_rets.median():+.2f}%, "
          f"win={((all_rets > 0).sum() / len(all_rets) * 100):.1f}%")

    # save report
    os.makedirs("output", exist_ok=True)
    ts = pendulum.now().strftime("%Y%m%d_%H%M%S")
    rp = f"output/near_month_backtest_{ts}.md"
    with open(rp, "w", encoding="utf-8") as f:
        f.write(f"# Near-Month Backtest {ts}\n\n")
        f.write(f"Screen date: {actual_screen.date()}  |  "
                f"End date: {end_date.date()}  |  "
                f"Pool: {len(pool)} stocks  |  "
                f"Screened: {len(universe)} stocks  |  "
                f"Ranked: {len(df)} stocks\n\n")
        f.write("## Return Summary by Top-N\n\n")
        f.write("| Top-N | Avg Return | Median | Win Rate | Winners | Losers |\n")
        f.write("|-------|-----------|--------|----------|---------|--------|\n")
        for n in top_list:
            sub = df.head(n)
            if sub.empty:
                continue
            avg_ret = sub["return_pct"].mean()
            med_ret = sub["return_pct"].median()
            winners = (sub["return_pct"] > 0).sum()
            losers = (sub["return_pct"] <= 0).sum()
            wr = winners / len(sub) * 100
            f.write(f"| {n} | {avg_ret:+.2f}% | {med_ret:+.2f}% | {wr:.1f}% | {winners} | {losers} |\n")
        f.write("\n## Equal-Weight Portfolio\n\n")
        f.write("| Top-N | Port Return | Best | Worst |\n")
        f.write("|-------|------------|------|-------|\n")
        for n in top_list:
            sub = df.head(n)
            if sub.empty:
                continue
            port_ret = sub["return_pct"].mean()
            best = sub["return_pct"].max()
            worst = sub["return_pct"].min()
            f.write(f"| {n} | {port_ret:+.2f}% | {best:+.2f}% | {worst:+.2f}% |\n")
        f.write("\n## Individual Returns\n\n")
        f.write("| Rank | Code | Name | Score | Start | End | Return |\n")
        f.write("|------|------|------|-------|-------|-----|--------|\n")
        for _, r in df.iterrows():
            f.write(f"| {int(r['rank'])} | {r['code']} | {r['name']} | "
                    f"{r['score']:.1f} | {r['price_start']:.2f} | "
                    f"{r['price_end']:.2f} | {r['return_pct']:+.2f}% |\n")
    print(f"\nReport: {rp}")
    print("Done!")


if __name__ == "__main__":
    run()
