"""???? ML ???? ? ????????? + ?????

?????
    python scripts/train_reversal.py
    python scripts/train_reversal.py --n-dates 300 --force
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.factors.panel import build_panel
from aimoon.factors.registry import get_default_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def train_reversal(
    n_dates: int = 300,
    forward_days: int = 5,
    force: bool = False,
) -> None:
    """???????? ML ?????"""
    t_start = time.time()

    # 1. ???????
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    logger.info("=== ???? ML ?? ===")
    logger.info("  n_dates: %d, forward_days: %d", n_dates, forward_days)

    pool = get_holdings_pool(cfg)
    if not pool:
        logger.error("?????")
        return
    logger.info("  ???: %d ???", len(pool))

    # 2. ?? K ???
    klines: dict = {}
    for code in pool:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()
    logger.info("  K ???: %d ???", len(klines))

    if len(klines) < 10:
        logger.error("K ?????")
        return

    # 3. ????
    panel = build_panel(klines, min_rows=60)
    if panel is None:
        logger.error("??????")
        return
    logger.info("  ??: %d ? x %d ??", panel["close"].shape[0], panel["close"].shape[1])

    registry = get_default_registry()
    logger.info("  ???: %d", len(registry.list()))

    # 4. ??????
    save_dir = Path(".aimoon_cache") / "ml"

    from aimoon.ml.trainer import train_ensemble

    logger.info("\n=== ?????????? ===")
    result = train_ensemble(
        panel=panel,
        klines=klines,
        registry=registry,
        n_dates=n_dates,
        forward_days=forward_days,
        save_dir=save_dir,
        warm_start=not force,
    )

    # 5. ????
    xgb_ic = result["xgb_result"].ic
    lgbm_ic = result["lgbm_result"].ic
    en_ic = result["en_result"].ic

    logger.info("\n=== ???? ===")
    logger.info("  XGBoost IC: %.4f", xgb_ic)
    logger.info("  LightGBM IC: %.4f", lgbm_ic)
    logger.info("  Elastic Net IC: %.4f", en_ic)
    logger.info("  XGB ??: %.2f", result["xgb_weight"])
    logger.info("  LGBM ??: %.2f", result["lgbm_weight"])
    logger.info("  EN ??: %.2f", result["en_weight"])

    total_time = time.time() - t_start
    logger.info("  ???: %.1f ?", total_time)

    # ????
    avg_ic = (abs(xgb_ic) + abs(lgbm_ic) + abs(en_ic)) / 3
    if avg_ic > 0.05:
        logger.info("  ? ????????? |IC| = %.4f > 0.05?", avg_ic)
    elif avg_ic > 0.03:
        logger.info("  ??  ????????? |IC| = %.4f?", avg_ic)
    else:
        logger.info("  ? ????????? |IC| = %.4f < 0.03?", avg_ic)


def main():
    parser = argparse.ArgumentParser(description="???? ML ??")
    parser.add_argument("--n-dates", type=int, default=300, help="???????")
    parser.add_argument("--forward-days", type=int, default=5, help="????")
    parser.add_argument("--force", action="store_true", help="??????")
    args = parser.parse_args()

    train_reversal(
        n_dates=args.n_dates,
        forward_days=args.forward_days,
        force=args.force,
    )


if __name__ == "__main__":
    main()
