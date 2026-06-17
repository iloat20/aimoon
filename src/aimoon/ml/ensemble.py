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

from aimoon.ml.ensemble_signals import (  # noqa: F401 — re-export for backward compat
    compute_optimal_weights,
    ensemble_predict_signals,
)
from aimoon.ml.feature_pipeline import extract_features
from aimoon.ml.icir_weighter import EWMAFactorWeighter, compute_factor_ic_series
from aimoon.ml.label_engine import generate_realized_returns
from aimoon.ml.stacking import StackingEnsemble  # noqa: F401 — re-export for backward compat

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path(".aimoon_cache") / "ml"


@dataclass
class EnsemblePredictor:
    """XGBoost + LightGBM 集成预测器。

    对同一特征矩阵分别用两个模型预测，然后加权平均。
    权重基于各模型在验证集上的 IC 动态计算，或通过
    DynamicMetaEnsemble 元学习器自适应调整。

    支持双模型 (A/B) 增量学习：
    - Model A: 长期全量模型
    - Model B: 短期增量模型（可选，当有增量数据时使用）
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
        meta_ensemble: Any = None,  # DynamicMetaEnsemble 元学习器
        dual_model: Any = None,  # DualModel 增量学习器
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
        self._cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self._meta_ensemble = meta_ensemble
        self._dual_model = dual_model

    @classmethod
    def from_cache(cls, cache_dir: str | Path | None = None) -> EnsemblePredictor:
        """从缓存目录加载集成模型。"""
        import lightgbm as lgb
        import xgboost as xgb

        cache = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR

        # 优先加载规范特征名文件（合并训练时所有模型使用的特征）
        canonical_fn = cache / "canonical_feature_names.json"
        xgb_fn = cache / "xgb_feature_names.json"
        lgbm_fn = cache / "lgbm_feature_names.json"
        en_fn = cache / "elasticnet_feature_names.json"
        feature_names = None
        if canonical_fn.exists():
            with open(canonical_fn, encoding="utf-8") as f:
                feature_names = json.load(f)
            logger.info("Loaded canonical feature names: %d features", len(feature_names))
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
                en_model.n_features_in_ = en_data.get("n_features_in", len(en_data["coef"]))

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

        # Load DynamicMetaEnsemble
        meta_ensemble = None
        try:
            from aimoon.ml.meta_ensemble import DynamicMetaEnsemble

            meta_ensemble = DynamicMetaEnsemble.load()
            if meta_ensemble.is_ridge_active:
                logger.info(
                    "DynamicMetaEnsemble loaded: ridge active, %d refits", meta_ensemble.n_refits
                )
        except Exception as e:
            logger.debug("DynamicMetaEnsemble load failed: %s", e)

        # Load SmartDualModel / DualModel (incremental learning)
        dual_model = None
        try:
            from aimoon.ml.incremental_trainer import load_dual_model

            dual_model = load_dual_model(cache)
            if dual_model is not None:
                logger.info(
                    "DualModel loaded: w_a=%.2f, w_b=%.2f, b_trains=%d",
                    dual_model.weight_a,
                    dual_model.weight_b,
                    dual_model.b_train_count,
                )
        except Exception as e:
            logger.debug("DualModel load failed: %s", e)

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
            meta_ensemble=meta_ensemble,
            dual_model=dual_model,
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
            panel,
            registry,
            target_date=target_date,
            sector_map=sector_map,
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
                    medians = self._feature_medians.reindex(list(missing), fill_value=0.0)
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
                    features_en = features.reindex(columns=self._feature_names, fill_value=0.0)
                else:
                    features_en = features
                en_scaled = self._en_scaler.transform(features_en.values)
                predictions["en"] = self._en.predict(en_scaled)
            except Exception as e:
                logger.warning("Elastic Net predict failed: %s", e)

        if not predictions:
            return pd.Series(dtype=float)

        # ── 双模型 (A/B) 加权 ──
        # 如果有 DualModel 且有 A/B 模型，优先使用双模型预测
        if (
            self._dual_model is not None
            and self._dual_model.model_a is not None
            and self._dual_model.model_b is not None
        ):
            try:
                from aimoon.ml.incremental_trainer import predict_dual

                dual_pred = predict_dual(self._dual_model, features)
                if len(dual_pred) == len(features):
                    dual_weight = self._dual_model.weight_a + self._dual_model.weight_b
                    predictions["dual"] = dual_pred * dual_weight
                    logger.info(
                        "DualModel prediction used: w_a=%.2f, w_b=%.2f, "
                        "decay=%.4f, ewc=%.4f, slide=%s",
                        self._dual_model.weight_a,
                        self._dual_model.weight_b,
                        self._dual_model.ic_decay_speed,
                        getattr(self._dual_model, "ewc_loss", 0.0),
                        getattr(self._dual_model, "slide_detected", False),
                    )
            except Exception as e:
                logger.debug("DualModel prediction failed: %s", e)

        # Weighted average — prefer meta ensemble weights, fallback to IC weights
        active_weights: dict[str, float] = {}

        # Try DynamicMetaEnsemble weights first
        if self._meta_ensemble is not None and self._meta_ensemble.is_ridge_active:
            meta_w = self._meta_ensemble.update_weights()
            for name in predictions:
                if name in meta_w:
                    active_weights[name] = meta_w[name]
                elif name == "en":
                    active_weights[name] = self._en_weight

        # Fallback to stored IC weights
        if not active_weights:
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
        forward_days: int = 22,
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
        import xgboost as xgb
        from scipy.stats import spearmanr

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
                        warnings.filterwarnings("ignore", message="An input array is constant")
                        ic_xgb, _ = spearmanr(preds_xgb, labels[common].values)
                    if not np.isnan(ic_xgb):
                        xgb_ics.append(float(ic_xgb))

                if self._lgbm is not None:
                    preds_lgbm = self._lgbm.predict(features.loc[common])
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore", message="An input array is constant")
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
            raw_xgb = float(exp_xgb / total)
            raw_lgbm = float(exp_lgbm / total)
            # 最低权重保护：防止单个模型因近期IC优势主导集成（过拟合近期regime）
            min_weight = 0.20
            self._xgb_weight = max(raw_xgb, min_weight) if raw_xgb > 0.5 else raw_xgb
            self._lgbm_weight = max(raw_lgbm, min_weight) if raw_lgbm > 0.5 else raw_lgbm
            # 重新归一化
            w_sum = self._xgb_weight + self._lgbm_weight
            self._xgb_weight /= w_sum
            self._lgbm_weight /= w_sum
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
                    weighter = EWMAFactorWeighter.load() or EWMAFactorWeighter(decay=0.95)
                    weights = weighter.update(avg_ic)
                    weighter.save()
                    logger.info(
                        "EWMA factor weights updated: %d factors, %d total updates",
                        len(weights),
                        weighter.n_updates,
                    )
        except Exception as e:
            logger.warning("EWMA factor weight update failed: %s", e)

        # ── Factor covariance + Sharpe diagnostic ──
        try:
            from aimoon.ml.icir_weighter import (
                compute_factor_covariance_and_sharpe,
                compute_icir_weights,
            )

            if not ic_df.empty and len(ic_df) >= 30:
                # Compute ICIR weights from the full IC series
                icir_w = compute_icir_weights(ic_df)
                if icir_w:
                    cov_result = compute_factor_covariance_and_sharpe(
                        ic_df,
                        icir_w,
                        denoise=True,
                    )
                    if cov_result is not None:
                        sharpe = cov_result.get("sharpe")
                        n_eff = cov_result.get("n_effective", 0)
                        shrinkage = cov_result.get("shrinkage", 0)
                        logger.info(
                            "Factor cov: sharpe=%.3f, n_eff=%.1f, shrinkage=%.3f",
                            sharpe if sharpe is not None else 0.0,
                            n_eff,
                            shrinkage,
                        )
        except Exception as e:
            logger.debug("Factor covariance diagnostic failed: %s", e)

        # ── DynamicMetaEnsemble update ──
        if self._meta_ensemble is not None:
            try:
                # Record this day's OOS predictions for meta learner training
                meta_w = self._meta_ensemble.update_weights(
                    xgb_ic=avg_ic_xgb if xgb_ics else 0.0,
                    lgbm_ic=avg_ic_lgbm if lgbm_ics else 0.0,
                )
                # Update stored weights from meta ensemble
                self._xgb_weight = meta_w.get("xgb", self._xgb_weight)
                self._lgbm_weight = meta_w.get("lgbm", self._lgbm_weight)
                self._meta_ensemble.save()
                logger.info(
                    "MetaEnsemble updated: ridge=%s, xgb_w=%.3f, lgbm_w=%.3f",
                    self._meta_ensemble.is_ridge_active,
                    self._xgb_weight,
                    self._lgbm_weight,
                )
            except Exception as e:
                logger.debug("MetaEnsemble update failed: %s", e)
