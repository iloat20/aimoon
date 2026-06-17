"""DataHandler - 数据处理抽象层。

实现 fit/transform 生命周期，确保训练和推理使用相同的归一化参数。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson
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
        self._factor_cache: dict[str, pd.DataFrame] = {}
        self._selected_ids: list[str] = []
        self._factor_panel_id: int = 0

    def fit(
        self,
        panel: dict[str, pd.DataFrame],
        dates: list[str] | None = None,
        icir_min: float = 0.02,
        icir_threshold: float = 0.3,
    ) -> DataHandler:
        """学习归一化参数（在训练数据上）。

        Args:
            panel: 面板数据
            dates: 用于拟合的日期列表（可选）
            icir_min: |IC| 均值阈值，低于此值的因子被过滤
            icir_threshold: ICIR 阈值，低于此值的因子被过滤

        Returns:
            self
        """
        from aimoon.factors.registry import get_default_registry

        registry = get_default_registry()
        all_features = []

        # ICIR 预筛选：仅保留 |IC| > icir_min 且 ICIR > icir_threshold 的因子
        selected_ids = list(registry.list())
        if len(panel.get("close", pd.DataFrame())) > 60 and icir_min > 0:
            selected_ids = self._icir_prescreen(panel, registry, icir_min, icir_threshold)

        # 计算筛选后的因子
        for alpha_id in selected_ids:
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

    def _icir_prescreen(
        self,
        panel: dict[str, pd.DataFrame],
        registry: Any,
        icir_min: float = 0.02,
        icir_threshold: float = 0.3,
    ) -> list[str]:
        """ICIR 预筛选：保留信息系数显著的因子。

        对最近 90 个交易日计算每个因子的 Rank IC 和 ICIR，
        仅保留 |IC| > icir_min 且 |ICIR| > icir_threshold 的因子。
        """
        import numpy as np

        close = panel.get("close")
        if close is None or len(close) < 60:
            return list(registry.list())

        # 计算未来 5 日收益率截面
        future_ret = close.pct_change(5).shift(-5)
        n_days = min(90, len(close) - 10)
        if n_days < 30:
            return list(registry.list())

        selected: list[str] = []
        all_ids = registry.list()
        checked = 0

        for alpha_id in all_ids:
            try:
                factor_df = registry.compute(alpha_id, panel)
                if factor_df is None or factor_df.empty:
                    continue

                # 取最近 n_days 天的数据
                f_slice = factor_df.iloc[-n_days:]
                ret_slice = future_ret.iloc[-n_days:]

                # 计算每天的截面 Rank IC
                ic_values = []
                for date in f_slice.index:
                    if date not in ret_slice.index:
                        continue
                    f_row = f_slice.loc[date]
                    r_row = ret_slice.loc[date]
                    common = f_row.dropna().index.intersection(r_row.dropna().index)
                    if len(common) < 10:
                        continue
                    f_vals = f_row[common].values.astype(np.float64)
                    r_vals = r_row[common].values.astype(np.float64)
                    # Rank IC = Spearman 相关系数
                    f_rank = np.argsort(np.argsort(f_vals))
                    r_rank = np.argsort(np.argsort(r_vals))
                    n = len(f_rank)
                    if n < 3:
                        continue
                    ic = 1.0 - 6.0 * np.sum((f_rank - r_rank) ** 2) / (n * (n**2 - 1))
                    ic_values.append(ic)

                if len(ic_values) < 10:
                    continue

                ic_mean = np.mean(ic_values)
                ic_std = np.std(ic_values, ddof=1)
                icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0

                if abs(ic_mean) > icir_min and abs(icir) > icir_threshold:
                    selected.append(alpha_id)

                checked += 1
            except Exception:
                continue

        if len(selected) < 5:
            # 筛选太严，回退到全部
            return list(registry.list())

        return selected

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
        with open(path, "wb") as f:
            f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE))

        logger.info("DataHandler parameters saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> DataHandler:
        """加载归一化参数。"""
        data = orjson.loads(path.read_bytes())

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
