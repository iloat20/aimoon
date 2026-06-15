"""Model artifact saving and loading utilities."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from aimoon.ml._training_commons import save_model_artifacts, save_training_meta  # noqa: F401

logger = logging.getLogger(__name__)


def load_model_artifacts(
    save_path: Path,
    model_filename: str = "xgb_model.json",
    feature_filename: str = "xgb_feature_names.json",
    meta_filename: str = "meta.json",
    model_load_fn: Any = None,
) -> dict[str, Any]:
    """Load model artifacts from disk.

    Parameters
    ----------
    save_path : Path
        Directory containing model artifacts.
    model_filename : str
        Model file name (XGBoost JSON format).
    feature_filename : str
        Feature names JSON file.
    meta_filename : str
        Training metadata JSON file.
    model_load_fn : callable, optional
        Custom model loader, signature ``(path) -> model``.
        If None, uses ``xgb.Booster().load_model()``.

    Returns
    -------
    dict with keys: model, feature_names, meta
    """
    result: dict[str, Any] = {"model": None, "feature_names": [], "meta": {}}

    # Load model
    model_path = save_path / model_filename
    if model_path.exists():
        try:
            if model_load_fn is not None:
                result["model"] = model_load_fn(str(model_path))
            else:
                import xgboost as xgb

                booster = xgb.Booster()
                booster.load_model(str(model_path))
                result["model"] = booster
        except Exception as e:
            logger.warning("Failed to load model from %s: %s", model_path, e)

    # Load feature names
    feature_path = save_path / feature_filename
    if feature_path.exists():
        try:
            with open(feature_path, encoding="utf-8") as f:
                result["feature_names"] = json.load(f)
        except Exception as e:
            logger.warning("Failed to load feature names from %s: %s", feature_path, e)

    # Load metadata
    meta_path = save_path / meta_filename
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                result["meta"] = json.load(f)
        except Exception as e:
            logger.warning("Failed to load meta from %s: %s", meta_path, e)

    return result
