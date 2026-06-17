"""ML predictor — single LightGBM inference.

Minimal MLPredictor class: load saved model, run inference,
return percentile-ranked ml_score (0-100).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class MLPredictor:
    """Single LightGBM predictor.

    Usage::

        predictor = MLPredictor.load(cache_dir)
        if predictor is None:
            # no model available
            return {}
        scores = predictor.predict(panel, sector_map=..., fundamentals=...)
    """

    def __init__(self) -> None:
        self.model: Any = None
        self.feature_names: list[str] = []
        self.feature_medians: dict[str, float] = {}

    @classmethod
    def load(cls, cache_dir: str | Path) -> MLPredictor | None:
        """Load LightGBM model + feature_names + feature_medians from disk.

        Returns None if the model file is missing.
        """
        cache_path = Path(cache_dir)
        model_path = cache_path / "lgbm_model.txt"
        fn_path = cache_path / "lgbm_feature_names.json"
        fm_path = cache_path / "lgbm_feature_medians.json"

        if not model_path.exists():
            logger.info("No LightGBM model found at %s", model_path)
            return None

        try:
            import lightgbm as lgb  # noqa: PLC0415

            instance = cls()
            instance.model = lgb.Booster(model_file=str(model_path))

            if fn_path.exists():
                with open(fn_path, encoding="utf-8") as f:
                    instance.feature_names = json.load(f)
            if fm_path.exists():
                with open(fm_path, encoding="utf-8") as f:
                    instance.feature_medians = json.load(f)

            logger.info(
                "MLPredictor loaded: %d features, %d medians",
                len(instance.feature_names),
                len(instance.feature_medians),
            )
            return instance
        except Exception as e:
            logger.warning("Failed to load MLPredictor: %s", e)
            return None

    def predict(
        self,
        panel: dict[str, pd.DataFrame],
        sector_map: dict[str, str] | None = None,
        fundamentals: dict[str, pd.DataFrame] | None = None,
    ) -> dict[str, int]:
        """Run inference and return percentile-ranked scores (0-100).

        Args:
            panel: {field: DataFrame(日期 x 股票)} from build_panel.
            sector_map: {code: sector_name} for sector_mom_20d.
            fundamentals: {pe|pb|dividend: DataFrame(日期 x 股票)}.

        Returns:
            dict[code, ml_score (0-100 int)].
            Empty dict if prediction fails.
        """
        from aimoon.ml.feature_pipeline import extract_features  # noqa: PLC0415

        features = extract_features(
            panel,
            sector_map=sector_map,
            fundamentals=fundamentals,
            feature_medians=pd.Series(self.feature_medians) if self.feature_medians else None,
        )
        if features.empty:
            return {}

        # Reindex to match training feature order, fill missing with 0
        X = features.reindex(columns=self.feature_names, fill_value=0.0).astype(float)

        try:
            import lightgbm as lgb  # noqa: PLC0415

            raw = self.model.predict(X.values)
        except Exception as e:
            logger.warning("ML predict failed: %s", e)
            return {}

        pred_series = pd.Series(raw, index=X.index).dropna()
        if pred_series.empty:
            return {}

        # Percentile rank (0-100)
        ranked = pred_series.rank(pct=True, method="average")
        result: dict[str, int] = {}
        for code in ranked.index:
            result[str(code)] = int(round(ranked[code] * 100))

        return result

    def predict_prob(
        self,
        panel: dict[str, pd.DataFrame],
        sector_map: dict[str, str] | None = None,
        fundamentals: dict[str, pd.DataFrame] | None = None,
    ) -> dict[str, float]:
        """Run inference and return raw predictions.

        Same as predict() but returns raw float predictions instead of
        percentile-ranked scores.

        Returns:
            dict[code, raw_prediction (float)].
            Empty dict if prediction fails.
        """
        from aimoon.ml.feature_pipeline import extract_features  # noqa: PLC0415

        features = extract_features(
            panel,
            sector_map=sector_map,
            fundamentals=fundamentals,
            feature_medians=pd.Series(self.feature_medians) if self.feature_medians else None,
        )
        if features.empty:
            return {}

        X = features.reindex(columns=self.feature_names, fill_value=0.0).astype(float)

        try:
            import lightgbm as lgb  # noqa: PLC0415

            raw = self.model.predict(X.values)
        except Exception as e:
            logger.warning("ML predict_prob failed: %s", e)
            return {}

        pred_series = pd.Series(raw, index=X.index).dropna()
        return {str(code): float(val) for code, val in pred_series.items()}
