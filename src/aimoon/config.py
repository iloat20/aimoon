"""配置模块 — frozen dataclass，显式传递，无全局单例"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    # 筛选参数
    history_days: int = 250
    min_market_cap_yi: float = 50.0
    max_market_cap_yi: float = 2000.0
    min_turnover_pct: float = 3.0
    max_turnover_pct: float = 30.0
    min_price: float = 5.0
    max_price: float = 100.0
    min_list_days: int = 250
    top_n: int = 30
    # 机构持仓
    min_northbound_cap: float = 1.0
    min_fund_pct: float = 5.0
    # 技术指标参数
    ma_short: int = 5
    ma_mid: int = 20
    ma_long: int = 60
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    kdj_period: int = 9
    boll_period: int = 20
    boll_std: float = 2.0
    volume_ma_period: int = 20
    # 缓存
    cache_dir: str = ".aimoon_cache"
    cache_ttl_hours: int = 24
    # 输出
    output_dir: str = "output"
    # CLI 参数
    no_csv: bool = False
    workers: int = 5
    demo: bool = False
    refresh: bool = False
    command: str | None = None
    stocks: str = "000001"
    hold_days: int = 20
    max_positions: int = 2
    # 排除规则
    exclude_boards: tuple[str, ...] = ("ST", "退", "北交所")
    exclude_prefixes: tuple[str, ...] = ("8", "4")


def load_config(args: argparse.Namespace | None = None, path: str | None = None) -> Config:
    """合并配置：CLI 参数 > YAML 文件 > 默认值。"""
    overrides: dict = {}

    # YAML 文件
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

    # CLI 参数覆盖
    if args:
        cli_map = {
            "top": "top_n", "workers": "workers", "no_csv": "no_csv",
            "demo": "demo", "refresh": "refresh",
            "hold_days": "hold_days", "stocks": "stocks",
        }
        for cli_key, cfg_key in cli_map.items():
            val = getattr(args, cli_key, None)
            if val is not None:
                overrides[cfg_key] = val
        if hasattr(args, "command") and args.command:
            overrides["command"] = args.command

    return Config(**overrides)


# 向后兼容别名 — 旧代码使用 CONFIG 全局变量，Task 11 清理时移除
CONFIG = Config()
