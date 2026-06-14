"""文件缓存层 - pickle 序列化 + TTL 过期"""
from __future__ import annotations

import logging
import tempfile
import threading
import time
from pathlib import Path

import pandas as pd

try:
    import pyarrow.parquet as pq
    _HAS_PARQUET = True
except ImportError:
    _HAS_PARQUET = False

try:
    import orjson
    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False

logger = logging.getLogger(__name__)

# 全局单例缓存实例，避免 cli.py 等调用方创建多个独立 DataCache。
_GLOBAL_CACHE: "DataCache | None" = None
_GLOBAL_CACHE_LOCK = threading.Lock()

# pandas Copy-on-Write：全局启用，消除隐式复制。
if hasattr(pd.options.mode, "copy_on_write"):
    pd.options.mode.copy_on_write = True


class DataCache:
    """缓存 DataFrame 到磁盘，支持 TTL 过期。"""

    def __init__(self, cache_dir: str = ".aimoon_cache", ttl_hours: int = 4, use_parquet: bool = False) -> None:
        self.cache_dir = Path(cache_dir)
        self._use_parquet = use_parquet and _HAS_PARQUET
        self.ttl_seconds = ttl_hours * 3600
        self._mem_cache: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_global(cls, cache_dir: str = ".aimoon_cache", ttl_hours: int = 4) -> "DataCache":
        """返回全局单例 DataCache。"""
        global _GLOBAL_CACHE
        if _GLOBAL_CACHE is None:
            with _GLOBAL_CACHE_LOCK:
                if _GLOBAL_CACHE is None:
                    _GLOBAL_CACHE = cls(cache_dir, ttl_hours)
        return _GLOBAL_CACHE

    @classmethod
    def reset_global(cls) -> None:
        """重置全局单例（测试用）。"""
        global _GLOBAL_CACHE
        with _GLOBAL_CACHE_LOCK:
            _GLOBAL_CACHE = None

    def _path_for(self, stock_code: str) -> Path:
        ext = "parquet" if self._use_parquet else "pkl"
        return self.cache_dir / f"{stock_code}.{ext}"

    def get(self, stock_code: str) -> pd.DataFrame | None:
        """返回缓存的 DataFrame，过期或不存在返回 None。"""
        # L1: 内存缓存命中
        with self._lock:
            if stock_code in self._mem_cache:
                return self._mem_cache[stock_code]
        path = self._path_for(stock_code)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.ttl_seconds:
            logger.debug("Cache expired for %s (%.0fs old)", stock_code, age)
            return None
        try:
            if self._use_parquet and path.suffix == ".parquet":
                df = pq.read_table(path).to_pandas()
            else:
                df = pd.read_pickle(path)
            with self._lock:
                self._mem_cache[stock_code] = df
            return df
        except Exception as e:
            logger.warning("Cache read failed for %s: %s", stock_code, e)
            return None

    def put(self, stock_code: str, df: pd.DataFrame) -> None:
        """写入 DataFrame 到缓存。"""
        if self._use_parquet:
            self.put_parquet(stock_code, df)
            return
        try:
            df.to_pickle(self._path_for(stock_code))
            with self._lock:
                self._mem_cache[stock_code] = df
        except Exception as e:
            logger.warning("Cache write failed for %s: %s", stock_code, e)

    def put_parquet(self, stock_code: str, df: pd.DataFrame) -> None:
        """写入 Parquet 文件（Snappy 压缩，比 pickle 小 6x）。"""
        if not _HAS_PARQUET:
            return
        target = self._path_for(stock_code)
        try:
            import pyarrow as pa
            save_df = df.reset_index() if not isinstance(df.index, pd.RangeIndex) else df.copy()
            table = pa.Table.from_pandas(save_df)
            pq.write_table(table, target, compression="snappy")
            with self._lock:
                self._mem_cache[stock_code] = df
        except Exception as e:
            logger.warning("Parquet write failed for %s: %s", stock_code, e)

    def clear(self) -> int:
        """清除所有缓存文件，返回删除数量。"""
        count = 0
        with self._lock:
            self._mem_cache.clear()
        for ext in ("*.pkl", "*.parquet"):
            for p in self.cache_dir.glob(ext):
                p.unlink()
                count += 1
        return count
