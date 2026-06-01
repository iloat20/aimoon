"""Generate a shipped holdings pool snapshot.

Run this script quarterly to refresh data/holdings_pool.pkl
so that fresh installs work without network access to
northbound/fund/ROE APIs.

Usage:
    python scripts/refresh_pool.py
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aimoon.config import Config
from aimoon.data.filters import _build_holdings_pool, save_shipped_pool


def main() -> None:
    cfg = Config()
    print("Building holdings pool from network...")
    pool = _build_holdings_pool(cfg)
    if pool:
        save_shipped_pool(pool)
        print(f"Done: {len(pool)} stocks saved.")
    else:
        print("FAILED: Could not build pool (network error?).")
        sys.exit(1)


if __name__ == "__main__":
    main()
