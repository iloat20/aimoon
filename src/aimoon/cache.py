"""文件缓存层 - pickle 序列化 + TTL 过期"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class DataCache:
    """缓存 DataFrame 到磁盘，支持 TTL 过期。"""

    def __init__(self, cache_dir: str = ".aimoon_cache", ttl_hours: int = 4) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_hours * 3600
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, stock_code: str) -> Path:
        return self.cache_dir / f"{stock_code}.pkl"

    def get(self, stock_code: str) -> pd.DataFrame | None:
        """返回缓存的 DataFrame，过期或不存在返回 None。"""
        path = self._path_for(stock_code)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.ttl_seconds:
            logger.debug("Cache expired for %s (%.0fs old)", stock_code, age)
            return None
        try:
            return pd.read_pickle(path)
        except Exception as e:
            logger.warning("Cache read failed for %s: %s", stock_code, e)
            return None

    def put(self, stock_code: str, df: pd.DataFrame) -> None:
        """写入 DataFrame 到缓存。"""
        try:
            df.to_pickle(self._path_for(stock_code))
        except Exception as e:
            logger.warning("Cache write failed for %s: %s", stock_code, e)

    def clear(self) -> int:
        """清除所有缓存文件，返回删除数量。"""
        count = 0
        for p in self.cache_dir.glob("*.pkl"):
            p.unlink()
            count += 1
        return count
