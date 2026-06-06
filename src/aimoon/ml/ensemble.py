"""Two Sigma 风格集成预测器 — XGBoost + LightGBM 加权平均。

集成多个异构模型的预测，输出连续预测值用于组合优化。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from aimoon.ml.feature_pipeline import extract_features
from aimoon.models import Signal

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(".aimoon_cache") / "ml"


@dataclass(frozen=True)
class EnsembleResult:
    """集成模型的预测结果。"""

    predictions: pd.Series  # code -> predicted return
    xgb_weight: float
    lgbm_weight: float
    xgb_ic: float
    lgbm_ic: float


class EnsemblePredictor:
    """XGBoost + LightGBM 集成预测器。

    对同一特征矩阵分别用两个模型预测，然后加权平均。
    权重基于各模型在验证集上的 IC 动态计算。
    """

    def __init__(
        self,
        xgb_model: object | None = None,
        lgbm_model: object | None = None,
        feature_names: list[str] | None = None,
        xgb_weight: float = 0.5,
        lgbm_weight: float = 0.5,
    ):
        self._xgb = xgb_model
        self._lgbm = lgbm_model
        self._feature_names = feature_names
        self._xgb_weight = xgb_weight
        self._lgbm_weight = lgbm_weight

    @classmethod
    def from_cache(cls, cache_dir: str | Path | None = None) -> EnsemblePredictor:
        """从缓存目录加载集成模型。"""
        import lightgbm as lgb
        import xgboost as xgb

        cache = Path(cache_dir or _CACHE_DIR)

        # Load feature names
        fn_path = cache / "feature_names.json"
        feature_names = None
        if fn_path.exists():
            with open(fn_path, encoding="utf-8") as f:
                feature_names = json.load(f)

        # Load XGBoost model
        xgb_model = None
        xgb_path = cache / "model.json"
        if xgb_path.exists():
            try:
                xgb_model = xgb.Booster()
                xgb_model.load_model(str(xgb_path))
            except Exception as e:
                logger.debug("Failed to load XGBoost model: %s", e)

        # Load LightGBM model
        lgbm_model = None
        lgbm_path = cache / "model.lgbm.txt"
        if lgbm_path.exists():
            try:
                booster = lgb.Booster(model_file=str(lgbm_path))
                lgbm_model = booster
            except Exception as e:
                logger.debug("Failed to load LightGBM model: %s", e)

        # Load ensemble weights from meta
        xgb_weight, lgbm_weight = 0.5, 0.5
        xgb_ic, lgbm_ic = 0.0, 0.0
        ensemble_meta = cache / "ensemble_meta.json"
        if ensemble_meta.exists():
            try:
                with open(ensemble_meta, encoding="utf-8") as f:
                    meta = json.load(f)
                xgb_weight = meta.get("xgb_weight", 0.5)
                lgbm_weight = meta.get("lgbm_weight", 0.5)
                xgb_ic = meta.get("xgb_ic", 0.0)
                lgbm_ic = meta.get("lgbm_ic", 0.0)
            except Exception:
                pass

        return cls(
            xgb_model=xgb_model,
            lgbm_model=lgbm_model,
            feature_names=feature_names,
            xgb_weight=xgb_weight,
            lgbm_weight=lgbm_weight,
        )

    def predict(
        self,
        panel: dict[str, pd.DataFrame],
        registry: object | None = None,
        sector_map: dict[str, str] | None = None,
    ) -> pd.Series:
        """集成预测：XGBoost + LightGBM 加权平均。

        Returns
        -------
        pd.Series
            index=stock code, value=predicted return (continuous).
        """
        features = extract_features(panel, registry, sector_map=sector_map)
        if features.empty:
            return pd.Series(dtype=float)

        # Reindex to expected features
        if self._feature_names:
            missing = set(self._feature_names) - set(features.columns)
            if missing:
                logger.debug("Ensemble: %d missing features filled with 0", len(missing))
            features = features.reindex(columns=self._feature_names, fill_value=0.0)

        predictions: dict[str, np.ndarray] = {}

        # XGBoost prediction
        if self._xgb is not None:
            try:
                import xgboost as xgb

                dmatrix = xgb.DMatrix(features)
                predictions["xgb"] = self._xgb.predict(dmatrix)
            except Exception as e:
                logger.debug("XGBoost predict failed: %s", e)

        # LightGBM prediction
        if self._lgbm is not None:
            try:
                predictions["lgbm"] = self._lgbm.predict(features)
            except Exception as e:
                logger.debug("LightGBM predict failed: %s", e)

        if not predictions:
            return pd.Series(dtype=float)

        # Weighted average
        if len(predictions) == 2:
            combined = (
                self._xgb_weight * predictions["xgb"] + self._lgbm_weight * predictions["lgbm"]
            )
        elif "xgb" in predictions:
            combined = predictions["xgb"]
        else:
            combined = predictions["lgbm"]

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

    def adapt_weights(
        self,
        panel: dict[str, pd.DataFrame],
        klines: dict[str, pd.DataFrame],
        registry: object | None = None,
        lookback_dates: int = 3,
        forward_days: int = 5,
        decay_eta: float = 0.05,
    ) -> None:
        """自适应权重：基于最近 N 天的滚动 IC 动态调整 XGB/LGBM 权重。

        结果缓存 24 小时，避免每次筛选都重新计算。
        """
        import json as _json
        import time as _time

        # 检查缓存
        cache_file = _CACHE_DIR / "adaptive_weights.json"
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    cached = _json.load(f)
                age_hours = (_time.time() - cached.get("timestamp", 0)) / 3600
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
            except Exception:
                pass
        from scipy.stats import spearmanr

        from aimoon.ml.feature_pipeline import extract_features

        close = panel.get("close")
        if close is None or len(close) < lookback_dates + forward_days + 20:
            return

        available = close.index[20:].tolist()
        if len(available) < lookback_dates:
            lookback_dates = len(available)
        dates = available[-lookback_dates:]

        xgb_ics: list[float] = []
        lgbm_ics: list[float] = []

        for date in dates:
            features = extract_features(panel, registry, target_date=date)
            if features.empty or self._feature_names is None:
                continue
            features = features.reindex(columns=self._feature_names, fill_value=0.0)
            # 修复前瞻偏差：使用已实现收益（过去 forward_days 天的收益）
            # 而不是前瞻收益（未来 forward_days 天的收益）
            from aimoon.ml.label_engine import generate_realized_returns
            labels = generate_realized_returns(klines, date, forward_days)
            common = features.index.intersection(labels.index)
            if len(common) < 20:
                continue

            try:
                if self._xgb is not None:
                    import xgboost as xgb

                    preds_xgb = self._xgb.predict(xgb.DMatrix(features.loc[common]))
                    ic_xgb, _ = spearmanr(preds_xgb, labels[common].values)
                    if not np.isnan(ic_xgb):
                        xgb_ics.append(float(ic_xgb))

                if self._lgbm is not None:
                    preds_lgbm = self._lgbm.predict(features.loc[common])
                    ic_lgbm, _ = spearmanr(preds_lgbm, labels[common].values)
                    if not np.isnan(ic_lgbm):
                        lgbm_ics.append(float(ic_lgbm))
            except Exception:
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

        # Softmax 权重分配 (numerically stable)
        temp = 5.0
        max_ic = max(avg_ic_xgb, avg_ic_lgbm)
        exp_xgb = np.exp((avg_ic_xgb - max_ic) * temp)
        exp_lgbm = np.exp((avg_ic_lgbm - max_ic) * temp)
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
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    _json.dump(
                        {
                            "timestamp": _time.time(),
                            "xgb_weight": self._xgb_weight,
                            "lgbm_weight": self._lgbm_weight,
                            "xgb_ic": avg_ic_xgb,
                            "lgbm_ic": avg_ic_lgbm,
                        },
                        f,
                        indent=2,
                    )
            except Exception:
                pass


def ensemble_predict_signals(
    predictor: EnsemblePredictor,
    panel: dict[str, pd.DataFrame],
    registry: object | None = None,
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
            sigs.append(Signal("ml_alpha_strong", f"ML集成强烈看多({pct:.0%})", +5))
        elif pct >= 0.75:
            sigs.append(Signal("ml_alpha", f"ML集成看多({pct:.0%})", +3))
        elif pct <= 0.10:
            sigs.append(Signal("ml_alpha_bear_strong", f"ML集成强烈看空({pct:.0%})", -5))
        elif pct <= 0.25:
            sigs.append(Signal("ml_alpha_bear", f"ML集成看空({pct:.0%})", -3))

        if sigs:
            signals_by_code[str(code)] = sigs

    return signals_by_code


def compute_optimal_weights(
    xgb_preds: pd.Series,
    lgbm_preds: pd.Series,
    labels: pd.Series,
) -> tuple[float, float]:
    """基于验证集 IC 计算最优集成权重。

    遍历 0.0-1.0 的权重网格，找到最大化集成 IC 的组合。
    """
    from scipy.stats import spearmanr

    common = xgb_preds.index.intersection(lgbm_preds.index).intersection(labels.index)
    if len(common) < 30:
        return 0.5, 0.5

    x = xgb_preds[common].values
    l = lgbm_preds[common].values
    y = labels[common].values

    best_ic = -999.0
    best_w = 0.5

    for w in np.arange(0.0, 1.05, 0.1):
        combined = w * x + (1 - w) * l
        ic, _ = spearmanr(combined, y)
        if not np.isnan(ic) and ic > best_ic:
            best_ic = ic
            best_w = w

    logger.info(
        "Optimal ensemble weights: XGB=%.1f, LGBM=%.1f, IC=%.4f",
        best_w,
        1 - best_w,
        best_ic,
    )
    return best_w, 1 - best_w
