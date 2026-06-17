"""Test Alpha Zoo panel build + signal speed."""
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.factors.panel import build_panel
from aimoon.factors.registry import get_default_registry
from aimoon.factors.scorer import compute_alpha_signals

cfg = Config()
cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
pool = list(get_holdings_pool(cfg))

# Load klines
klines = {}
for code in pool[:20]:
    r = get_kline(code, cfg.history_days, cache)
    if r.is_ok():
        klines[code] = r.unwrap()
print(f"Loaded {len(klines)} klines")

# Build panel
t0 = time.time()
panel = build_panel(klines)
print(f"Panel build: {time.time() - t0:.1f}s, stocks={len(panel['close'].columns) if panel else 0}")

if panel:
    registry = get_default_registry()
    print(f"Registry: {len(registry.list())} factors")

    # Compute alpha signals (full panel, no target_date)
    t0 = time.time()
    sigs = compute_alpha_signals(registry, panel)
    elapsed = time.time() - t0
    print(f"Alpha signals: {elapsed:.1f}s, stocks={len(sigs)}")
    if sigs:
        sample_code = list(sigs.keys())[0]
        print(f"  Sample {sample_code}: {len(sigs[sample_code])} signals")
