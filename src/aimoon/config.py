"""Config module -- frozen dataclass, explicit passing, no global singleton."""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    # Screening parameters
    history_days: int = 250
    min_market_cap_yi: float = 10.0
    max_market_cap_yi: float = 10000.0
    min_turnover_pct: float = 0.0
    max_turnover_pct: float = 100.0
    min_price: float = 0.0
    max_price: float = 99999.0
    min_list_days: int = 250
    top_n: int = 20
    # Institutional holdings
    min_northbound_cap: float = 1.0
    min_fund_pct: float = 5.0
    # Valuation filters
    max_pb: float = 10.0
    max_pe_ttm: float = 26.0
    min_dividend_yield: float = 1.5
    # Technical indicator params
    ma_short: int = 5
    ma_mid: int = 20
    ma_long: int = 60
    rsi_period: int = 10
    macd_fast: int = 10
    macd_slow: int = 20
    macd_signal: int = 6
    kdj_period: int = 10
    boll_period: int = 20
    boll_std: float = 2.0
    volume_ma_period: int = 20
    # Cache
    cache_dir: str = ".aimoon_cache"
    cache_ttl_hours: int = 24
    # Output
    output_dir: str = "output"
    # CLI parameters
    no_csv: bool = False
    workers: int = 20
    demo: bool = False
    refresh: bool = False
    use_reversal: bool = False
    use_alpha: bool = True
    command: str | None = None
    stocks: str = "000001"
    hold_days: int = 22
    max_positions: int = 4
    # Exclusion rules
    exclude_boards: tuple[str, ...] = ("ST", "\u9000", "\u5317\u4ea4\u6240")
    exclude_prefixes: tuple[str, ...] = ("8", "4")
    # Risk limits
    max_position_pct: float = 0.10
    max_sector_pct: float = 0.30
    max_drawdown_limit: float = 0.15
    target_volatility: float = 0.15
    # Enhanced backtest defaults
    stop_loss_pct: float = 0.035
    take_profit_pct: float = 0.14
    entry_threshold: float = 50.0
    benchmark_code: str = "000300"
    # Default start date for training and backtesting (YYYY-MM-DD)
    backtest_start_date: str = "2025-02-01"

    # Walk-forward
    train_pct: float = 0.7
    n_splits: int = 3


def load_config(args: argparse.Namespace | None = None, path: str | None = None) -> Config:
    """Merge config: CLI args > YAML file > defaults."""
    overrides: dict = {}

    # YAML file
    if path:
        p = Path(path)
        if p.exists():
            try:
                import yaml

                with open(p, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                valid = {f.name for f in fields(Config)}
                tuple_fields = {f.name for f in fields(Config) if isinstance(f.default, tuple)}
                for k, v in data.items():
                    if k in valid:
                        overrides[k] = tuple(v) if k in tuple_fields and isinstance(v, list) else v
            except Exception as e:
                logger.warning("Failed to load config %s: %s", path, e)

    # CLI overrides
    if args:
        cli_map = {
            "top": "top_n",
            "workers": "workers",
            "no_csv": "no_csv",
            "demo": "demo",
            "refresh": "refresh",
            "hold_days": "hold_days",
            "stocks": "stocks",
            "stop_loss": "stop_loss_pct",
            "take_profit": "take_profit_pct",
            "benchmark": "benchmark_code",
            "reversal": "use_reversal",
        }
        for cli_key, cfg_key in cli_map.items():
            val = getattr(args, cli_key, None)
            if val is not None:
                overrides[cfg_key] = val
        if hasattr(args, "command") and args.command:
            overrides["command"] = args.command
        # --no-alpha: 显式反转（default=None，仅在用户传入时生效）
        if getattr(args, "no_alpha", None) is True:
            overrides["use_alpha"] = False

    return Config(**overrides)


# Backward-compat alias -- legacy code uses CONFIG global, remove in cleanup

DEFAULT_CONFIG = Config()
