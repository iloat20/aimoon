"""DataHandler - 数据处理抽象层。

实现 fit/transform 生命周期，确保训练和推理使用相同的归一化参数。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class NormalizationParams:
    """归一化参数。"""

    medians: dict[str, float] = field(default_factory=dict)
    mads: dict[str, float] = field(default_factory=dict)
    scales: dict[str, float] = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)


class DataHandler:
    """数据处理器 - 实现 fit/transform 生命周期。

    用于特征工程和归一化，确保训练和推理使用相同的参数。
    """

    def __init__(self, clip_value: float = 3.0):
        self.clip_value = clip_value
        self.params: NormalizationParams | None = None
        self._is_fitted = False

    def fit(self, panel: dict[str, pd.DataFrame], dates: list[str] | None = None) -> DataHandler:
        """学习归一化参数（在训练数据上）。

        Args:
            panel: 面板数据
            dates: 用于拟合的日期列表（可选）

        Returns:
            self
        """
        from aimoon.factors.registry import get_default_registry
        from aimoon.factors.scorer import compute_alpha_signals

        registry = get_default_registry()
        all_features = []

        # 计算所有因子
        for alpha_id in registry.list():
            try:
                factor_df = registry.compute(alpha_id, panel)
                if dates:
                    factor_df = factor_df.loc[factor_df.index.isin(dates)]
                all_features.append(factor_df)
            except Exception:
                continue

        if not all_features:
            logger.warning("No features computed for fitting")
            return self

        # 合并所有特征
        combined = pd.concat(all_features, axis=1)

        # 计算归一化参数
        medians = {}
        mads = {}
        scales = {}

        for col in combined.columns:
            values = combined[col].dropna()
            if len(values) > 0:
                medians[col] = float(values.median())
                mad = float((values - medians[col]).abs().median())
                mads[col] = max(mad, 1e-10)  # 避免除零
                scales[col] = 1.4826  # MAD 到标准差的转换系数

        self.params = NormalizationParams(
            medians=medians,
            mads=mads,
            scales=scales,
            feature_names=list(combined.columns),
        )
        self._is_fitted = True

        logger.info("DataHandler fitted with %d features", len(combined.columns))
        return self

    def transform(
        self, panel: dict[str, pd.DataFrame], target_date: pd.Timestamp | None = None
    ) -> pd.DataFrame:
        """应用归一化参数（在任何数据上）。

        Args:
            panel: 面板数据
            target_date: 目标日期（可选）

        Returns:
            归一化后的特征 DataFrame
        """
        if not self._is_fitted or self.params is None:
            raise ValueError("DataHandler not fitted. Call fit() first.")

        from aimoon.factors.registry import get_default_registry

        registry = get_default_registry()
        all_features = []

        # 计算所有因子
        for alpha_id in registry.list():
            try:
                factor_df = registry.compute(alpha_id, panel)
                if target_date and target_date in factor_df.index:
                    factor_df = factor_df.loc[[target_date]]
                all_features.append(factor_df)
            except Exception:
                continue

        if not all_features:
            return pd.DataFrame()

        # 合并所有特征
        combined = pd.concat(all_features, axis=1)

        # 应用归一化参数
        normalized = pd.DataFrame(index=combined.index)
        for col in combined.columns:
            if col in self.params.medians:
                median = self.params.medians[col]
                mad = self.params.mads[col]
                scale = self.params.scales[col]

                # Robust z-score: (value - median) / (MAD * scale)
                z_scores = (combined[col] - median) / (mad * scale)
                normalized[col] = z_scores.clip(-self.clip_value, self.clip_value)
            else:
                # 新特征，使用当前数据的统计量
                values = combined[col].dropna()
                if len(values) > 0:
                    median = float(values.median())
                    mad = float((values - median).abs().median())
                    if mad > 1e-10:
                        z_scores = (combined[col] - median) / (1.4826 * mad)
                        normalized[col] = z_scores.clip(-self.clip_value, self.clip_value)
                    else:
                        normalized[col] = 0.0
                else:
                    normalized[col] = 0.0

        return normalized

    def fit_transform(
        self, panel: dict[str, pd.DataFrame], dates: list[str] | None = None
    ) -> pd.DataFrame:
        """拟合并转换（用于训练数据）。"""
        self.fit(panel, dates)
        return self.transform(panel)

    def save(self, path: Path) -> None:
        """保存归一化参数。"""
        if self.params is None:
            raise ValueError("No parameters to save")

        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "medians": self.params.medians,
            "mads": self.params.mads,
            "scales": self.params.scales,
            "feature_names": self.params.feature_names,
            "clip_value": self.clip_value,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info("DataHandler parameters saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> DataHandler:
        """加载归一化参数。"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        handler = cls(clip_value=data.get("clip_value", 3.0))
        handler.params = NormalizationParams(
            medians=data["medians"],
            mads=data["mads"],
            scales=data["scales"],
            feature_names=data["feature_names"],
        )
        handler._is_fitted = True

        logger.info("DataHandler loaded from %s", path)
        return handler
