"""配置模块 - 禁止直访环境变量"""
from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AppConfig:
    """应用配置，所有参数集中管理"""
    history_days: int = 250
    recent_days: int = 20
    min_market_cap_yi: float = 50.0
    max_market_cap_yi: float = 2000.0
    min_turnover_pct: float = 3.0
    max_turnover_pct: float = 30.0
    min_price: float = 5.0
    max_price: float = 100.0
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
    top_n: int = 30
    output_dir: str = "output"
    exclude_boards: tuple[str, ...] = ("ST", "退", "北交所")
    exclude_prefixes: tuple[str, ...] = ("8", "4")
    cache_ttl_hours: int = 4
    min_northbound_shares: int = 5_000_000
    min_social_security_pct: float = 2.0


def load_config(path: str | None = None) -> AppConfig:
    """加载配置：默认值 < YAML 文件。
    YAML 文件不存在时使用默认值并记录警告。
    """
    if path is None:
        return AppConfig()

    p = Path(path)
    if not p.exists():
        logger.warning("Config file not found: %s, using defaults", path)
        return AppConfig()

    try:
        import yaml

        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load config %s: %s, using defaults", path, e)
        return AppConfig()

    valid_fields = {f.name for f in fields(AppConfig)}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return AppConfig(**filtered)


CONFIG = AppConfig()
