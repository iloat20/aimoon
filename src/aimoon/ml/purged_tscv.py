"""Purged TimeSeriesSplit - 正确实现的时间序列交叉验证

这个模块实现了正确的 Purged TimeSeriesSplit，避免信息泄露。

核心原理：
1. Purge: 从训练集末尾移除 purge_days 天，避免标签泄露
2. Embargo: 在训练集和验证集之间加入 embargo_days 间隔
3. 滚动窗口: 使用滚动窗口验证，更贴近实际使用

参考文献：
- Lopez de Prado, "Advances in Financial Machine Learning" (2018)
- De Prado, M.M.L. (2020) "Machine Learning for Asset Managers"
"""

from __future__ import annotations

import logging
from collections.abc import Generator

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class PurgedTimeSeriesSplit:
    """Purged TimeSeriesSplit - 避免信息泄露的时间序列交叉验证

    Args:
        n_splits: 折数（默认 5）
        purge_days: 清洗天数（默认 5）
        embargo_days: 禁运天数（默认 10）

    Example:
        >>> import pandas as pd
        >>> from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit
        >>>
        >>> # 创建示例数据
        >>> dates = pd.date_range('2020-01-01', periods=100, freq='D')
        >>> X = pd.DataFrame({'feature': range(100)}, index=dates)
        >>> y = pd.Series(range(100), index=dates)
        >>>
        >>> # 创建分割器
        >>> tscv = PurgedTimeSeriesSplit(n_splits=5, purge_days=5, embargo_days=10)
        >>>
        >>> # 遍历分割
        >>> for train_idx, val_idx in tscv.split(X):
        ...     print(f"Train: {len(train_idx)}, Val: {len(val_idx)}")
    """

    def __init__(
        self,
        n_splits: int = 5,
        purge_days: int = 5,
        embargo_days: int = 10,
    ):
        self.n_splits = n_splits
        self.purge_days = purge_days
        self.embargo_days = embargo_days

    def split(
        self,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray | None = None,
        groups: np.ndarray | None = None,
        date_column: str | None = None,
    ) -> Generator[tuple[np.ndarray, np.ndarray], None, None]:
        """生成训练集和验证集的索引

        Args:
            X: 特征矩阵（如果 index 是 DatetimeIndex，将使用日期计算 purge/embargo）
            y: 标签（可选）
            groups: 分组（可选）
            date_column: 日期列名（如果提供，使用该列进行日期分割）

        Yields:
            tuple: (训练集索引, 验证集索引)
        """
        n_samples = len(X)

        # 优先使用 date_column 参数
        dates: pd.DatetimeIndex | None = None
        if date_column and isinstance(X, pd.DataFrame) and date_column in X.columns:
            dates = pd.to_datetime(X[date_column])
            is_datetime = True
        elif isinstance(X, pd.DataFrame):
            # 检测是否使用日期索引
            is_datetime = isinstance(X.index, pd.DatetimeIndex)
            dates = X.index if is_datetime else None  # type: ignore[assignment]
        else:
            is_datetime = False

        if is_datetime:
            # 确保 dates 是 DatetimeIndex
            if not isinstance(dates, pd.DatetimeIndex):
                dates = pd.DatetimeIndex(dates)

            # M1: 使用交易日数而非日历日数计算 fold 大小
            # 在A股中，交易日间隔≈1，日历日需转换为交易日
            n_trading_days = len(dates)
            fold_size = n_trading_days // (self.n_splits + 1)

            if fold_size < self.purge_days + self.embargo_days:
                logger.warning(
                    "Fold size (%d trading days) is smaller than purge (%d) + embargo (%d). "
                    "Consider reducing n_splits or increasing data size.",
                    fold_size,
                    self.purge_days,
                    self.embargo_days,
                )

            for i in range(self.n_splits):
                # M1: 基于交易日索引（而非日历日）计算边界
                fold_end_idx = fold_size * (i + 1)
                purge_end_idx = max(0, fold_end_idx - self.purge_days)
                embargo_start_idx = min(
                    n_trading_days - 1, fold_end_idx + self.embargo_days
                )
                val_end_idx = min(
                    n_trading_days, embargo_start_idx + fold_size
                )

                train_mask = np.zeros(n_trading_days, dtype=bool)
                train_mask[:purge_end_idx] = True
                val_mask = np.zeros(n_trading_days, dtype=bool)
                val_mask[embargo_start_idx:val_end_idx] = True

                train_idx = np.where(train_mask)[0]
                val_idx = np.where(val_mask)[0]

                if len(train_idx) == 0 or len(val_idx) == 0:
                    logger.debug(
                        "Skipping fold %d: train (%d) or val (%d) is empty",
                        i,
                        len(train_idx),
                        len(val_idx),
                    )
                    continue

                logger.debug(
                    "Fold %d (date-based): train=[0:%d], val=[%d:%d], "
                    "purge=%d days, embargo=%d days",
                    i,
                    len(train_idx),
                    len(val_idx),
                    self.purge_days,
                    self.embargo_days,
                )

                yield train_idx, val_idx
        else:
            # 回退：按行数计算（原有逻辑）
            fold_size = n_samples // (self.n_splits + 1)

            if fold_size < self.purge_days + self.embargo_days:
                logger.warning(
                    "Fold size (%d) is smaller than purge (%d) + embargo (%d). "
                    "Consider reducing n_splits or increasing data size.",
                    fold_size,
                    self.purge_days,
                    self.embargo_days,
                )

            for i in range(self.n_splits):
                train_end = fold_size * (i + 1)
                train_end_purged = train_end - self.purge_days
                val_start = train_end + self.embargo_days
                val_end = val_start + fold_size

                if val_end > n_samples:
                    logger.debug(
                        "Skipping fold %d: val_end (%d) > n_samples (%d)",
                        i,
                        val_end,
                        n_samples,
                    )
                    break

                if train_end_purged < 0:
                    logger.debug(
                        "Skipping fold %d: train_end_purged (%d) < 0",
                        i,
                        train_end_purged,
                    )
                    continue

                train_idx = np.arange(0, train_end_purged)
                val_idx = np.arange(val_start, val_end)

                logger.debug(
                    "Fold %d: train=[0:%d], val=[%d:%d], purge=%d, embargo=%d",
                    i,
                    train_end_purged,
                    val_start,
                    val_end,
                    self.purge_days,
                    self.embargo_days,
                )

                yield train_idx, val_idx

    def get_n_splits(self) -> int:
        """返回折数"""
        return self.n_splits


class CombinatorialPurgedCV:
    """组合式 Purged 交叉验证

    使用组合式分割，更充分利用数据。

    Args:
        n_splits: 基础折数
        n_test_splits: 测试折数
        purge_days: 清洗天数
        embargo_days: 禁运天数

    Example:
        >>> tscv = CombinatorialPurgedCV(n_splits=6, n_test_splits=2)
        >>> # 生成 C(6,2) = 15 种组合
    """

    def __init__(
        self,
        n_splits: int = 6,
        n_test_splits: int = 2,
        purge_days: int = 5,
        embargo_days: int = 10,
    ):
        self.n_splits = n_splits
        self.n_test_splits = n_test_splits
        self.purge_days = purge_days
        self.embargo_days = embargo_days

    def split(
        self,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray | None = None,
    ) -> Generator[tuple[np.ndarray, np.ndarray], None, None]:
        """生成组合式分割

        从 n_splits 折中选择 n_test_splits 折作为测试集，
        其余作为训练集。
        """
        from itertools import combinations

        n_samples = len(X)
        dates: pd.DatetimeIndex | None = None
        if isinstance(X, pd.DataFrame) and isinstance(X.index, pd.DatetimeIndex):
            dates = X.index

        if dates is not None:
            total_days = (dates[-1] - dates[0]).days
            fold_days = total_days // self.n_splits

            # 生成所有可能的测试折组合
            test_fold_combinations = list(
                combinations(range(self.n_splits), self.n_test_splits)
            )

            for test_folds in test_fold_combinations:
                train_folds = [f for f in range(self.n_splits) if f not in test_folds]
                train_idx: list[int] = []
                val_idx: list[int] = []

                for fold_idx in train_folds:
                    fold_start = dates[0] + pd.Timedelta(days=fold_days * fold_idx)
                    fold_end = fold_start + pd.Timedelta(days=fold_days)
                    purge_end = fold_end - pd.Timedelta(days=self.purge_days)
                    mask = (dates >= fold_start) & (dates < purge_end)
                    train_idx.extend(np.where(mask)[0])

                for fold_idx in test_folds:
                    fold_start = dates[0] + pd.Timedelta(days=fold_days * fold_idx)
                    embargo_start = fold_start + pd.Timedelta(days=self.embargo_days)
                    fold_end = fold_start + pd.Timedelta(days=fold_days)
                    mask = (dates >= embargo_start) & (dates < fold_end)
                    val_idx.extend(np.where(mask)[0])

                if train_idx and val_idx:
                    yield np.array(train_idx), np.array(val_idx)
        else:
            # 回退：按行数计算（原有逻辑）
            fold_size = n_samples // self.n_splits

            test_fold_combinations = list(
                combinations(range(self.n_splits), self.n_test_splits)
            )

            for test_folds in test_fold_combinations:
                train_folds = [f for f in range(self.n_splits) if f not in test_folds]
                train_idx = []
                val_idx = []

                for fold_idx in train_folds:
                    start = fold_idx * fold_size
                    end = start + fold_size - self.purge_days
                    train_idx.extend(range(start, max(start, end)))

                for fold_idx in test_folds:
                    start = fold_idx * fold_size + self.embargo_days
                    end = min(start + fold_size, n_samples)
                    val_idx.extend(range(start, end))

                if train_idx and val_idx:
                    yield np.array(train_idx), np.array(val_idx)


def create_purged_cv(
    method: str = "standard",
    n_splits: int = 5,
    purge_days: int = 5,
    embargo_days: int = 10,
    **kwargs,
):
    """创建 Purged 交叉验证对象

    Args:
        method: 方法 ("standard" 或 "combinatorial")
        n_splits: 折数
        purge_days: 清洗天数
        embargo_days: 禁运天数
        **kwargs: 其他参数

    Returns:
        PurgedTimeSeriesSplit 或 CombinatorialPurgedCV
    """
    if method == "standard":
        return PurgedTimeSeriesSplit(
            n_splits=n_splits,
            purge_days=purge_days,
            embargo_days=embargo_days,
        )
    elif method == "combinatorial":
        n_test_splits = kwargs.get("n_test_splits", 2)
        return CombinatorialPurgedCV(
            n_splits=n_splits,
            n_test_splits=n_test_splits,
            purge_days=purge_days,
            embargo_days=embargo_days,
        )
    else:
        raise ValueError(f"Unknown method: {method}")


def validate_cv_results(
    train_indices: list[np.ndarray],
    val_indices: list[np.ndarray],
    min_train_size: int = 100,
    min_val_size: int = 20,
) -> dict:
    """验证交叉验证结果

    Args:
        train_indices: 训练集索引列表
        val_indices: 验证集索引列表
        min_train_size: 最小训练集大小
        min_val_size: 最小验证集大小

    Returns:
        dict: 验证结果
    """
    issues = []

    for i, (train_idx, val_idx) in enumerate(zip(train_indices, val_indices)):
        # 检查大小
        if len(train_idx) < min_train_size:
            issues.append(f"Fold {i}: 训练集太小 ({len(train_idx)} < {min_train_size})")

        if len(val_idx) < min_val_size:
            issues.append(f"Fold {i}: 验证集太小 ({len(val_idx)} < {min_val_size})")

        # 检查重叠
        overlap = set(train_idx) & set(val_idx)
        if overlap:
            issues.append(f"Fold {i}: 训练集和验证集有重叠 ({len(overlap)} 个样本)")

        # 检查顺序
        if len(train_idx) > 0 and len(val_idx) > 0:
            if max(train_idx) >= min(val_idx):
                issues.append(f"Fold {i}: 训练集和验证集顺序错误")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "n_folds": len(train_indices),
        "avg_train_size": np.mean([len(idx) for idx in train_indices]),
        "avg_val_size": np.mean([len(idx) for idx in val_indices]),
    }
