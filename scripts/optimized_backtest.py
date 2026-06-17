import time

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.enhanced_backtest import EnhancedBacktestEngine
from aimoon.scoring import hybrid_score
from aimoon.scoring.rps import compute_rps
from aimoon.screener import screen_universe


def run_optimized_backtest():
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
    print('=' * 60)
    print('Optimized Backtest - Time-Aligned')
    print('=' * 60)

    print('\n1. Getting holdings pool...')
    t0 = time.time()
    pool = get_holdings_pool(cfg)
    t1 = time.time()
    print(f'   OK: {len(pool)} stocks ({t1 - t0:.2f}s)')

    print('\n2. Fetching K-line data...')
    t0 = time.time()
    klines = {}
    for code in pool:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            kdf = r.unwrap()
            if len(kdf) >= 60:
                klines[code] = kdf
    t1 = time.time()
    print(f'   OK: {len(klines)} stocks ({t1 - t0:.2f}s)')

    if len(klines) < 5:
        print('   FAIL: insufficient data')
        return

    print('\n3. Building universe...')
    t0 = time.time()
    bt_start = pd.Timestamp(cfg.backtest_start_date)
    all_dates = set()
    for kdf in klines.values():
        all_dates.update(kdf.index.tolist())
    trading_dates = sorted(all_dates)
    screen_date = max([d for d in trading_dates if d <= bt_start], default=bt_start)

    rows = []
    for code, kdf in klines.items():
        if screen_date not in kdf.index:
            continue
        loc = kdf.index.get_loc(screen_date)
        if loc < 20:
            continue
        row = kdf.loc[screen_date]
        close = float(row['close'])
        if close <= 0:
            continue
        window = kdf.iloc[max(0, loc - 19):loc + 1]
        avg_amount = float(window['amount'].mean()) if 'amount' in window.columns else 0.0
        if avg_amount < 10_000_000:
            continue
        turnover = float(row['turnover']) if 'turnover' in row.index else 0.0
        pe = float(row['pe']) if 'pe' in row.index and not pd.isna(row.get('pe')) else 0.0
        pb = float(row['pb']) if 'pb' in row.index and not pd.isna(row.get('pb')) else 0.0
        pct = float(row['pct_change']) if 'pct_change' in row.index else 0.0
        rows.append({
            'stock_code': code, 'stock_name': code, 'price': close,
            'pct_change': pct, 'turnover': turnover,
            'daily_turnover': avg_amount, 'pe': pe, 'pb': pb,
            'total_market_cap': 0.0, 'float_market_cap': 0.0,
        })
    spot = pd.DataFrame(rows).reset_index(drop=True)
    t1 = time.time()
    print(f'   OK: {len(spot)} stocks (screen: {screen_date.date()}) ({t1 - t0:.2f}s)')

    print('\n4. Screening...')
    t0 = time.time()
    results, tails, _ = screen_universe(spot, cfg, cache, use_alpha=False)
    t1 = time.time()
    print(f'   OK: {len(results)} stocks ({t1 - t0:.2f}s)')

    if not results:
        print('   FAIL: no stocks screened')
        return

    print('\n5. Scoring and ranking...')
    results = compute_rps(results, tails)
    top_n = min(10, len(results))
    top_stocks = sorted(results, key=lambda s: hybrid_score(list(s.signals)), reverse=True)[:top_n]
    for i, stock in enumerate(top_stocks, 1):
        print(f'   {i}. {stock.code} ({stock.name}) score={stock.total_score}')

    codes = [s.code for s in top_stocks]
    names = {s.code: s.name for s in top_stocks}

    print('\n6. Running backtest...')
    t0 = time.time()
    engine = EnhancedBacktestEngine(
        hold_days=cfg.hold_days, max_positions=5, commission=0.0003,
        slippage=0.001, stamp_tax=0.0005, stop_loss_pct=cfg.stop_loss_pct,
        take_profit_pct=cfg.take_profit_pct, benchmark_code=cfg.benchmark_code,
        entry_threshold=cfg.entry_threshold, max_sector_pct=cfg.max_sector_pct,
        use_reversal=False, use_alpha=False, use_kelly=True, use_ml=True,
        backtest_start_date=str(screen_date.date()),
    )
    result = engine.run_portfolio(klines, names)
    t1 = time.time()
    print(f'   OK ({t1 - t0:.2f}s)')

    print()
    print('=' * 60)
    print('RESULTS')
    print('=' * 60)
    print(f'Total Return: {result.total_return:+.2f}%')
    print(f'Sharpe: {result.sharpe_ratio:.2f}')
    print(f'Max Drawdown: {result.max_drawdown:.2f}%')
    print(f'Win Rate: {result.win_rate:.2%}')
    print(f'P/L Ratio: {result.profit_loss_ratio:.2f}')
    print(f'Trades: {result.trade_count}')
    print(f'Avg Win: {result.avg_win:+.2f}%')
    print(f'Avg Loss: {result.avg_loss:+.2f}%')
    print(f'Benchmark: {result.benchmark_return:+.2f}%')
    print(f'Excess: {result.excess_return:.2%}')

    exit_stats = {}
    for t in result.trades:
        r = t.exit_reason
        if r not in exit_stats:
            exit_stats[r] = {'n': 0, 'pnl': 0.0}
        exit_stats[r]['n'] += 1
        exit_stats[r]['pnl'] += t.return_pct
    print()
    print('Exit Reasons:')
    for r, s in sorted(exit_stats.items(), key=lambda x: -x[1]['n']):
        print(f'   {r}: {s["n"]} times, avg {s["pnl"] / s["n"]:+.2f}%')

    from aimoon.output import OutputFormatter
    fmt = OutputFormatter(cfg)
    rp = fmt.export_backtest_report(result, top_stocks, cfg)
    print(f'Report: {rp}')
    print('Done!')
    return result


if __name__ == '__main__':
    run_optimized_backtest()
