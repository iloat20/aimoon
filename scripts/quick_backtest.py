"""Quick backtest test using cached data -- bypasses slow screening."""
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.enhanced_backtest import EnhancedBacktestEngine

cfg = Config()
cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
pool = get_holdings_pool(cfg)
codes = list(pool)

# Load klines
print("Loading klines...")
klines = {}
names = {}
for code in codes:
    r = get_kline(code, cfg.history_days, cache)
    if r.is_ok():
        klines[code] = r.unwrap()
        names[code] = code
print(f"Loaded {len(klines)} klines")

# Load benchmark
br = get_kline("000300", cfg.history_days, cache)
if br.is_ok():
    klines["000300"] = br.unwrap()
    print(f"Benchmark 000300: {len(klines['000300'])} rows")

# Run backtest with use_alpha=False (skip Alpha Zoo)
print("Running backtest (no alpha)...")
t0 = time.time()
engine = EnhancedBacktestEngine(
    hold_days=5,
    max_positions=5,
    commission=0.0003,
    slippage=0.001,
    stamp_tax=0.0005,
    stop_loss_pct=0.05,
    take_profit_pct=0.30,
    benchmark_code="000300",
    entry_threshold=55,
    max_sector_pct=0.30,
    use_reversal=False,
    use_alpha=False,
    use_kelly=False,
    exit_ratio=0.5,
    backtest_start_date="2024-02-05",
)
result = engine.run_portfolio(klines, names)
elapsed = time.time() - t0
print(f"Done in {elapsed:.1f}s")
print(f"Trades: {result.trade_count}")
print(f"Total return: {result.total_return}%")
print(f"Sharpe: {result.sharpe_ratio}")
print(f"Max DD: {result.max_drawdown}%")
print(f"Win rate: {result.win_rate}")
if result.trades:
    for t in result.trades[:10]:
        print(f"  {t.code} {t.entry_date}->{t.exit_date}: {t.return_pct:.2f}% ({t.exit_reason})")
else:
    print("WARNING: 0 trades produced!")
