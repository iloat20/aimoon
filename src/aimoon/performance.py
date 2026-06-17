"""Performance optimization utilities for aimoon.

Provides vectorized operations, parallel processing, and memory optimization.
"""

from __future__ import annotations

import gc
import hashlib
import logging
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any

import numpy as np
import pandas as pd
from tqdm import tqdm

logger = logging.getLogger(__name__)

# ── 内存优化 ──


def optimize_dataframe_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """优化 DataFrame 数据类型以减少内存使用。"""
    for col in df.columns:
        col_dtype = df[col].dtype

        if col_dtype == "float64":
            max_val = df[col].max()
            min_val = df[col].min()
            if max_val < np.finfo(np.float32).max and min_val > np.finfo(np.float32).min:
                df[col] = df[col].astype(np.float32)

        elif col_dtype == "int64":
            max_val = df[col].max()
            min_val = df[col].min()
            if max_val < np.iinfo(np.int32).max and min_val > np.iinfo(np.int32).min:
                if max_val < np.iinfo(np.int16).max and min_val > np.iinfo(np.int16).min:
                    df[col] = df[col].astype(np.int16)
                else:
                    df[col] = df[col].astype(np.int32)

    return df


# 不可降精度的价格列（必须保持 float64）
_PRICE_COLUMNS = frozenset({"open", "high", "low", "close", "amount", "volume"})


def optimize_factor_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """优化因子输出的数据类型（不用于价格数据）。"""
    result = df.copy()
    for col in result.columns:
        if col in _PRICE_COLUMNS:
            continue

        col_dtype = result[col].dtype

        if col_dtype == "float64":
            max_val = result[col].max()
            min_val = result[col].min()
            if max_val < np.finfo(np.float32).max and min_val > np.finfo(np.float32).min:
                result[col] = result[col].astype(np.float32)

        elif col_dtype == "int64":
            max_val = result[col].max()
            min_val = result[col].min()
            if max_val < np.iinfo(np.int32).max and min_val > np.iinfo(np.int32).min:
                if max_val < np.iinfo(np.int16).max and min_val > np.iinfo(np.int16).min:
                    result[col] = result[col].astype(np.int16)
                else:
                    result[col] = result[col].astype(np.int32)

    return result


def optimize_panel_dtypes(panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """优化整个面板的数据类型。"""
    optimized = {}
    for key, df in panel.items():
        if isinstance(df, pd.DataFrame):
            optimized[key] = optimize_dataframe_dtypes(df.copy())
        else:
            optimized[key] = df
    return optimized


def release_memory(*objects: Any) -> None:
    """及时释放内存。"""
    gc.collect()


def force_gc() -> None:
    """强制垃圾回收。"""
    gc.collect()


def release_from_dict(d: dict, *keys: str) -> None:
    """从字典中删除指定键并触发垃圾回收。"""
    for key in keys:
        d.pop(key, None)
    gc.collect()


# ── 向量化优化 ──


def vectorized_pct_change(series: pd.Series) -> pd.Series:
    """向量化的百分比变化计算。"""
    shifted = series.shift(1)
    return (series - shifted) / shifted


def vectorized_rolling_mean(series: pd.Series, window: int) -> pd.Series:
    """向量化的滚动均值。"""
    return series.rolling(window=window, min_periods=window).mean()


def vectorized_rolling_std(series: pd.Series, window: int) -> pd.Series:
    """向量化的滚动标准差。"""
    return series.rolling(window=window, min_periods=window).std()


# ── 并行处理 ──


def _compute_single_factor(
    factor_id: str, panel: dict[str, pd.DataFrame], registry: Any
) -> tuple[str, pd.DataFrame | None]:
    """单个因子计算（模块级函数，可 pickle）。"""
    try:
        result = registry.compute(factor_id, panel)
        return (factor_id, result)
    except Exception as e:
        logger.debug("Factor %s failed: %s", factor_id, e)
        return (factor_id, None)


def compute_factors_parallel(
    factors: list[str],
    panel: dict[str, pd.DataFrame],
    registry: Any,
    max_workers: int | None = None,
    use_processes: bool = False,
) -> dict[str, pd.DataFrame]:
    """并行计算多个因子。"""
    if max_workers is None:
        max_workers = min(multiprocessing.cpu_count(), 8)

    if len(factors) < 4:
        results = {}
        for factor_id in factors:
            try:
                results[factor_id] = registry.compute(factor_id, panel)
            except Exception as e:
                logger.debug("Factor %s failed: %s", factor_id, e)
        return results

    logger.info("Computing %d factors in parallel with %d workers", len(factors), max_workers)

    executor_class: type[ThreadPoolExecutor] = ThreadPoolExecutor
    if use_processes:
        try:
            executor_class = ProcessPoolExecutor  # type: ignore[assignment]
            logger.debug("Using ProcessPoolExecutor")
        except Exception:
            logger.warning("ProcessPoolExecutor not available, falling back to ThreadPoolExecutor")
            executor_class = ThreadPoolExecutor

    results = {}
    with executor_class(max_workers=max_workers) as executor:
        futures = [executor.submit(_compute_single_factor, f, panel, registry) for f in factors]
        for future in tqdm(futures, desc="Computing factors", total=len(factors)):
            try:
                factor_id, result = future.result(timeout=30)
                if result is not None:
                    results[factor_id] = result
            except Exception as e:
                logger.debug("Parallel computation failed: %s", e)

    logger.info("Successfully computed %d/%d factors", len(results), len(factors))
    return results


# ── 缓存优化 ──


_factor_cache: dict[str, pd.DataFrame] = {}
_factor_cache_fingerprint: str = ""
_FACTOR_CACHE_MAX_SIZE = 200
_factor_cache_ttl: float = 300.0  # 5-minute TTL
_factor_cache_timestamps: dict[str, float] = {}


def _panel_fingerprint(panel: dict[str, pd.DataFrame]) -> str:
    """基于 close 面板的 shape + 首尾值生成指纹。"""
    close = panel.get("close")
    if close is None:
        return ""
    key = f"{close.shape}|{close.iloc[0, 0]:.6f}|{close.iloc[-1, -1]:.6f}"
    return hashlib.sha256(key.encode()).hexdigest()


def clear_factor_cache() -> None:
    """清除因子缓存。"""
    global _factor_cache, _factor_cache_fingerprint
    _factor_cache.clear()
    _factor_cache_fingerprint = ""
    _factor_cache_timestamps.clear()


def _is_cache_expired(factor_id: str) -> bool:
    """Check if a cached factor has exceeded TTL."""
    ts = _factor_cache_timestamps.get(factor_id, 0.0)
    return (time.time() - ts) > _factor_cache_ttl


def get_cached_factor(
    factor_id: str,
    panel: dict[str, pd.DataFrame],
    registry: Any,
) -> pd.DataFrame:
    """带缓存的因子计算（TTL 5分钟 + 面板指纹校验）。"""
    import time as _time

    global _factor_cache, _factor_cache_fingerprint

    fingerprint = _panel_fingerprint(panel)

    if fingerprint != _factor_cache_fingerprint:
        clear_factor_cache()
        _factor_cache_fingerprint = fingerprint

    if factor_id in _factor_cache and not _is_cache_expired(factor_id):
        return _factor_cache[factor_id]

    try:
        result = registry.compute(factor_id, panel)
        _factor_cache[factor_id] = result
        _factor_cache_timestamps[factor_id] = _time.time()

        if len(_factor_cache) > _FACTOR_CACHE_MAX_SIZE:
            oldest_key = next(
                iter(sorted(_factor_cache_timestamps, key=_factor_cache_timestamps.get))
            )
            del _factor_cache[oldest_key]
            _factor_cache_timestamps.pop(oldest_key, None)

        return result
    except Exception as e:
        logger.debug("Factor %s computation failed: %s", factor_id, e)
        raise


# ── 批量处理优化 ──


def batch_compute_factors(
    factor_ids: list[str],
    panel: dict[str, pd.DataFrame],
    registry: Any,
    use_parallel: bool = True,
    max_workers: int | None = None,
) -> dict[str, pd.DataFrame]:
    """批量计算因子，自动选择串行或并行。"""
    if use_parallel and len(factor_ids) >= 4:
        return compute_factors_parallel(factor_ids, panel, registry, max_workers)
    else:
        results = {}
        for factor_id in factor_ids:
            try:
                results[factor_id] = get_cached_factor(factor_id, panel, registry)
            except Exception as e:
                logger.debug("Batch factor %s failed: %s", factor_id, e)
                continue
        return results


# ── 性能监控 ──


class PerformanceMonitor:
    """性能监控工具。"""

    def __init__(self):
        self.timings: dict[str, list[float]] = {}
        self.memory_usage: dict[str, list[float]] = {}
        self._start_times: dict[str, float] = {}

    def start_timer(self, name: str) -> None:
        """开始计时。"""
        import time

        self._start_times[name] = time.time()

    def stop_timer(self, name: str) -> float:
        """停止计时并返回耗时。"""
        import time

        if name not in self._start_times:
            return 0.0

        elapsed = time.time() - self._start_times.pop(name)
        self.timings.setdefault(name, []).append(elapsed)
        return elapsed

    def timer(self, name: str):
        """上下文管理器，自动计时代码块。"""

        class TimerContext:
            def __init__(self, monitor, timer_name):
                self.monitor = monitor
                self.name = timer_name

            def __enter__(self):
                self.monitor.start_timer(self.name)
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                self.monitor.stop_timer(self.name)
                return False

        return TimerContext(self, name)

    def record_memory(self, name: str) -> None:
        """记录当前内存使用情况。"""
        import os

        mb = 0.0
        try:
            import psutil

            process = psutil.Process(os.getpid())
            mb = process.memory_info().rss / (1024 * 1024)
        except ImportError:
            try:
                import resource

                mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # type: ignore[attr-defined]
            except (ImportError, AttributeError):
                pass

        self.memory_usage.setdefault(name, []).append(mb)

    def get_average_time(self, name: str) -> float:
        """获取平均耗时。"""
        if name not in self.timings or not self.timings[name]:
            return 0.0
        return sum(self.timings[name]) / len(self.timings[name])

    def get_total_time(self, name: str) -> float:
        """获取总耗时。"""
        if name not in self.timings or not self.timings[name]:
            return 0.0
        return sum(self.timings[name])

    def report(self) -> dict[str, float]:
        """生成性能报告（平均耗时）。"""
        return {name: self.get_average_time(name) for name in self.timings}

    def summary(self) -> str:
        """生成格式化的性能报告。"""
        lines = ["=== Performance Report ==="]

        if self.timings:
            lines.append("\n--- Timing ---")
            for name in sorted(self.timings.keys()):
                times = self.timings[name]
                if not times:
                    continue
                avg = sum(times) / len(times)
                total = sum(times)
                count = len(times)
                lines.append(f"  {name}: avg={avg:.3f}s, total={total:.3f}s, count={count}")

        if self.memory_usage:
            lines.append("\n--- Memory ---")
            for name in sorted(self.memory_usage.keys()):
                values = self.memory_usage[name]
                if not values:
                    continue
                avg = sum(values) / len(values)
                max_val = max(values)
                lines.append(f"  {name}: avg={avg:.1f}MB, max={max_val:.1f}MB")

        return "\n".join(lines)


# ── 内存分析 ──


def analyze_memory_usage(df: pd.DataFrame) -> dict[str, Any]:
    """分析 DataFrame 的内存使用情况。"""
    memory_usage = df.memory_usage(deep=True)
    total_memory = memory_usage.sum()

    return {
        "total_memory_mb": total_memory / (1024 * 1024),
        "memory_per_column": {col: memory_usage[col] / (1024 * 1024) for col in df.columns},
        "dtypes": df.dtypes.to_dict(),
        "shape": df.shape,
    }


def suggest_dtype_optimizations(df: pd.DataFrame) -> list[str]:
    """建议数据类型优化。"""
    suggestions = []

    for col in df.columns:
        col_dtype = df[col].dtype

        if col_dtype == "float64":
            max_val = df[col].max()
            min_val = df[col].min()
            if max_val < np.finfo(np.float32).max and min_val > np.finfo(np.float32).min:
                suggestions.append(f"Column '{col}': float64 -> float32 (save ~50% memory)")

        elif col_dtype == "int64":
            max_val = df[col].max()
            min_val = df[col].min()
            if max_val < np.iinfo(np.int32).max and min_val > np.iinfo(np.int32).min:
                if max_val < np.iinfo(np.int16).max and min_val > np.iinfo(np.int16).min:
                    suggestions.append(f"Column '{col}': int64 -> int16 (save ~75% memory)")
                else:
                    suggestions.append(f"Column '{col}': int64 -> int32 (save ~50% memory)")

    return suggestions
