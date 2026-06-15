"""Two Sigma 风格集成预测器 — XGBoost + LightGBM 加权平均。

集成多个异构模型的预测，输出连续预测值用于组合优化。
"""

from __future__ import annotations

import json
import logging
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import spearmanr

from aimoon.ml.feature_pipeline import extract_features
from aimoon.ml.icir_weighter import EWMAFactorWeighter, compute_factor_ic_series
from aimoon.ml.label_engine import generate_realized_returns
from aimoon.models import Signal

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path(".aimoon_cache") / "ml"


@dataclass
class EnsemblePredictor:
    """XGBoost + LightGBM 集成预测器。

    对同一特征矩阵分别用两个模型预测，然后加权平均。
    权重基于各模型在验证集上的 IC 动态计算。
    """

    def __init__(
        self,
        xgb_model: Any = None,
        lgbm_model: Any = None,
        en_model: Any = None,
        en_scaler: Any = None,
        feature_names: list[str] | None = None,
        xgb_weight: float = 0.5,
        lgbm_weight: float = 0.5,
        en_weight: float = 0.0,
        feature_medians: pd.Series | None = None,  # M5: 用于填充缺失特征
        zoo_factor_ids: list[str] | None = None,  # 训练时因子子集，确保回测一致
        cache_dir: str | Path | None = None,  # ML 缓存目录
    ):
        self._xgb = xgb_model
        self._lgbm = lgbm_model
        self._en = en_model
        self._en_scaler = en_scaler
        self._feature_names = feature_names
        self._xgb_weight = xgb_weight
        self._lgbm_weight = lgbm_weight
        self._en_weight = en_weight
        self._feature_medians = feature_medians
        self._zoo_factor_ids = zoo_factor_ids
        self._cache_dir = Path(cache_dir) / "ml" if cache_dir else _DEFAULT_CACHE_DIR

    @classmethod
    def from_cache(cls, cache_dir: str | Path | None = None) -> EnsemblePredictor:
        """从缓存目录加载集成模型。"""
        import lightgbm as lgb
        import xgboost as xgb

        cache = Path(cache_dir) / "ml" if cache_dir else _DEFAULT_CACHE_DIR

        # 优先加载规范特征名文件（合并训练时所有模型使用的特征）
        canonical_fn = cache / "canonical_feature_names.json"
        xgb_fn = cache / "xgb_feature_names.json"
        lgbm_fn = cache / "lgbm_feature_names.json"
        en_fn = cache / "elasticnet_feature_names.json"
        feature_names = None
        if canonical_fn.exists():
            with open(canonical_fn, encoding="utf-8") as f:
                feature_names = json.load(f)
            logger.info(
                "Loaded canonical feature names: %d features", len(feature_names)
            )
        elif xgb_fn.exists():
            with open(xgb_fn, encoding="utf-8") as f:
                feature_names = json.load(f)
        elif lgbm_fn.exists():
            with open(lgbm_fn, encoding="utf-8") as f:
                feature_names = json.load(f)
        elif en_fn.exists():
            with open(en_fn, encoding="utf-8") as f:
                feature_names = json.load(f)

        # Load XGBoost model
        xgb_model = None
        xgb_path = cache / "xgb_model.json"
        if xgb_path.exists():
            try:
                xgb_model = xgb.Booster()
                xgb_model.load_model(str(xgb_path))
            except Exception as e:
                logger.warning("Failed to load XGBoost model: %s", e)

        # Load LightGBM model
        lgbm_model = None
        lgbm_path = cache / "lgbm_model.txt"
        if lgbm_path.exists():
            try:
                booster = lgb.Booster(model_file=str(lgbm_path))
                lgbm_model = booster
            except Exception as e:
                logger.warning("Failed to load LightGBM model: %s", e)

        # Load Elastic Net model
        en_model = None
        en_scaler = None
        en_path = cache / "model.elasticnet.json"
        if en_path.exists():
            try:
                with open(en_path, encoding="utf-8") as f:
                    en_data = json.load(f)
                from sklearn.linear_model import ElasticNet
                from sklearn.preprocessing import StandardScaler

                en_model = ElasticNet()
                en_model.coef_ = np.array(en_data["coef"], dtype=np.float64)
                en_model.intercept_ = float(en_data["intercept"])
                en_model.n_features_in_ = en_data.get(
                    "n_features_in", len(en_data["coef"]))

                en_scaler = StandardScaler()
                en_scaler.mean_ = np.array(en_data["scaler_mean"], dtype=np.float64)
                en_scaler.scale_ = np.array(en_data["scaler_scale"], dtype=np.float64)
                en_scaler.var_ = np.array(en_data["scaler_var"], dtype=np.float64)
                en_scaler.n_features_in_ = len(en_data["scaler_mean"])
            except Exception as e:
                logger.warning("Failed to load Elastic Net model: %s", e)

        # Load ensemble weights from meta
        xgb_weight, lgbm_weight, en_weight = 0.5, 0.5, 0.0
        _xgb_ic, _lgbm_ic = 0.0, 0.0
        ensemble_meta = cache / "ensemble_meta.json"
        if ensemble_meta.exists():
            try:
                with open(ensemble_meta, encoding="utf-8") as f:
                    meta = json.load(f)
                xgb_weight = meta.get("xgb_weight", 0.5)
                lgbm_weight = meta.get("lgbm_weight", 0.5)
                en_weight = meta.get("en_weight", 0.0)
                _xgb_ic = meta.get("xgb_ic", 0.0)
                _lgbm_ic = meta.get("lgbm_ic", 0.0)
            except (TypeError, AttributeError):
                logger.warning("Failed to load ensemble meta from %s", ensemble_meta)

        # Load zoo_factor_ids from ensemble_meta
        zoo_factor_ids = None
        if ensemble_meta.exists():
            try:
                meta_zoo = json.load(open(ensemble_meta, encoding="utf-8"))
                zoo_factor_ids = meta_zoo.get("zoo_factor_ids")
            except (json.JSONDecodeError, OSError, TypeError):
                logger.debug("Failed to load zoo_factor_ids from %s", ensemble_meta)

        return cls(
            xgb_model=xgb_model,
            lgbm_model=lgbm_model,
            en_model=en_model,
            en_scaler=en_scaler,
            feature_names=feature_names,
            xgb_weight=xgb_weight,
            lgbm_weight=lgbm_weight,
            en_weight=en_weight,
            zoo_factor_ids=zoo_factor_ids,
            cache_dir=cache_dir,
        )

    def predict(
        self,
        panel: dict[str, pd.DataFrame],
        registry: Any = None,
        sector_map: dict[str, str] | None = None,
    ) -> pd.Series:
        """集成预测：XGBoost + LightGBM 加权平均。

        Returns
        -------
        pd.Series
            index=stock code, value=predicted return (continuous).
        """
        # 处理 panel 为 None 或空的情况
        if panel is None or not isinstance(panel, dict) or len(panel) == 0:
            return pd.Series(dtype=float)

        # 从 panel 推断目标日期（最后一根 K 线日期）
        close = panel.get("close")
        target_date = close.index[-1] if close is not None and len(close) > 0 else None

        features = extract_features(
            panel, registry, target_date=target_date, sector_map=sector_map,
            zoo_factor_ids=self._zoo_factor_ids,
        )
        if features.empty:
            return pd.Series(dtype=float)

        # Reindex to expected features + 维度对齐验证
        if self._feature_names:
            missing = set(self._feature_names) - set(features.columns)
            if missing:
                missing_pct = len(missing) / len(self._feature_names) * 100
                if missing_pct > 50:
                    logger.warning(
                        "特征维度严重不匹配: 缺失 %d/%d 个特征 (%.0f%%) — "
                        "模型可能已过时，建议重新训练",
                        len(missing),
                        len(self._feature_names),
                        missing_pct,
                    )
                else:
                    logger.info(
                        "特征对齐: %d 个缺失特征以中位数填充 (%.0f%%)",
                        len(missing),
                        missing_pct,
                    )
                # M5: 用训练集中位数填充缺失特征（而非0）
                if self._feature_medians is not None:
                    medians = self._feature_medians.reindex(
                        list(missing), fill_value=0.0
                    )
                    for col in missing:
                        features[col] = medians.get(col, 0.0)
                else:
                    for col in missing:
                        features[col] = 0.0
            features = features.reindex(columns=self._feature_names)

        predictions: dict[str, np.ndarray] = {}

        # XGBoost prediction
        if self._xgb is not None:
            try:
                import xgboost as xgb

                dmatrix = xgb.DMatrix(features)
                predictions["xgb"] = self._xgb.predict(dmatrix)
            except Exception as e:
                logger.warning("XGBoost predict failed: %s", e)

        # LightGBM prediction — normalize output to 1D array
        if self._lgbm is not None:
            try:
                raw = self._lgbm.predict(features)
                predictions["lgbm"] = np.asarray(raw).ravel()
            except Exception as e:
                logger.warning("LightGBM predict failed: %s", e)

        # Elastic Net prediction
        if self._en is not None and self._en_scaler is not None:
            try:
                if self._feature_names:
                    features_en = features.reindex(
                        columns=self._feature_names, fill_value=0.0
                    )
                else:
                    features_en = features
                en_scaled = self._en_scaler.transform(features_en.values)
                predictions["en"] = self._en.predict(en_scaled)
            except Exception as e:
                logger.warning("Elastic Net predict failed: %s", e)

        if not predictions:
            return pd.Series(dtype=float)

        # Weighted average of available models
        active_weights: dict[str, float] = {}
        for name in predictions:
            if name == "xgb":
                active_weights[name] = self._xgb_weight
            elif name == "lgbm":
                active_weights[name] = self._lgbm_weight
            elif name == "en":
                active_weights[name] = self._en_weight

        total_weight = sum(active_weights.values())
        if total_weight <= 0:
            return pd.Series(dtype=float)

        combined = np.zeros(len(features))
        for name, weight in active_weights.items():
            combined += (weight / total_weight) * predictions[name]

        result = pd.Series(combined, index=features.index).dropna()
        logger.info(
            "Ensemble predict: %d stocks, xgb_w=%.2f, lgbm_w=%.2f",
            len(result),
            self._xgb_weight,
            self._lgbm_weight,
        )
        return result

    @property
    def has_xgb(self) -> bool:
        return self._xgb is not None

    @property
    def has_lgbm(self) -> bool:
        return self._lgbm is not None

    @property
    def has_any(self) -> bool:
        return self._xgb is not None or self._lgbm is not None or self._en is not None

    @property
    def xgb_weight(self) -> float:
        """XGBoost 模型在当前集成中的权重。"""
        return self._xgb_weight

    @property
    def lgbm_weight(self) -> float:
        """LightGBM 模型在当前集成中的权重。"""
        return self._lgbm_weight

    def adapt_weights(
        self,
        panel: dict[str, pd.DataFrame],
        klines: dict[str, pd.DataFrame],
        registry: Any = None,
        lookback_dates: int = 3,
        forward_days: int = 5,
        decay_eta: float = 0.05,
        factor_cache: dict[str, pd.DataFrame] | None = None,
        realtime: bool = False,  # M3: 新增 realtime 模式
    ) -> None:
        """自适应权重：基于最近 N 天的滚动 IC 动态调整 XGB/LGBM 权重。

       结果缓存 24 小时，避免每次筛选都重新计算。

        Parameters
        ----------
        realtime : bool
            M3: 实时模式。True 时使用已实现收益（无需未来数据），
            False 时使用前瞻收益（仅回测可用）。
        """
        # 检查缓存
        cache_file = self._cache_dir / "adaptive_weights.json"
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    cached = json.load(f)
                age_hours = (time.time() - cached.get("timestamp", 0)) / 3600
                if age_hours < 24:
                    self._xgb_weight = cached["xgb_weight"]
                    self._lgbm_weight = cached["lgbm_weight"]
                    logger.info(
                        "Using cached adaptive weights (age=%.1fh): xgb=%.2f, lgbm=%.2f",
                        age_hours,
                        self._xgb_weight,
                        self._lgbm_weight,
                    )
                    return
            except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
                pass
        close = panel.get("close")
        if close is None or len(close) < lookback_dates + forward_days + 20:
            return

        # 只使用有足够前瞻数据的日期（需要 forward_days 后的数据来计算标签）
        available = close.index[20:-forward_days].tolist()
        if len(available) < lookback_dates:
            lookback_dates = len(available)
        if lookback_dates < 1:
            return
        dates = available[-lookback_dates:]

        xgb_ics: list[float] = []
        lgbm_ics: list[float] = []

        for date in dates:
            features = extract_features(panel, registry, target_date=date)
            if features.empty or self._feature_names is None:
                continue
            features = features.reindex(columns=self._feature_names, fill_value=0.0)
            # M3: 根据模式选择标签类型
            if realtime:
                # 实时模式：使用已实现收益（无需未来数据）
                labels = generate_realized_returns(klines, date, forward_days)
            else:
                # 回测模式：使用前瞻收益（与训练一致）
                from aimoon.ml.label_engine import generate_reversal_labels
                labels = generate_reversal_labels(klines, date, forward_days, lookback_days=20)
            common = features.index.intersection(labels.index)
            if len(common) < 20:
                continue

            try:
                if self._xgb is not None:
                    preds_xgb = self._xgb.predict(xgb.DMatrix(features.loc[common]))
                    with warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore", message="An input array is constant"
                        )
                        ic_xgb, _ = spearmanr(preds_xgb, labels[common].values)
                    if not np.isnan(ic_xgb):
                        xgb_ics.append(float(ic_xgb))

                if self._lgbm is not None:
                    preds_lgbm = self._lgbm.predict(features.loc[common])
                    with warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore", message="An input array is constant"
                        )
                        ic_lgbm, _ = spearmanr(preds_lgbm, labels[common].values)
                    if not np.isnan(ic_lgbm):
                        lgbm_ics.append(float(ic_lgbm))
            except (ValueError, RuntimeError):
                continue

        if not xgb_ics or not lgbm_ics:
            return

        # 指数加权平均 IC
        decay_xgb = np.exp(-decay_eta * np.arange(len(xgb_ics))[::-1])
        decay_xgb = decay_xgb / decay_xgb.sum()
        avg_ic_xgb = float(np.average(xgb_ics, weights=decay_xgb))

        decay_lgbm = np.exp(-decay_eta * np.arange(len(lgbm_ics))[::-1])
        decay_lgbm = decay_lgbm / decay_lgbm.sum()
        avg_ic_lgbm = float(np.average(lgbm_ics, weights=decay_lgbm))

        # 如果两个模型的平均 IC 都为负，说明当前市场环境下模型预测能力差
        # 不更新权重，保持默认的 0.5/0.5
        if avg_ic_xgb <= 0 and avg_ic_lgbm <= 0:
            logger.info(
                "Both models have negative IC (XGB=%.4f, LGBM=%.4f), "
                "keeping default weights (0.5/0.5)",
                avg_ic_xgb,
                avg_ic_lgbm,
            )
            return

        # Softmax 权重分配 (numerically stable)
        # 温度参数根据 IC 差异动态调整：
        # - IC 差异大时用更低温度（更尖锐），差异小时用更高温度（更平滑）
        ic_diff = abs(avg_ic_xgb - avg_ic_lgbm)
        temp = max(0.1, 0.5 - ic_diff * 10)
        max_ic = max(avg_ic_xgb, avg_ic_lgbm)
        exp_xgb = np.exp((avg_ic_xgb - max_ic) / temp)
        exp_lgbm = np.exp((avg_ic_lgbm - max_ic) / temp)
        total = exp_xgb + exp_lgbm

        if total > 0:
            self._xgb_weight = float(exp_xgb / total)
            self._lgbm_weight = float(exp_lgbm / total)
            logger.info(
                "Ensemble weights adapted: XGB_IC=%.4f, LGBM_IC=%.4f → w_xgb=%.2f, w_lgbm=%.2f",
                avg_ic_xgb,
                avg_ic_lgbm,
                self._xgb_weight,
                self._lgbm_weight,
            )
            # 缓存自适应权重，24小时内复用
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "timestamp": time.time(),
                            "xgb_weight": self._xgb_weight,
                            "lgbm_weight": self._lgbm_weight,
                            "xgb_ic": avg_ic_xgb,
                            "lgbm_ic": avg_ic_lgbm,
                        },
                        f,
                        indent=2,
                    )
            except (OSError, TypeError):
                logger.debug("Failed to cache adaptive weights")
        # ── EWMA factor weight update (weekly) ──
        # Compute 5-day average IC per alpha factor and feed into EWMA weighter
        try:
            ic_df = compute_factor_ic_series(
                panel,
                klines,
                registry,
                n_dates=5,
                forward_days=forward_days,
                factor_cache=factor_cache,
            )
            if not ic_df.empty:
                # Average IC across the 5 days
                avg_ic = ic_df.mean().dropna().to_dict()
                if avg_ic:
                    weighter = EWMAFactorWeighter.load() or EWMAFactorWeighter(
                        decay=0.95
                    )
                    weights = weighter.update(avg_ic)
                    weighter.save()
                    logger.info(
                        "EWMA factor weights updated: %d factors, %d total updates",
                        len(weights),
                        weighter.n_updates,
                    )
        except Exception as e:
            logger.warning("EWMA factor weight update failed: %s", e)


def ensemble_predict_signals(
    predictor: EnsemblePredictor,
    panel: dict[str, pd.DataFrame],
    registry: Any = None,
    sector_map: dict[str, str] | None = None,
) -> dict[str, list[Signal]]:
    """集成预测 → Signal 映射。

    保留百分位阈值逻辑，但基于集成后的连续预测值。
    """
    pred_series = predictor.predict(panel, registry, sector_map)
    if len(pred_series) < 5:
        return {}

    ranked = pred_series.rank(pct=True)
    signals_by_code: dict[str, list[Signal]] = {}

    for code in pred_series.index:
        pct = ranked[code]
        sigs: list[Signal] = []

        if pct >= 0.90:
            sigs.append(Signal("ml_alpha_strong", f"ML集成强烈看多({pct:.0%})", +5, category="ml"))
        elif pct >= 0.75:
            sigs.append(Signal("ml_alpha", f"ML集成看多({pct:.0%})", +3, category="ml"))
        elif pct <= 0.10:
            sigs.append(
                Signal("ml_alpha_bear_strong", f"ML集成强烈看空({pct:.0%})", -5, category="ml")
            )
        elif pct <= 0.25:
            sigs.append(Signal("ml_alpha_bear", f"ML集成看空({pct:.0%})", -3, category="ml"))

        if sigs:
            signals_by_code[str(code)] = sigs

    return signals_by_code


# ── Stacking Ensemble ──


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
            Path(cache_dir) / "ml" / "stacking_native"
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

        # Collect out-of-fold meta-features
        oof_preds_xgb = np.full(len(X), np.nan)
        oof_preds_lgbm = np.full(len(X), np.nan)

        # L4: Use regressor for continuous targets (default), classifier for binary
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

            # L4: Use regressor for continuous targets, classifier for binary
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

            # Train LightGBM base
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

        # Build meta-features from OOF predictions
        valid_mask = ~(np.isnan(oof_preds_xgb) | np.isnan(oof_preds_lgbm))
        if valid_mask.sum() < 30:
            logger.warning(
                "Stacking: insufficient OOF predictions (%d)", valid_mask.sum()
            )
            return

        meta_X = pd.DataFrame(
            {
                "xgb_pred": oof_preds_xgb[valid_mask],
                "lgbm_pred": oof_preds_lgbm[valid_mask],
            }
        )
        meta_y = (
            y.iloc[valid_mask] if isinstance(y, pd.Series) else pd.Series(y[valid_mask])
        )

        # L4: Use regressor for continuous targets, classifier for binary
        if is_binary:
            self._meta_model = lgb.LGBMClassifier(n_estimators=50, verbose=-1)
        else:
            self._meta_model = lgb.LGBMRegressor(n_estimators=50, verbose=-1)
        self._meta_model.fit(meta_X, meta_y)

        # Train calibrator on meta-model OOF predictions
        # Use a simple holdout (last 20%) for calibration to avoid data leakage
        # from PurgedTimeSeriesSplit on low-dimensional meta-features.
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

        # L4: Retrain base models on full data for production use
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
        logger.info(
            "StackingEnsemble fitted: %d samples, %d features", len(X), len(X.columns)
        )

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

        # L4: Base model predictions (regressor or classifier)
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

        # Meta-features
        meta_X = pd.DataFrame(preds, index=X.index)

        # Meta-model prediction
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

        # Calibration
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
        if self._lgbm_base is not None:
            self._lgbm_base.booster_.save_model(str(base / "lgbm_base.txt"))
        if self._meta_model is not None:
            self._meta_model.booster_.save_model(str(base / "meta_model.txt"))

        # Save calibrator and metadata as JSON
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
            Path(cache_dir) / "ml" / "stacking_native"
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

            # Load XGBoost base
            xgb_path = base / "xgb_base.json"
            if xgb_path.exists():
                import xgboost as xgb
                obj._xgb_base = xgb.XGBClassifier()
                obj._xgb_base.load_model(str(xgb_path))

            # Load LightGBM base
            lgbm_path = base / "lgbm_base.txt"
            if lgbm_path.exists():
                import lightgbm as lgb
                obj._lgbm_base = lgb.LGBMClassifier()
                obj._lgbm_base._Booster = lgb.Booster(model_file=str(lgbm_path))

            # Load meta model
            meta_model_path = base / "meta_model.txt"
            if meta_model_path.exists():
                import lightgbm as lgb
                obj._meta_model = lgb.LGBMClassifier()
                obj._meta_model._Booster = lgb.Booster(model_file=str(meta_model_path))

            # Reconstruct calibrator from JSON
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


def compute_optimal_weights(
    xgb_preds: pd.Series,
    lgbm_preds: pd.Series,
    labels: pd.Series,
    min_weight: float = 0.1,
) -> tuple[float, float]:
    """Compute optimal ensemble weights by minimizing MSE against labels.

    Uses a simple grid search over weight combinations to find the
    (w_xgb, w_lgbm) pair that minimizes weighted-MSE against labels.
    Weights are clamped to [min_weight, 1 - min_weight] and sum to 1.

    Args:
        xgb_preds: XGBoost model predictions.
        lgbm_preds: LightGBM model predictions.
        labels: Ground-truth labels (e.g., forward returns).
        min_weight: Minimum weight per model (prevents zero-weight).

    Returns:
        Tuple of (w_xgb, w_lgbm) that sum to 1.0.
    """
    if len(labels) < 10:
        return 0.5, 0.5

    best_mse = float("inf")
    best_w_xgb = 0.5
    for w_xgb_int in range(int(min_weight * 100), int((1 - min_weight) * 100) + 1, 5):
        w_xgb = w_xgb_int / 100.0
        w_lgbm = 1.0 - w_xgb
        combined = w_xgb * xgb_preds.values + w_lgbm * lgbm_preds.values
        mse = float(np.mean((combined - labels.values) ** 2))
        if mse < best_mse:
            best_mse = mse
            best_w_xgb = w_xgb
    return best_w_xgb, 1.0 - best_w_xgb
