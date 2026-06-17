"""Two-layer stacking ensemble: XGB + LGBM base → LGBM meta → Isotonic calibration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path(".aimoon_cache") / "ml"


class StackingEnsemble:
    """Two-layer stacking ensemble: XGB + LGBM base → LGBM meta → Isotonic calibration.

    Base models generate out-of-fold predictions via PurgedTimeSeriesSplit.
    A LightGBM meta-model learns to combine base predictions.
    Final output is probability-calibrated via IsotonicRegression.
    """

    def __init__(self, cache_dir: str | Path | None = None):
        self._xgb_base: Any = None
        self._lgbm_base: Any = None
        self._meta_model: Any = None
        self._calibrator: Any = None
        self._feature_names: list[str] | None = None
        self._is_fitted: bool = False
        self._cache_dir = (
            Path(cache_dir) / "stacking_native"
            if cache_dir
            else _DEFAULT_CACHE_DIR / "stacking_native"
        )

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        n_splits: int = 5,
        purge_days: int = 5,
        embargo_days: int = 10,
        base_models: dict[str, Any] | None = None,
    ) -> None:
        """Train stacking ensemble with PurgedTimeSeriesSplit.

        Parameters
        ----------
        X : pd.DataFrame
            Feature matrix (samples × features).
        y : pd.Series
            Continuous targets (e.g., forward returns) for regression,
            or binary labels (0/1) for classification.
        n_splits : int
            Number of CV splits.
        purge_days : int
            Purge gap between train/val.
        embargo_days : int
            Embargo after validation set.
        base_models : dict, optional
            Pre-trained base models ``{"xgb": ..., "lgbm": ...}``.
            If provided, skips base model training and uses these directly.
        """
        import lightgbm as lgb
        import xgboost as xgb
        from sklearn.isotonic import IsotonicRegression

        from aimoon.ml.optimized_config import get_stacking_params
        from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

        stacking_cfg = get_stacking_params()
        xgb_params = stacking_cfg["xgb_params"]
        lgbm_params = stacking_cfg["lgbm_params"]

        self._feature_names = list(X.columns)
        cv = PurgedTimeSeriesSplit(
            n_splits=n_splits,
            purge_days=purge_days,
            embargo_days=embargo_days,
        )

        oof_preds_xgb = np.full(len(X), np.nan)
        oof_preds_lgbm = np.full(len(X), np.nan)

        unique_vals = set(y.unique())
        is_binary = unique_vals.issubset({0, 1}) and len(unique_vals) <= 2

        if is_binary:
            xgb_params = stacking_cfg["xgb_params"]
            lgbm_params = stacking_cfg["lgbm_params"]
        else:
            xgb_params = stacking_cfg["xgb_regression_params"]
            lgbm_params = stacking_cfg["lgbm_regression_params"]

        for fold_idx, (train_idx, val_idx) in enumerate(cv.split(X)):
            X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
            y_train, _y_val = y.iloc[train_idx], y.iloc[val_idx]

            try:
                if is_binary:
                    xgb_model = xgb.XGBClassifier(**xgb_params)
                else:
                    xgb_model = xgb.XGBRegressor(**xgb_params)
                xgb_model.fit(X_train, y_train)
                if is_binary:
                    oof_preds_xgb[val_idx] = xgb_model.predict_proba(X_val)[:, 1]
                else:
                    oof_preds_xgb[val_idx] = xgb_model.predict(X_val)
            except Exception as e:
                logger.warning("XGB base fold %d failed: %s", fold_idx, e)

            try:
                if is_binary:
                    lgbm_model = lgb.LGBMClassifier(**lgbm_params)
                else:
                    lgbm_model = lgb.LGBMRegressor(**lgbm_params)
                lgbm_model.fit(X_train, y_train)
                if is_binary:
                    oof_preds_lgbm[val_idx] = lgbm_model.predict_proba(X_val)[:, 1]
                else:
                    oof_preds_lgbm[val_idx] = lgbm_model.predict(X_val)
            except Exception as e:
                logger.warning("LGBM base fold %d failed: %s", fold_idx, e)

            logger.debug(
                "Stacking fold %d: train=%d, val=%d",
                fold_idx,
                len(train_idx),
                len(val_idx),
            )

        valid_mask = ~(np.isnan(oof_preds_xgb) | np.isnan(oof_preds_lgbm))
        if valid_mask.sum() < 30:
            logger.warning("Stacking: insufficient OOF predictions (%d)", valid_mask.sum())
            return

        meta_X = pd.DataFrame(
            {
                "xgb_pred": oof_preds_xgb[valid_mask],
                "lgbm_pred": oof_preds_lgbm[valid_mask],
            }
        )
        meta_y = y.iloc[valid_mask] if isinstance(y, pd.Series) else pd.Series(y[valid_mask])

        if is_binary:
            self._meta_model = lgb.LGBMClassifier(n_estimators=50, verbose=-1)
        else:
            self._meta_model = lgb.LGBMRegressor(n_estimators=50, verbose=-1)
        self._meta_model.fit(meta_X, meta_y)

        try:
            n_cal = max(int(len(meta_X) * 0.2), 20)
            cal_X = meta_X.iloc[-n_cal:]
            cal_y = meta_y.iloc[-n_cal:]
            train_X = meta_X.iloc[:-n_cal]
            train_y = meta_y.iloc[:-n_cal]

            if is_binary:
                self._meta_model = lgb.LGBMClassifier(n_estimators=50, verbose=-1)
            else:
                self._meta_model = lgb.LGBMRegressor(n_estimators=50, verbose=-1)
            self._meta_model.fit(train_X, train_y)
            if is_binary:
                cal_probs = self._meta_model.predict_proba(cal_X)[:, 1]
            else:
                cal_probs = self._meta_model.predict(cal_X)

            self._calibrator = IsotonicRegression(out_of_bounds="clip")
            self._calibrator.fit(cal_probs, cal_y.values)
        except Exception as e:
            logger.warning("Calibrator training failed: %s", e)

        try:
            if is_binary:
                self._xgb_base = xgb.XGBClassifier(**xgb_params)
            else:
                self._xgb_base = xgb.XGBRegressor(**xgb_params)
            self._xgb_base.fit(X, y)
        except Exception as e:
            logger.warning("Full XGB training failed: %s", e)

        try:
            if is_binary:
                self._lgbm_base = lgb.LGBMClassifier(**lgbm_params)
            else:
                self._lgbm_base = lgb.LGBMRegressor(**lgbm_params)
            self._lgbm_base.fit(X, y)
        except Exception as e:
            logger.warning("Full LGBM training failed: %s", e)

        self._is_fitted = True
        logger.info("StackingEnsemble fitted: %d samples, %d features", len(X), len(X.columns))

    def predict(self, X: pd.DataFrame) -> pd.Series:
        """Predict calibrated probabilities.

        Parameters
        ----------
        X : pd.DataFrame
            Feature matrix.

        Returns
        -------
        pd.Series
            index=stock code, value=calibrated probability.
        """
        if not self._is_fitted:
            return pd.Series(dtype=float)

        if self._feature_names:
            X = X.reindex(columns=self._feature_names, fill_value=0.0)

        preds = {}
        if self._xgb_base is not None:
            try:
                if hasattr(self._xgb_base, "predict_proba"):
                    preds["xgb"] = self._xgb_base.predict_proba(X)[:, 1]
                else:
                    preds["xgb"] = self._xgb_base.predict(X)
            except (ValueError, TypeError):
                pass
        if self._lgbm_base is not None:
            try:
                if hasattr(self._lgbm_base, "predict_proba"):
                    preds["lgbm"] = self._lgbm_base.predict_proba(X)[:, 1]
                else:
                    preds["lgbm"] = self._lgbm_base.predict(X)
            except (ValueError, TypeError):
                pass

        if not preds:
            return pd.Series(dtype=float)

        meta_X = pd.DataFrame(preds, index=X.index)

        if self._meta_model is not None:
            try:
                if hasattr(self._meta_model, "predict_proba"):
                    raw_probs = self._meta_model.predict_proba(meta_X)[:, 1]
                else:
                    raw_probs = self._meta_model.predict(meta_X)
            except (ValueError, TypeError):
                raw_probs = meta_X.mean(axis=1).values
        else:
            raw_probs = meta_X.mean(axis=1).values

        if self._calibrator is not None:
            try:
                calibrated = self._calibrator.predict(raw_probs)
            except (ValueError, TypeError):
                calibrated = raw_probs
        else:
            calibrated = raw_probs

        return pd.Series(calibrated, index=X.index)

    def save(self, path: Path | None = None) -> None:
        """Save stacking model using native formats (no pickle).

        Saves XGBoost/LightGBM as their native model files,
        calibrator and metadata as JSON.
        """
        import json as _json

        base = path or self._cache_dir
        base.parent.mkdir(parents=True, exist_ok=True)

        if self._xgb_base is not None:
            self._xgb_base.save_model(str(base / "xgb_base.json"))
        if (
            self._lgbm_base is not None
            and hasattr(self._lgbm_base, "booster_")
            and self._lgbm_base.booster_ is not None
        ):
            self._lgbm_base.booster_.save_model(str(base / "lgbm_base.txt"))
        if (
            self._meta_model is not None
            and hasattr(self._meta_model, "booster_")
            and self._meta_model.booster_ is not None
        ):
            self._meta_model.booster_.save_model(str(base / "meta_model.txt"))

        meta: dict = {
            "feature_names": self._feature_names,
            "is_fitted": self._is_fitted,
        }
        if self._calibrator is not None:
            meta["calibrator_x"] = list(self._calibrator.X_thresholds_)
            meta["calibrator_y"] = list(self._calibrator.y_thresholds_)
            meta["calibrator_min"] = float(self._calibrator.X_min_)
            meta["calibrator_max"] = float(self._calibrator.X_max_)
        with open(base / "meta.json", "w", encoding="utf-8") as f:
            _json.dump(meta, f, indent=2)
        logger.info("StackingEnsemble saved: %s", base)

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        cache_dir: str | Path | None = None,
    ) -> StackingEnsemble | None:
        """Load stacking model from native format files (no pickle)."""
        import json as _json

        base = path or (
            Path(cache_dir) / "stacking_native"
            if cache_dir
            else _DEFAULT_CACHE_DIR / "stacking_native"
        )
        meta_path = base / "meta.json"
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = _json.load(f)
            obj = cls(cache_dir=cache_dir)
            obj._feature_names = meta.get("feature_names")
            obj._is_fitted = meta.get("is_fitted", False)

            xgb_path = base / "xgb_base.json"
            if xgb_path.exists():
                import xgboost as xgb

                obj._xgb_base = xgb.XGBClassifier()
                obj._xgb_base.load_model(str(xgb_path))

            lgbm_path = base / "lgbm_base.txt"
            if lgbm_path.exists():
                import lightgbm as lgb

                obj._lgbm_base = lgb.LGBMClassifier()
                booster = lgb.Booster(model_file=str(lgbm_path))
                obj._lgbm_base._Booster = booster
                obj._lgbm_base.booster_ = booster

            meta_model_path = base / "meta_model.txt"
            if meta_model_path.exists():
                import lightgbm as lgb

                obj._meta_model = lgb.LGBMClassifier()
                booster = lgb.Booster(model_file=str(meta_model_path))
                obj._meta_model._Booster = booster
                obj._meta_model.booster_ = booster

            if "calibrator_x" in meta:
                from sklearn.isotonic import IsotonicRegression

                obj._calibrator = IsotonicRegression(out_of_bounds="clip")
                obj._calibrator.X_thresholds_ = np.array(meta["calibrator_x"])
                obj._calibrator.y_thresholds_ = np.array(meta["calibrator_y"])
                obj._calibrator.X_min_ = meta["calibrator_min"]
                obj._calibrator.X_max_ = meta["calibrator_max"]

            return obj
        except Exception as e:
            logger.warning("Failed to load StackingEnsemble: %s", e)
            return None
