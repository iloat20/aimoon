"""因子质量前置过滤系统。

在训练/回测/筛选开始前，使用 ICIR + Turnover + Correlation 三大指标
对全部 457 个 Alpha Zoo 因子做一次全面的质量过滤，锁定高质量因子列表到文件。

设计目标：
1. 过滤一次，到处复用 — 确保训练和推理使用完全相同的因子集
2. 避免在每次回测/筛选时重复计算全部 457 个因子
3. 高质量因子列表持久化到文件，跨会话跨进程共享
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from pathlib import Path

import pandas as pd

from aimoon.factors.quality import (
    compute_factor_correlation,
    compute_factor_turnover,
    filter_factors,
)
from aimoon.factors.registry import Registry, RegistryError, SkipAlphaError

logger = logging.getLogger(__name__)

# ── 缓存配置 ──
_DEFAULT_CACHE_DIR = Path(".aimoon_cache") / "factor_quality"
_QC_CACHE_TTL_HOURS = 720  # 30 天

# ── 过滤阈值 ──
_DEFAULT_ICIR_THRESHOLD = 0.01
_DEFAULT_TURNOVER_THRESHOLD = 0.8
_DEFAULT_CORRELATION_THRESHOLD = 0.95

# ── 预过滤后用于 ML 训练的最大因子数 ──
# 前置过滤后保留的因子质量较高，可以放心使用更多因子
_MAX_ALPHA_ZOO_FACTORS = 200


def run_quality_filter(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    *,
    icir_threshold: float = _DEFAULT_ICIR_THRESHOLD,
    turnover_threshold: float = _DEFAULT_TURNOVER_THRESHOLD,
    correlation_threshold: float = _DEFAULT_CORRELATION_THRESHOLD,
    factor_cache: dict[str, pd.DataFrame] | None = None,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> list[str]:
    """运行完整的因子质量过滤管线，返回过滤后的因子 ID 列表。

    步骤：
    1. 计算全部因子的 ICIR（EWMA 加权）
    2. 计算因子周转率（稳定性）
    3. 计算因子两两相关性（去冗余）
    4. 三网关过滤

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo 面板数据。
    klines : dict[str, pd.DataFrame]
        股票 K 线数据（用于计算前瞻收益标签）。
    registry : Registry | None
        因子注册表。默认从全局注册表加载。
    icir_threshold : float
        ICIR 最低阈值（默认 0.5）。
    turnover_threshold : float
        因子周转率上限（默认 1.0，即 100%，不生效）。
    correlation_threshold : float
        因子间最大允许相关度（默认 0.7）。
    factor_cache : dict[str, pd.DataFrame] | None
        预先计算的因子数据帧缓存。

    Returns
    -------
    list[str]
        过滤后的因子 ID 列表，按 ICIR 降序排列。
        如果无法计算则返回空列表（空列表 = 使用全部因子）。
    """
    from aimoon.ml.icir_weighter import load_or_compute_ewma

    if registry is None:
        from aimoon.factors.registry import get_default_registry

        registry = get_default_registry()

    all_ids = registry.list()
    if not all_ids:
        logger.warning("因子注册表为空")
        return []

    logger.info("因子质量过滤开始: %d 个因子待评估", len(all_ids))

    # Step 1: ICIR 权重（EWMA 加权 IC/IR）
    try:
        icir_weights = load_or_compute_ewma(
            panel,
            klines,
            registry,
            factor_cache=factor_cache,
            cache_dir=cache_dir,
        )
    except (ValueError, RuntimeError, KeyError) as e:
        logger.warning("ICIR 权重计算失败: %s", e)
        icir_weights = None

    if not icir_weights:
        logger.warning("无法计算 ICIR 权重，使用全部因子")
        return list(all_ids)

    factor_icir = pd.Series(icir_weights)

    # Step 2: 因子周转率
    try:
        factor_turnover = compute_factor_turnover(
            registry,
            panel,
            n_dates=20,
            factor_cache=factor_cache,
        )
    except (ValueError, RuntimeError, KeyError) as e:
        logger.warning("因子周转率计算失败: %s", e)
        factor_turnover = pd.Series(dtype=float)

    # Step 3: 因子相关性矩阵
    try:
        factor_correlations = compute_factor_correlation(
            registry,
            panel,
            n_dates=20,
            factor_cache=factor_cache,
        )
    except (ValueError, RuntimeError, KeyError) as e:
        logger.warning("因子相关性矩阵计算失败: %s", e)
        factor_correlations = pd.DataFrame()

    # Step 4: 三网关过滤
    filtered_ids = filter_factors(
        factor_icir,
        factor_turnover,
        factor_correlations,
        icir_threshold=icir_threshold,
        turnover_threshold=turnover_threshold,
        correlation_threshold=correlation_threshold,
    )

    logger.info(
        "因子质量过滤完成: %d -> %d 因子",
        len(all_ids),
        len(filtered_ids),
    )
    return filtered_ids


# ── 文件锁（跨进程并行防护） ──

_LOCK_TIMEOUT = 30  # 锁获取超时（秒）
_LOCK_RETRY_INTERVAL = 0.5  # 重试间隔（秒）


def _lock_path(cache_dir: Path) -> Path:
    """返回与白名单文件配套的锁文件路径。"""
    return (cache_dir / "filtered_factor_ids").with_suffix(".lock")


def _acquire_lock(cache_dir: Path) -> int | None:
    """获取文件锁（阻塞，最多 _LOCK_TIMEOUT 秒）。

    使用 O_CREAT|O_EXCL 原子创建锁文件，跨平台兼容。
    锁文件内容为当前主机名+PID，便于调试。

    Returns:
        int | None: 文件描述符（成功），或 None（超时/失败）。
    """
    lock = _lock_path(cache_dir)
    deadline = time.time() + _LOCK_TIMEOUT
    while time.time() < deadline:
        try:
            fd = os.open(
                str(lock),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
            # 写入持有者信息以便调试
            with os.fdopen(fd, "w") as f:
                f.write(f"{socket.gethostname()}:{os.getpid()}\n")
                f.flush()
                os.fsync(f.fileno())
            return fd
        except FileExistsError:
            # 锁已被占用，检查是否过期（stale lock）
            try:
                age = time.time() - lock.stat().st_mtime
                if age > _LOCK_TIMEOUT:
                    logger.warning(
                        "Stale lock file detected (age=%.1fs), removing",
                        age,
                    )
                    lock.unlink()
                    continue
            except OSError:
                pass
            time.sleep(_LOCK_RETRY_INTERVAL)
    logger.warning("Failed to acquire lock after %ds", _LOCK_TIMEOUT)
    return None


def _release_lock(fd: int | None, cache_dir: Path) -> None:
    """释放文件锁。"""
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        _lock_path(cache_dir).unlink(missing_ok=True)
    except OSError:
        pass


def save_filtered_ids(
    factor_ids: list[str],
    ttl_hours: float = _QC_CACHE_TTL_HOURS,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> None:
    """将过滤后的因子 ID 列表持久化到缓存文件。

    Parameters
    ----------
    factor_ids : list[str]
        过滤后的因子 ID 列表。
    ttl_hours : float
        缓存过期时间（小时）。
    cache_dir : Path
        缓存目录路径。
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    whitelist_file = cache_dir / "filtered_factor_ids.json"
    data = {
        "timestamp": time.time(),
        "ttl_hours": ttl_hours,
        "n_factors": len(factor_ids),
        "factor_ids": factor_ids,
    }
    # 原子写入：先写临时文件，再重命名，避免崩溃时文件损坏
    tmp_file = whitelist_file.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp_file.rename(whitelist_file)
    logger.info(
        "因子白名单已缓存: %d 个因子 -> %s (原子写入)",
        len(factor_ids),
        whitelist_file,
    )


def load_filtered_ids(
    force_refresh: bool = False,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> list[str] | None:
    """从缓存加载过滤后的因子 ID 列表。

    Parameters
    ----------
    force_refresh : bool
        强制刷新缓存（返回 None 触发重新计算）。
    cache_dir : Path
        缓存目录路径。

    Returns
    -------
    list[str] | None
        因子 ID 列表。缓存不存在或过期时返回 None。
    """
    cache_dir = Path(cache_dir)
    whitelist_file = cache_dir / "filtered_factor_ids.json"
    if force_refresh or not whitelist_file.exists():
        return None

    try:
        with open(whitelist_file, encoding="utf-8") as f:
            data = json.load(f)

        age_hours = (time.time() - data.get("timestamp", 0)) / 3600
        ttl = data.get("ttl_hours", _QC_CACHE_TTL_HOURS)

        if age_hours > ttl:
            logger.info(
                "因子白名单缓存过期 (%.1fh > %.1fh)，需要重新计算",
                age_hours,
                ttl,
            )
            return None

        factor_ids = data.get("factor_ids", [])
        if not factor_ids:
            return None

        logger.info(
            "使用缓存因子白名单: %d 个因子 (age=%.1fh)",
            len(factor_ids),
            age_hours,
        )
        return factor_ids

    except (json.JSONDecodeError, OSError, ValueError) as e:
        logger.warning("加载因子白名单缓存失败: %s", e)
        return None


def get_or_compute_filtered_ids(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    registry: Registry | None = None,
    *,
    force_refresh: bool = False,
    cache_dir: Path | None = None,
) -> list[str]:
    """获取过滤后的因子 ID 列表（从缓存或实时计算）。

    这是外部调用的主要入口。优先加载缓存，缓存不存在或过期则重新计算。

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo 面板数据。
    klines : dict[str, pd.DataFrame]
        股票 K 线数据。
    registry : Registry | None
        因子注册表。
    force_refresh : bool
        强制重新计算。
    cache_dir : Path | None
        缓存目录路径。

    Returns
    -------
    list[str]
        过滤后的因子 ID 列表。

    Note
    ----
    本函数使用文件锁防止多进程并行计算。如果锁获取失败（超时），
    返回现有的缓存数据（即使已过期），避免阻塞主流程。
    """
    cache_dir = Path(cache_dir) if cache_dir is not None else _DEFAULT_CACHE_DIR

    if not force_refresh:
        cached = load_filtered_ids(cache_dir=cache_dir)
        if cached is not None:
            return cached

    # 获取文件锁，防止多进程同时计算
    lock_fd = _acquire_lock(cache_dir)

    # 获取锁后再次检查缓存（另一个进程可能在等待期间写入了）
    if not force_refresh:
        cached = load_filtered_ids(cache_dir=cache_dir)
        if cached is not None:
            _release_lock(lock_fd, cache_dir)
            return cached

    # 计算所有因子的缓存（一次性）
    if registry is None:
        from aimoon.factors.registry import get_default_registry

        registry = get_default_registry()

    all_ids = registry.list()
    factor_cache: dict[str, pd.DataFrame] = {}
    for alpha_id in all_ids:
        try:
            factor_cache[alpha_id] = registry.compute(alpha_id, panel)
        except (SkipAlphaError, RegistryError):
            continue
        except (ValueError, TypeError, KeyError) as e:
            logger.debug("Factor %s precompute skipped: %s", alpha_id, e)
            continue

    logger.info("全部因子预计算完成: %d / %d", len(factor_cache), len(all_ids))

    filtered_ids = run_quality_filter(
        panel,
        klines,
        registry,
        factor_cache=factor_cache,
        cache_dir=cache_dir,
    )

    if filtered_ids:
        save_filtered_ids(filtered_ids, cache_dir=cache_dir)
    else:
        # 保底：使用全部因子
        filtered_ids = list(all_ids)
        logger.warning("因子过滤返回空列表，使用全部 %d 个因子", len(filtered_ids))

    _release_lock(lock_fd, cache_dir)
    return filtered_ids


def get_filtered_factor_count(cache_dir: Path = _DEFAULT_CACHE_DIR) -> int:
    """返回当前缓存的过滤后因子数量。"""
    cached = load_filtered_ids(cache_dir=cache_dir)
    if cached is not None:
        return len(cached)
    # 保守估计：如果缓存不存在，返回 457（全部因子）
    from aimoon.factors.registry import get_default_registry

    return len(get_default_registry().list())


def select_top_factors_for_ml(
    factor_ids: list[str],
    max_count: int = 60,
) -> list[str]:
    """从过滤后的因子列表中按组选择用于 ML 训练的子集。

    因为输入已经是质量过滤后的因子，按组等比抽样即可保持多样性。

    Parameters
    ----------
    factor_ids : list[str]
        过滤后的因子 ID 列表（ICIR 降序）。
    max_count : int
        最大因子数。

    Returns
    -------
    list[str]
        选中的因子 ID 列表。
    """
    if len(factor_ids) <= max_count:
        return factor_ids

    # 按组（前辍）分组
    groups: dict[str, list[str]] = {}
    for fid in factor_ids:
        group = fid.rsplit("_", 1)[0] if "_" in fid else fid[:4]
        groups.setdefault(group, []).append(fid)

    # 每组等比例分配
    per_group = max(1, max_count // max(len(groups), 1))
    selected: list[str] = []
    for group_ids in groups.values():
        selected.extend(group_ids[:per_group])
        if len(selected) >= max_count:
            break

    return selected[:max_count]
