"""精简训练入口 — 单 LightGBM 训练。

删除旧的 train_ensemble / train_elasticnet_model / train_incremental_dual / ensure_model_fresh 等。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aimoon.factors.ashare import build_panel
from aimoon.ml._training_commons import compute_spearmanr_safe
from aimoon.ml.feature_pipeline import extract_features
from aimoon.ml.label_engine import generate_labels
from aimoon.ml.optimized_config import LGBM_PARAMS, TRAINING_CONFIG
from aimoon.ml.training_loop import run_cv_training_lgbm

logger = logging.getLogger(__name__)


def train_model(
    klines: dict[str, pd.DataFrame],
    panel: dict[str, pd.DataFrame] | None = None,
    save_dir: str | Path | None = None,
    sector_map: dict[str, str] | None = None,
    fundamentals: dict[str, pd.DataFrame] | None = None,
    n_dates: int = 120,
    forward_days: int = 5,
    force: bool = False,
) -> dict[str, Any] | None:
    """Train single LightGBM model with 2-fold Purged TSCV.

    Args:
        klines: {code: kline_df} for label generation.
        panel: {field: DataFrame(日期 x 股票)}. Built from klines if None.
        save_dir: Directory to save artifacts (model, feature_names, medians, meta).
        sector_map: {code: sector_name} for sector_mom_20d.
        fundamentals: {pe|pb|dividend: DataFrame(日期 x 股票)}.
        n_dates: Number of training dates to sample.
        forward_days: Forward return horizon for labels.
        force: Ignored (kept for API compatibility).

    Returns:
        dict with keys: model, feature_names, feature_medians, cv_meta, train_duration.
        None if training fails.
    """
    t0 = time.time()

    params = dict(LGBM_PARAMS)
    training_cfg = dict(TRAINING_CONFIG)

    # Build feature panel from klines
    panel = panel or build_panel(klines, min_rows=n_dates)
    if panel is None:
        logger.error("Cannot build panel for training")
        return None

    close = panel.get("close")
    if close is None:
        logger.error("Panel missing close data")
        return None

    # Select dates evenly across the panel
    all_dates = close.index.tolist()
    if len(all_dates) < n_dates:
        n_dates = len(all_dates)
    step = max(1, len(all_dates) // n_dates)
    selected_dates = [all_dates[i] for i in range(0, len(all_dates), step)][-n_dates:]

    # Collect features + labels for each date
    X_list: list[pd.DataFrame] = []
    y_list: list[pd.Series] = []

    for date in selected_dates:
        features = extract_features(
            panel,
            target_date=date,
            sector_map=sector_map,
            fundamentals=fundamentals,
        )
        if features.empty:
            continue

        labels = generate_labels(klines, date, forward_days)
        common = features.index.intersection(labels.index)
        if len(common) < 5:
            continue

        X_list.append(features.loc[common])
        y_list.append(labels[common])

    if len(X_list) < 2:
        logger.error("Insufficient training data")
        return None

    X = pd.concat(X_list)
    y = pd.concat(y_list)

    # Clip extreme labels
    y = y.clip(-30.0, 30.0)

    # Save feature medians for inference
    feature_medians = X.median()
    feature_names = X.columns.tolist()

    # Train with CV
    final_model, cv_meta = run_cv_training_lgbm(
        X,
        y,
        params,
        n_dates=n_dates,
    )

    # Compute final validation IC on a held-out portion
    val_size = max(1, int(len(y) * 0.2))
    if val_size < len(y):
        X_val = X.iloc[-val_size:]
        y_val = y.iloc[-val_size:]
        preds = final_model.predict(X_val.values)
        val_ic = compute_spearmanr_safe(preds, y_val.values)
        cv_meta["val_ic"] = float(val_ic)

    train_duration = time.time() - t0

    meta = {
        "timestamp": time.time(),
        "train_duration": train_duration,
        "n_stocks": len(y),
        "n_dates": n_dates,
        "forward_days": forward_days,
        "n_features": len(feature_names),
        "mean_ic": cv_meta.get("mean_ic", 0.0),
        "val_ic": cv_meta.get("val_ic", 0.0),
        "fold_ics": cv_meta.get("fold_ics", []),
    }

    # Save artifacts
    save_path = Path(save_dir) if save_dir else Path(".aimoon_cache") / "ml"
    save_path.mkdir(parents=True, exist_ok=True)

    final_model.save_model(str(save_path / "lgbm_model.txt"))

    with open(save_path / "lgbm_feature_names.json", "w", encoding="utf-8") as f:
        json.dump(feature_names, f)

    with open(save_path / "lgbm_feature_medians.json", "w", encoding="utf-8") as f:
        json.dump(feature_medians.to_dict(), f)

    with open(save_path / "lgbm_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    logger.info(
        "LightGBM trained: IC=%.4f, %d stocks, %d dates, %.1fs",
        cv_meta.get("mean_ic", 0.0),
        len(y),
        n_dates,
        train_duration,
    )

    return {
        "model": final_model,
        "feature_names": feature_names,
        "feature_medians": feature_medians.to_dict(),
        "cv_meta": cv_meta,
        "train_duration": train_duration,
    }
