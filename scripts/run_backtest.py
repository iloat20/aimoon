"""Quick backtest using shipped pool codes + cached/fetched klines."""
import json
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.history import get_kline
from aimoon.enhanced_backtest import EnhancedBacktestEngine

pool_path = __import__("pathlib").Path(r"C:\Users\Administrator\Downloads\work\aimoon\src\aimoon\data\holdings_pool.json")
codes = set(json.loads(pool_path.read_text(encoding="utf-8")))
print(f"Pool: {len(codes)} stocks")

cfg = Config()
cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

print("Loading klines...")
klines = {}
names = {}
for code in sorted(codes):
    r = get_kline(code, cfg.history_days, cache)
    if r.is_ok():
        klines[code] = r.unwrap()
        names[code] = code
print(f"Loaded {len(klines)} klines")

br = get_kline("000300", cfg.history_days, cache)
benchmark_kline = None
if br.is_ok():
    benchmark_kline = br.unwrap()
    print(f"Benchmark 000300: {len(benchmark_kline)} rows")

if len(klines) < 2:
    print("ERROR: Not enough kline data.")
    sys.exit(1)

print(f"\nRunning backtest with {len(klines)} stocks...")
t0 = time.time()
engine = EnhancedBacktestEngine(
    hold_days=cfg.hold_days,
    max_positions=cfg.max_positions,
    commission=0.0003,
    slippage=0.001,
    stamp_tax=0.0005,
    stop_loss_pct=cfg.stop_loss_pct,
    take_profit_pct=cfg.take_profit_pct,
    benchmark_code=cfg.benchmark_code,
    entry_threshold=cfg.entry_threshold,
    max_sector_pct=cfg.max_sector_pct,
    use_reversal=cfg.use_reversal,
    use_alpha=False,
    use_kelly=True,
    exit_ratio=0.65,
    backtest_start_date=cfg.backtest_start_date,
)
result = engine.run_portfolio(klines, names)
elapsed = time.time() - t0

print(f"\n{'=' * 60}")
print(f"Backtest Results ({elapsed:.1f}s)")
print(f"{'=' * 60}")
print(f"  Total return:    {result.total_return:>8.2f}%")
print(f"  Annual return:   {result.annual_return:>8.2f}%")
print(f"  Sharpe ratio:    {result.sharpe_ratio:>8.2f}")
print(f"  Sortino ratio:   {result.sortino_ratio:>8.2f}")
print(f"  Max drawdown:    {result.max_drawdown:>8.2f}%")
print(f"  Win rate:        {result.win_rate:>8.2%}")
print(f"  Profit factor:   {result.profit_factor:>8.2f}")
print(f"  Profit/Loss:     {result.profit_loss_ratio:>8.2f}")
print(f"  Trade count:     {result.trade_count:>8d}")
print(f"  Avg hold days:  {result.avg_hold_days:>8.1f}")
print(f"  Benchmark:       {result.benchmark_return:>8.2f}%")
print(f"  Excess return:   {result.excess_return:>8.2f}%")
print(f"  Calmar ratio:    {result.calmar_ratio:>8.2f}")
print(f"  Max consec loss: {result.max_consecutive_loss:>8d}")
print(f"  Info ratio:      {result.information_ratio:>8.2f}")
if result.ic_series:
    import numpy as np
    ic_vals = [x for x in result.ic_series if x != 0]
    if ic_vals:
        print(f"  Avg Rank IC:     {np.mean(ic_vals):>8.4f}")

if result.trades:
    print("\n  First 20 trades:")
    for t in result.trades[:20]:
        print(f"    {t.code} {t.entry_date}->{t.exit_date}: {t.return_pct:>+7.2f}% [{t.exit_reason}] hold={t.hold_days}d")
    if len(result.trades) > 20:
        print(f"    ... and {len(result.trades) - 20} more")

summary = {
    "total_return": result.total_return,
    "annual_return": result.annual_return,
    "sharpe_ratio": result.sharpe_ratio,
    "sortino_ratio": result.sortino_ratio,
    "max_drawdown": result.max_drawdown,
    "win_rate": result.win_rate,
    "profit_factor": result.profit_factor,
    "trade_count": result.trade_count,
    "avg_hold_days": result.avg_hold_days,
    "benchmark_return": result.benchmark_return,
    "excess_return": result.excess_return,
    "calmar_ratio": result.calmar_ratio,
    "n_stocks": len(klines),
}
out_path = __import__("pathlib").Path("output/backtest_summary.json")
out_path.parent.mkdir(exist_ok=True)
out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(f"\n  Summary saved: {out_path}")
