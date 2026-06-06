"""Walk-Forward Validation Framework - 滚动窗口验证框架。

实现 Qlib 风格的滚动窗口验证：
- Train on [t0, t1], predict on [t1, t2]
- Roll forward by step_size
- Aggregate predictions across all windows
- Report time-varying model performance
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalkForwardResult:
    """Walk-Forward 验证结果。"""
    predictions: pd.Series
    actual_returns: pd.Series
    window_metrics: list[dict[str, float]]
    overall_metrics: dict[str, float]
    n_windows: int
    train_size: int
    test_size: int
    step_size: int


class WalkForwardValidator:
    """Walk-Forward 验证器。

    实现滚动窗口验证，防止前瞻偏差。
    """

    def __init__(
        self,
        model: Any,
        feature_extractor: Any,
        train_size: int = 250,
        test_size: int = 20,
        step_size: int = 20,
        purge_days: int = 5,
    ):
        """
        Args:
            model: 模型对象（需实现 fit/predict）
            feature_extractor: 特征提取器
            train_size: 训练窗口大小（交易日）
            test_size: 测试窗口大小（交易日）
            step_size: 滚动步长（交易日）
            purge_days: 清洗天数（防止前瞻偏差）
        """
        self.model = model
        self.feature_extractor = feature_extractor
        self.train_size = train_size
        self.test_size = test_size
        self.step_size = step_size
        self.purge_days = purge_days

    def validate(
        self,
        panel: dict[str, pd.DataFrame],
        klines: dict[str, pd.DataFrame],
        forward_days: int = 5,
    ) -> WalkForwardResult:
        """执行 Walk-Forward 验证。

        Args:
            panel: 面板数据
            klines: K 线数据
            forward_days: 前瞻天数

        Returns:
            验证结果
        """
        from aimoon.ml.label_engine import generate_rank_labels

        close = panel.get("close")
        if close is None:
            raise ValueError("Panel must contain 'close' data")

        all_dates = close.index.tolist()
        n_dates = len(all_dates)

        if n_dates < self.train_size + self.test_size + self.purge_days:
            raise ValueError(
                f"Insufficient data: {n_dates} dates < {self.train_size} + {self.test_size} + {self.purge_days}"
            )

        # 计算窗口数量
        n_windows = (n_dates - self.train_size - self.test_size - self.purge_days) // self.step_size + 1
        logger.info(f"Walk-Forward validation: {n_windows} windows, train={self.train_size}, test={self.test_size}, step={self.step_size}")

        # 存储结果
        all_predictions = []
        all_actual_returns = []
        window_metrics = []

        for window_idx in range(n_windows):
            # 计算窗口边界
            train_start = window_idx * self.step_size
            train_end = train_start + self.train_size
            test_start = train_end + self.purge_days
            test_end = test_start + self.test_size

            if test_end > n_dates:
                break

            # 提取日期
            train_dates = all_dates[train_start:train_end]
            test_dates = all_dates[test_start:test_end]

            logger.info(f"Window {window_idx + 1}/{n_windows}: train={train_dates[0]} to {train_dates[-1]}, test={test_dates[0]} to {test_dates[-1]}")

            # 训练模型
            try:
                X_train, y_train = self._prepare_data(
                    panel, klines, train_dates, forward_days
                )
                if len(X_train) < 10:
                    logger.warning(f"Window {window_idx + 1}: insufficient training data ({len(X_train)} samples)")
                    continue

                self.model.fit(X_train, y_train)
            except Exception as e:
                logger.error(f"Window {window_idx + 1}: training failed: {e}")
                continue

            # 测试模型
            try:
                X_test, y_test = self._prepare_data(
                    panel, klines, test_dates, forward_days
                )
                if len(X_test) < 5:
                    logger.warning(f"Window {window_idx + 1}: insufficient test data ({len(X_test)} samples)")
                    continue

                predictions = self.model.predict(X_test)

                # 存储结果
                for i, (code, pred) in enumerate(zip(X_test.index, predictions)):
                    all_predictions.append({
                        'date': test_dates[i % len(test_dates)],
                        'code': code,
                        'prediction': float(pred),
                    })
                    if code in y_test.index:
                        all_actual_returns.append({
                            'date': test_dates[i % len(test_dates)],
                            'code': code,
                            'actual_return': float(y_test[code]),
                        })

                # 计算窗口指标
                window_ic = self._calculate_ic(predictions, y_test)
                window_metrics.append({
                    'window': window_idx + 1,
                    'train_start': str(train_dates[0]),
                    'train_end': str(train_dates[-1]),
                    'test_start': str(test_dates[0]),
                    'test_end': str(test_dates[-1]),
                    'ic': window_ic,
                    'n_samples': len(X_test),
                })

                logger.info(f"Window {window_idx + 1}: IC={window_ic:.4f}, samples={len(X_test)}")

            except Exception as e:
                logger.error(f"Window {window_idx + 1}: testing failed: {e}")
                continue

        # 汇总结果
        if not all_predictions:
            raise ValueError("Walk-Forward validation failed: no valid predictions")

        predictions_series = pd.Series(
            [p['prediction'] for p in all_predictions],
            index=pd.MultiIndex.from_tuples(
                [(p['date'], p['code']) for p in all_predictions],
                names=['date', 'code']
            )
        )

        actual_returns_series = pd.Series(
            [r['actual_return'] for r in all_actual_returns],
            index=pd.MultiIndex.from_tuples(
                [(r['date'], r['code']) for r in all_actual_returns],
                names=['date', 'code']
            )
        )

        # 计算整体指标
        overall_ic = self._calculate_ic(
            predictions_series.values,
            actual_returns_series.reindex(predictions_series.index).values
        )

        overall_metrics = {
            'ic': overall_ic,
            'n_windows': len(window_metrics),
            'n_predictions': len(all_predictions),
            'avg_window_ic': np.mean([m['ic'] for m in window_metrics]) if window_metrics else 0.0,
            'std_window_ic': np.std([m['ic'] for m in window_metrics]) if window_metrics else 0.0,
        }

        logger.info(f"Walk-Forward validation complete: IC={overall_ic:.4f}, windows={len(window_metrics)}")

        return WalkForwardResult(
            predictions=predictions_series,
            actual_returns=actual_returns_series,
            window_metrics=window_metrics,
            overall_metrics=overall_metrics,
            n_windows=len(window_metrics),
            train_size=self.train_size,
            test_size=self.test_size,
            step_size=self.step_size,
        )

    def _prepare_data(
        self,
        panel: dict[str, pd.DataFrame],
        klines: dict[str, pd.DataFrame],
        dates: list,
        forward_days: int,
    ) -> tuple[pd.DataFrame, pd.Series]:
        """准备训练/测试数据。"""
        from aimoon.ml.feature_pipeline import extract_features
        from aimoon.ml.label_engine import generate_rank_labels

        # 提取特征
        features_list = []
        labels_list = []

        for date in dates:
            features = extract_features(panel, target_date=date)
            labels = generate_rank_labels(klines, date, forward_days)

            if not features.empty and not labels.empty:
                common = features.index.intersection(labels.index)
                if len(common) >= 10:
                    features_list.append(features.loc[common])
                    labels_list.append(labels.loc[common])

        if not features_list:
            return pd.DataFrame(), pd.Series(dtype=float)

        X = pd.concat(features_list, axis=0)
        y = pd.concat(labels_list, axis=0)

        # 去重（同一股票可能在多个日期出现）
        X = X[~X.index.duplicated(keep='last')]
        y = y[~y.index.duplicated(keep='last')]

        # 对齐
        common = X.index.intersection(y.index)
        X = X.loc[common]
        y = y.loc[common]

        return X, y

    def _calculate_ic(self, predictions: np.ndarray, actual_returns: np.ndarray) -> float:
        """计算信息系数（IC）。"""
        from scipy.stats import spearmanr

        # 移除 NaN
        mask = ~(np.isnan(predictions) | np.isnan(actual_returns))
        if mask.sum() < 10:
            return 0.0

        predictions = predictions[mask]
        actual_returns = actual_returns[mask]

        try:
            ic, _ = spearmanr(predictions, actual_returns)
            return float(ic) if not np.isnan(ic) else 0.0
        except Exception:
            return 0.0
