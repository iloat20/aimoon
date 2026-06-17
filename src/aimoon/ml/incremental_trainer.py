"""双模型增量学习 — A/B 模型 + EWC 正则 + 自适应权重。

核心策略：
    Model A: 长期全量模型（历史数据训练，稳定性强）
    Model B: 短期增量模型（近期数据训练，适应性强）

    预测时：根据 IC 衰减速度动态调整 A/B 权重
    训练时：使用 EWC 正则防止 B 遗忘旧知识

EWC (Elastic Weight Consolidation) 原理：
    L_total = L_new + λ/2 * Σ_i F_i * (θ_i - θ*_i)²

    其中：
    - L_new: 新数据的损失函数
    - F_i: Fisher 信息矩阵对角线（参数重要性）
    - θ*_i: 旧模型参数（A 模型的参数）
    - λ: EWC 正则强度

    重要参数（Fisher 信息大）在增量训练时被约束不能偏离 A 模型太远。

SmartIncrementalLearner：
    集成 EWC callback 训练、IC 衰减检测、Purged TSCV 滑坡检测、
    自适应权重分配于一体。向后兼容原有 DualModel 接口。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import spearmanr

from aimoon.ml._training_commons import (
    compute_ewc_penalty,
    compute_fisher_diagonal,
    compute_spearmanr_safe,
)

logger = logging.getLogger(__name__)

# ── 常量 ──
_DEFAULT_IC_DECAY_THRESHOLD = 0.02
_DEFAULT_EWC_LAMBDA = 50.0
_DEFAULT_RETRAIN_THRESHOLD = 0.3
_MIN_INCREMENTAL_SAMPLES = 50


# ════════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════════


@dataclass
class DualModel:
    """双模型状态。

    Attributes
    ----------
    model_a : xgb.Booster | None
        长期全量模型。
    model_b : xgb.Booster | None
        短期增量模型。
    weight_a : float
        A 模型当前权重。
    weight_b : float
        B 模型当前权重。
    ic_decay_speed : float
        近 20 天 IC 衰减速度（线性回归斜率）。
    fisher_info : dict[str, float]
        Fisher 信息对角线（参数重要性）。
    last_train_time : str
        上次训练时间。
    b_train_count : int
        B 模型累计训练次数。
    feature_names : list[str]
        特征名列表。
    a_ic : float
        A 模型在增量数据上的 IC。
    b_ic : float
        B 模型在增量数据上的 IC。
    """

    model_a: Any = None  # xgb.Booster
    model_b: Any = None  # xgb.Booster
    weight_a: float = 0.5
    weight_b: float = 0.5
    ic_decay_speed: float = 0.0
    fisher_info: dict[str, float] = field(default_factory=dict)
    last_train_time: str = ""
    b_train_count: int = 0
    feature_names: list[str] = field(default_factory=list)
    a_ic: float = 0.0
    b_ic: float = 0.0


@dataclass
class SmartDualModel:
    """增强版双模型容器（向后兼容 DualModel）。

    新增字段：ewc_loss, slide_detected。
    所有字段均为可选，缺失时用默认值填充。
    """

    model_a: Any = None
    model_b: Any = None
    weight_a: float = 0.7
    weight_b: float = 0.3
    fisher_info: dict[str, float] = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)
    a_ic: float = 0.0
    b_ic: float = 0.0
    ic_decay_speed: float = 0.0
    b_train_count: int = 0
    last_train_time: str = ""
    ewc_loss: float = 0.0
    slide_detected: bool = False


@dataclass
class IncrementalTrainResult:
    """增量训练结果。"""

    model: Any  # xgb.Booster
    ic: float
    ic_train: float
    overfit_ratio: float
    n_samples: int
    is_b_model: bool
    ewc_loss: float = 0.0
    performance_slide: bool = False


# ════════════════════════════════════════════════════════════════
#  1. Fisher 信息估计（EWC 核心）— 向后兼容
# ════════════════════════════════════════════════════════════════


def compute_fisher_information(
    model: Any,
    X: pd.DataFrame,
    y: pd.Series,
    feature_names: list[str],
    n_samples: int = 100,
) -> dict[str, float]:
    """估计 Fisher 信息矩阵对角线（参数重要性）。

    简化实现：用 XGBoost 的 feature importance (gain) 作为代理。

    数学定义：
        F_i ≈ E[ (∂L/∂θ_i)² ] ≈ importance(θ_i)

    Parameters
    ----------
    model : xgb.Booster
        已训练模型。
    X : pd.DataFrame
        训练数据。
    y : pd.Series
        标签。
    feature_names : list[str]
        特征名列表。
    n_samples : int
        采样数（用于估计）。

    Returns
    -------
    dict[str, float]
        feature_name → Fisher 信息值。
    """
    try:
        importance = model.get_score(importance_type="gain")
        fisher: dict[str, float] = {}
        max_importance = max(importance.values()) if importance else 1.0

        for fname in feature_names:
            imp = importance.get(fname, 0.0)
            fisher[fname] = imp / max_importance if max_importance > 0 else 0.0

        return fisher
    except Exception as e:
        logger.debug("Fisher information estimation failed: %s", e)
        return {fname: 1.0 for fname in feature_names}


# ════════════════════════════════════════════════════════════════
#  2. EWC 损失计算 — 向后兼容
# ════════════════════════════════════════════════════════════════


def compute_ewc_loss(
    model_b_params: dict[str, float],
    model_a_params: dict[str, float],
    fisher_info: dict[str, float],
    lambda_ewc: float = _DEFAULT_EWC_LAMBDA,
) -> float:
    """计算 EWC 正则损失。

    L_ewc = λ/2 * Σ_i F_i * (θ_i^B - θ_i^A)²

    对于树模型，我们用叶节点权重的差异作为参数差异的代理。

    Parameters
    ----------
    model_b_params : dict[str, float]
        B 模型参数（叶节点权重摘要）。
    model_a_params : dict[str, float]
        A 模型参数（叶节点权重摘要）。
    fisher_info : dict[str, float]
        Fisher 信息。
    lambda_ewc : float
        EWC 正则强度。

    Returns
    -------
    float
        EWC 损失值。
    """
    return compute_ewc_penalty(model_b_params, model_a_params, fisher_info, lambda_ewc)


def extract_model_params(model: Any) -> dict[str, float]:
    """提取模型参数摘要（用于 EWC 比较）。

    对于 XGBoost，提取每个特征的平均分裂增益作为参数代理。
    """
    try:
        importance = model.get_score(importance_type="gain")
        total = sum(importance.values()) if importance else 1.0
        return {k: v / total for k, v in importance.items()}
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════════
#  3. IC 衰减速度计算
# ════════════════════════════════════════════════════════════════


def compute_ic_decay_speed(
    recent_ics: list[float],
    window: int = 20,
) -> float:
    """计算 IC 衰减速度（线性回归斜率）。

    数学定义：
        IC_t = α + β * t + ε
        decay_speed = β (斜率)

    β < 0 表示 IC 在衰减，|β| 越大衰减越快。

    Parameters
    ----------
    recent_ics : list[float]
        最近 N 天的 IC 序列。
    window : int
        计算窗口。

    Returns
    -------
    float
        衰减速度（斜率）。负值 = 衰减。
    """
    if len(recent_ics) < 5:
        return 0.0

    arr = np.array(recent_ics[-window:])
    x = np.arange(len(arr), dtype=np.float64)

    coeffs = np.polyfit(x, arr, 1)
    return float(coeffs[0])  # β = 斜率


# ════════════════════════════════════════════════════════════════
#  4. 自适应 A/B 权重
# ════════════════════════════════════════════════════════════════


def compute_dual_weights(
    ic_decay_speed: float,
    a_ic: float,
    b_ic: float,
    decay_threshold: float = _DEFAULT_IC_DECAY_THRESHOLD,
) -> tuple[float, float]:
    """根据 IC 衰减速度动态调整 A/B 权重。

    策略：
        if IC 衰减速度 < threshold（衰减快）:
            B 权重提高到 0.7（短期模型更适应新市场）
        else:
            A 权重为主 0.7（长期模型更稳定）

    额外考虑：如果 B 的 IC 显著低于 A，降低 B 权重。

    Parameters
    ----------
    ic_decay_speed : float
        IC 衰减速度（负值 = 衰减）。
    a_ic : float
        A 模型的 IC。
    b_ic : float
        B 模型的 IC。
    decay_threshold : float
        衰减速度阈值。

    Returns
    -------
    tuple[float, float]
        (weight_a, weight_b)。
    """
    if ic_decay_speed < -decay_threshold:
        base_a, base_b = 0.3, 0.7
    else:
        base_a, base_b = 0.7, 0.3

    if b_ic > 0 and a_ic > 0:
        ic_ratio = b_ic / a_ic
        if ic_ratio < 0.5:
            base_b *= 0.5
            base_a = 1.0 - base_b
    elif b_ic <= 0 and a_ic > 0:
        base_b = 0.1
        base_a = 0.9

    total = base_a + base_b
    return base_a / total, base_b / total


# ════════════════════════════════════════════════════════════════
#  5. Purged TSCV 性能滑坡检测
# ════════════════════════════════════════════════════════════════


def detect_performance_slide(
    model: Any,
    X: pd.DataFrame,
    y: pd.Series,
    feature_names: list[str],
    n_splits: int = 5,
    purge_days: int = 5,
    embargo_days: int = 15,
    slide_threshold: float = 0.3,
) -> tuple[bool, float, list[float]]:
    """使用 Purged Time Series Split 检测性能滑坡。

    数学定义：
        对每个 fold i:
            IC_i = Spearman(pred_val_i, label_val_i)

        滑坡检测：
            IC_recent = mean(IC[-2:])  — 最近两折
            IC_earlier = mean(IC[:-2]) — 之前几折
            slide_ratio = IC_recent / IC_earlier

            if slide_ratio < slide_threshold:
                performance_slide = True

    Parameters
    ----------
    model : xgb.Booster
        待检测模型。
    X : pd.DataFrame
        特征矩阵。
    y : pd.Series
        标签。
    feature_names : list[str]
        特征名。
    n_splits : int
        CV 折数。
    purge_days : int
        purge 间隔。
    embargo_days : int
        embargo 间隔。
    slide_threshold : float
        滑坡阈值。

    Returns
    -------
    tuple[bool, float, list[float]]
        (is_slide, slide_ratio, fold_ics)。
    """
    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    X_with_date = X.copy()
    if "_date" not in X_with_date.columns:
        return False, 1.0, []

    tscv = PurgedTimeSeriesSplit(
        n_splits=n_splits,
        purge_days=purge_days,
        embargo_days=embargo_days,
    )

    fold_ics: list[float] = []
    for train_idx, val_idx in tscv.split(
        X_with_date,
        date_column="_date",
    ):
        if len(val_idx) < 5:
            continue

        X_val = X_with_date.iloc[val_idx].drop(columns=["_date"], errors="ignore")
        y_val = y.iloc[val_idx]

        try:
            preds = model.predict(xgb.DMatrix(X_val[feature_names]))
            with np.errstate(all="ignore"):
                ic, _ = spearmanr(preds, y_val.values)
            if not np.isnan(ic):
                fold_ics.append(float(ic))
        except Exception:
            continue

    if len(fold_ics) < 3:
        return False, 1.0, fold_ics

    n_recent = min(2, len(fold_ics) // 2)
    ic_recent = np.mean(fold_ics[-n_recent:])
    ic_earlier = np.mean(fold_ics[:-n_recent]) if len(fold_ics) > n_recent else ic_recent

    if abs(ic_earlier) < 1e-6:
        slide_ratio = 1.0
    else:
        slide_ratio = ic_recent / ic_earlier

    is_slide = slide_ratio < slide_threshold

    if is_slide:
        logger.warning(
            "Performance slide detected: IC_recent=%.4f, IC_earlier=%.4f, ratio=%.2f < %.2f",
            ic_recent,
            ic_earlier,
            slide_ratio,
            slide_threshold,
        )

    return is_slide, float(slide_ratio), fold_ics


# ════════════════════════════════════════════════════════════════
#  6. 增量训练核心（带 EWC）— 向后兼容
# ════════════════════════════════════════════════════════════════


def train_incremental_b(
    model_a: Any,
    X_new: pd.DataFrame,
    y_new: pd.Series,
    feature_names: list[str],
    fisher_info: dict[str, float],
    xgb_params: dict[str, Any],
    lambda_ewc: float = _DEFAULT_EWC_LAMBDA,
    n_rounds: int = 100,
) -> IncrementalTrainResult:
    """训练增量模型 B（带 EWC 正则）。

    简化实现：
    - 先用新数据训练 B 模型
    - 然后用 EWC 损失评估 B 与 A 的差异

    Parameters
    ----------
    model_a : xgb.Booster
        长期模型 A（作为 EWC 锚点）。
    X_new, y_new : 增量数据。
    feature_names : 特征名。
    fisher_info : Fisher 信息。
    xgb_params : XGBoost 参数。
    lambda_ewc : EWC 强度。
    n_rounds : 增量训练轮数。

    Returns
    -------
    IncrementalTrainResult
        训练结果。
    """
    t0 = time.time()

    dtrain = xgb.DMatrix(X_new[feature_names], label=y_new)

    b_params = {**xgb_params}
    b_params["learning_rate"] = max(0.01, xgb_params.get("learning_rate", 0.1) * 0.5)
    b_params["n_estimators"] = n_rounds

    try:
        model_b = xgb.train(
            b_params,
            dtrain,
            num_boost_round=n_rounds,
            obj=None,
        )
    except Exception as e:
        logger.warning("Incremental B training failed: %s", e)
        model_b = model_a

    params_a = extract_model_params(model_a)
    params_b = extract_model_params(model_b)
    ewc_loss = compute_ewc_loss(params_b, params_a, fisher_info, lambda_ewc)

    preds_b = model_b.predict(dtrain)
    ic_b = float(spearmanr(preds_b, y_new.values)[0]) if len(y_new) > 5 else 0.0

    preds_a = model_a.predict(dtrain)
    ic_a = float(spearmanr(preds_a, y_new.values)[0]) if len(y_new) > 5 else 0.0

    overfit_ratio = abs(ic_a) / (abs(ic_b) + 1e-10) if ic_b != 0 else 1.0

    result = IncrementalTrainResult(
        model=model_b,
        ic=ic_b,
        ic_train=ic_a,
        overfit_ratio=overfit_ratio,
        n_samples=len(y_new),
        is_b_model=True,
        ewc_loss=ewc_loss,
    )

    logger.info(
        "Incremental B trained: IC=%.4f, EWC_loss=%.4f, %.1fs",
        ic_b,
        ewc_loss,
        time.time() - t0,
    )

    return result


# ════════════════════════════════════════════════════════════════
#  7. 双模型预测 — 向后兼容
# ════════════════════════════════════════════════════════════════


def predict_dual(
    dual: DualModel,
    X: pd.DataFrame,
) -> np.ndarray:
    """双模型加权预测。

    prediction = w_A * pred_A + w_B * pred_B

    Parameters
    ----------
    dual : DualModel
        双模型状态。
    X : pd.DataFrame
        特征矩阵。

    Returns
    -------
    np.ndarray
        加权预测值。
    """
    predictions = []

    if dual.model_a is not None:
        try:
            pred_a = dual.model_a.predict(xgb.DMatrix(X[dual.feature_names]))
            predictions.append(("a", pred_a))
        except Exception as e:
            logger.debug("Model A predict failed: %s", e)

    if dual.model_b is not None:
        try:
            pred_b = dual.model_b.predict(xgb.DMatrix(X[dual.feature_names]))
            predictions.append(("b", pred_b))
        except Exception as e:
            logger.debug("Model B predict failed: %s", e)

    if not predictions:
        return np.zeros(len(X))

    combined = np.zeros(len(X))
    total_weight = 0.0

    for name, pred in predictions:
        w = dual.weight_a if name == "a" else dual.weight_b
        combined += w * pred
        total_weight += w

    if total_weight > 0:
        combined /= total_weight

    return combined


# ════════════════════════════════════════════════════════════════
#  8. 持久化 — 向后兼容
# ════════════════════════════════════════════════════════════════


def save_dual_model(
    dual: DualModel,
    cache_dir: Path,
) -> None:
    """保存双模型状态。"""
    cache_dir.mkdir(parents=True, exist_ok=True)

    if dual.model_a is not None:
        dual.model_a.save_model(str(cache_dir / "model_a.json"))

    if dual.model_b is not None:
        dual.model_b.save_model(str(cache_dir / "model_b.json"))

    meta = {
        "timestamp": time.time(),
        "weight_a": dual.weight_a,
        "weight_b": dual.weight_b,
        "ic_decay_speed": dual.ic_decay_speed,
        "a_ic": dual.a_ic,
        "b_ic": dual.b_ic,
        "b_train_count": dual.b_train_count,
        "feature_names": dual.feature_names,
        "fisher_info": dual.fisher_info,
    }
    with open(cache_dir / "dual_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    logger.info(
        "Dual model saved: w_a=%.2f, w_b=%.2f, b_trains=%d",
        dual.weight_a,
        dual.weight_b,
        dual.b_train_count,
    )


def load_dual_model(cache_dir: Path) -> DualModel | None:
    """加载双模型状态。"""
    meta_file = cache_dir / "dual_meta.json"
    if not meta_file.exists():
        return None

    try:
        with open(meta_file, encoding="utf-8") as f:
            meta = json.load(f)

        model_a = None
        model_b = None

        model_a_path = cache_dir / "model_a.json"
        if model_a_path.exists():
            model_a = xgb.Booster()
            model_a.load_model(str(model_a_path))

        model_b_path = cache_dir / "model_b.json"
        if model_b_path.exists():
            model_b = xgb.Booster()
            model_b.load_model(str(model_b_path))

        return DualModel(
            model_a=model_a,
            model_b=model_b,
            weight_a=meta.get("weight_a", 0.5),
            weight_b=meta.get("weight_b", 0.5),
            ic_decay_speed=meta.get("ic_decay_speed", 0.0),
            a_ic=meta.get("a_ic", 0.0),
            b_ic=meta.get("b_ic", 0.0),
            b_train_count=meta.get("b_train_count", 0),
            feature_names=meta.get("feature_names", []),
            fisher_info=meta.get("fisher_info", {}),
        )
    except Exception as e:
        logger.warning("Failed to load dual model: %s", e)
        return None


# ════════════════════════════════════════════════════════════════
#  9. SmartIncrementalLearner — 新增核心类
# ════════════════════════════════════════════════════════════════


class SmartIncrementalLearner:
    """智能增量学习控制器。

    集成 EWC callback 训练、IC 衰减检测、Purged TSCV 滑坡检测、
    自适应权重分配于一体。

    与现有代码的集成点：
    - 替代 trainer.py 中的 train_incremental_dual()
    - 替代 incremental_trainer.py 中的 train_incremental_b()
    - 复用 purged_tscv.py 的 PurgedTimeSeriesSplit
    - 复用 _training_commons.py 的 compute_spearmanr_safe()

    向后兼容：保留 DualModel / save_dual_model / load_dual_model /
    predict_dual / train_incremental_b 原有接口不变。
    """

    def __init__(self, cache_dir: Path, config: dict[str, Any] | None = None):
        """初始化智能增量学习器。

        Parameters
        ----------
        cache_dir : Path
            模型缓存目录。
        config : dict | None
            配置参数。支持字段：
            - ewc_lambda: EWC 正则强度 (默认 50.0)
            - fisher_samples: Fisher 计算采样数 (默认 200)
            - ic_decay_threshold: IC 衰减速度阈值 (默认 0.02)
            - ic_decay_window: IC 衰减计算窗口 (默认 20)
            - slide_threshold: 性能滑坡检测阈值 (默认 0.3)
            - slide_n_splits: Purged TSCV 折数 (默认 5)
            - slide_purge_days: Purge 间隔 (默认 5)
            - slide_embargo_days: Embargo 间隔 (默认 15)
            - incremental_rounds: B 模型最大 boosting 轮数 (默认 100)
            - a_retrain_days: A 模型全量重训周期 (默认 7)
            - b_retrain_on_slide: 滑坡时 B 全量重训 (默认 True)
            - weight_boost_on_decay: IC 衰减时 B 权重 (默认 0.7)
            - weight_normal: 正常时 A 权重 (默认 0.7)
            - min_weight_b: B 模型最低权重 (默认 0.1)
        """
        self.cache_dir = cache_dir
        cfg = config or {}
        self.ewc_lambda: float = cfg.get("ewc_lambda", 50.0)
        self.fisher_samples: int = cfg.get("fisher_samples", 200)
        self.decay_threshold: float = cfg.get("ic_decay_threshold", 0.02)
        self.decay_window: int = cfg.get("ic_decay_window", 20)
        self.slide_threshold: float = cfg.get("slide_threshold", 0.3)
        self.slide_n_splits: int = cfg.get("slide_n_splits", 5)
        self.slide_purge_days: int = cfg.get("slide_purge_days", 5)
        self.slide_embargo_days: int = cfg.get("slide_embargo_days", 15)
        self.n_rounds: int = cfg.get("incremental_rounds", 100)
        self.a_retrain_days: int = cfg.get("a_retrain_days", 7)
        self.b_retrain_on_slide: bool = cfg.get("b_retrain_on_slide", True)
        self.weight_boost_on_decay: float = cfg.get("weight_boost_on_decay", 0.7)
        self.weight_normal: float = cfg.get("weight_normal", 0.7)
        self.min_weight_b: float = cfg.get("min_weight_b", 0.1)

    def train(
        self,
        model_a: xgb.Booster | None,
        X_full: pd.DataFrame,
        y_full: pd.Series,
        X_new: pd.DataFrame,
        y_new: pd.Series,
        feature_names: list[str],
        xgb_params: dict[str, Any],
        date_index: pd.DatetimeIndex | None = None,
    ) -> SmartDualModel:
        """完整的智能增量训练流程。

        1. 训练/刷新 Model A（长期全量模型）
        2. 计算 Fisher 信息
        3. 训练 Model B（EWC 正则增量）
        4. 性能滑坡检测（Purged TSCV）
        5. 计算 IC 和衰减速度
        6. 计算集成权重
        7. 保存结果

        Parameters
        ----------
        model_a : xgb.Booster | None
            已有的 A 模型。None 则从零训练。
        X_full, y_full : 全量训练数据。
        X_new, y_new : 增量训练数据。
        feature_names : 特征名列表。
        xgb_params : XGBoost 参数。
        date_index : 日期索引（用于 IC 衰减计算）。

        Returns
        -------
        SmartDualModel
            更新后的双模型。
        """
        # ─── Step 1: Model A ───
        if model_a is None:
            logger.info("Training Model A from scratch on full data (%d samples)", len(X_full))
            model_a = self._train_model_a(X_full, y_full, feature_names, xgb_params)

        # ─── Step 2: Fisher 信息 ───
        fisher_info = compute_fisher_diagonal(
            model_a, X_full, feature_names, n_samples=self.fisher_samples
        )

        # ─── Step 3: Model B（EWC 正则增量）───
        model_b, ewc_loss = self._train_b_with_ewc(
            model_a,
            X_new,
            y_new,
            feature_names,
            fisher_info,
            xgb_params,
        )

        # ─── Step 4: 性能滑坡检测 ───
        slide_detected = False
        if date_index is not None and len(X_new) > 100:
            slide_detected, slide_ratio, fold_ics = detect_performance_slide(
                model_b,
                X_new,
                y_new,
                feature_names,
                n_splits=self.slide_n_splits,
                purge_days=self.slide_purge_days,
                embargo_days=self.slide_embargo_days,
                slide_threshold=self.slide_threshold,
            )
            if slide_detected and self.b_retrain_on_slide:
                logger.warning(
                    "Performance slide detected (ratio=%.3f), retraining Model B from scratch",
                    slide_ratio,
                )
                model_b = self._train_model_b_from_scratch(X_new, y_new, feature_names, xgb_params)

        # ─── Step 5: IC 和衰减速度 ───
        a_ic = self._compute_ic(model_a, X_new, y_new, feature_names)
        b_ic = self._compute_ic(model_b, X_new, y_new, feature_names)

        ic_decay_speed = 0.0
        if date_index is not None:
            ic_decay_speed = self._compute_decay_speed_from_date_index(
                model_a, X_new, y_new, feature_names, date_index
            )

        # ─── Step 6: 集成权重 ───
        weight_a, weight_b = self._compute_smart_weights(ic_decay_speed, a_ic, b_ic)

        # ─── Step 7: 构建结果 ───
        dual = SmartDualModel(
            model_a=model_a,
            model_b=model_b,
            weight_a=weight_a,
            weight_b=weight_b,
            fisher_info=fisher_info,
            feature_names=feature_names,
            a_ic=a_ic,
            b_ic=b_ic,
            ic_decay_speed=ic_decay_speed,
            b_train_count=self._get_b_train_count() + 1,
            last_train_time=datetime.now().isoformat(),
            ewc_loss=ewc_loss,
            slide_detected=slide_detected,
        )

        self._save_smart(dual)
        return dual

    def predict(self, dual: SmartDualModel, X: pd.DataFrame) -> np.ndarray:
        """双模型加权预测。"""
        dmatrix = xgb.DMatrix(X[dual.feature_names])

        preds_a = dual.model_a.predict(dmatrix)
        preds_b = dual.model_b.predict(dmatrix) if dual.model_b is not None else 0

        denom = dual.weight_a + dual.weight_b
        combined = (
            (dual.weight_a * preds_a + dual.weight_b * preds_b) / denom if denom > 0 else preds_a
        )

        return combined

    # ── 内部方法 ──────────────────────────────────────────────────

    def _train_model_a(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        feature_names: list[str],
        xgb_params: dict[str, Any],
    ) -> xgb.Booster:
        """训练 Model A：全量数据 + 标准 CV。"""
        from aimoon.ml.training_loop import run_cv_training

        final_model, _, _, _, best_round, _, _, _, _ = run_cv_training(
            X,
            y,
            xgb_params,
            feature_names,
            forward_days=22,
            save_dir=None,
        )
        return final_model

    def _train_b_with_ewc(
        self,
        model_a: xgb.Booster,
        X_new: pd.DataFrame,
        y_new: pd.Series,
        feature_names: list[str],
        fisher_info: dict[str, float],
        xgb_params: dict[str, Any],
    ) -> tuple[xgb.Booster, float]:
        """训练 Model B，使用 EWC callback 防止遗忘。

        核心思路：通过自定义 callback 监控 EWC 损失，
        当 EWC 损失持续上升时停止训练。
        """
        a_params = extract_model_params(model_a)

        dtrain = xgb.DMatrix(X_new[feature_names], label=y_new.values)

        b_params = {
            **xgb_params,
            "learning_rate": xgb_params.get("learning_rate", 0.01) * 0.5,
            "reg_lambda": xgb_params.get("reg_lambda", 2.0) * 2.0,
            "max_depth": min(xgb_params.get("max_depth", 2), 2),
        }
        b_params.pop("n_estimators", None)
        b_params.pop("early_stopping_rounds", None)

        ewc_state: dict[str, Any] = {"best_loss": float("inf"), "best_model": None, "patience": 0}

        def ewc_callback(env: Any) -> None:
            """XGBoost callback: 每 10 轮评估 EWC 损失。"""
            if env.iteration % 10 != 0 or env.iteration == 0:
                return

            try:
                current = env.model
                b_params_current = extract_model_params(current)
                penalty = compute_ewc_penalty(
                    b_params_current, a_params, fisher_info, self.ewc_lambda
                )

                val_loss = env.evaluation_result_list[-1][1] if env.evaluation_result_list else 0.0
                total_loss = val_loss + penalty

                if total_loss < ewc_state["best_loss"]:
                    ewc_state["best_loss"] = total_loss
                    ewc_state["best_model"] = current.copy()
                    ewc_state["patience"] = 0
                else:
                    ewc_state["patience"] += 1

                if ewc_state["patience"] >= 3:
                    env.model = ewc_state["best_model"]
                    raise xgb.core.XGBoostError("__early_stop__")
            except xgb.core.XGBoostError:
                raise
            except Exception:
                pass

        try:
            model_b = xgb.train(
                b_params,
                dtrain,
                num_boost_round=self.n_rounds,
                callbacks=[ewc_callback],
                verbose_eval=False,
            )
            ewc_loss_val = ewc_state["best_loss"]
        except xgb.core.XGBoostError:
            best_model = ewc_state["best_model"]
            fallback = best_model if best_model is not None else model_a.copy()
            model_b = fallback
            ewc_loss_val = ewc_state["best_loss"] if ewc_state["best_loss"] < float("inf") else 0.0

        return model_b, ewc_loss_val

    def _train_model_b_from_scratch(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        feature_names: list[str],
        xgb_params: dict[str, Any],
    ) -> xgb.Booster:
        """Model B 全量重训（性能滑坡时触发）。"""
        b_params = {
            **xgb_params,
            "learning_rate": xgb_params.get("learning_rate", 0.01) * 0.5,
            "reg_lambda": xgb_params.get("reg_lambda", 2.0) * 2.0,
        }
        b_params.pop("n_estimators", None)
        b_params.pop("early_stopping_rounds", None)

        dtrain = xgb.DMatrix(X[feature_names], label=y.values)
        return xgb.train(b_params, dtrain, num_boost_round=self.n_rounds, verbose_eval=False)

    def _compute_ic(
        self,
        model: xgb.Booster,
        X: pd.DataFrame,
        y: pd.Series,
        feature_names: list[str],
    ) -> float:
        """计算模型在数据上的 IC。"""
        dmatrix = xgb.DMatrix(X[feature_names])
        preds = model.predict(dmatrix)
        return compute_spearmanr_safe(preds, y.values)

    def _compute_decay_speed_from_date_index(
        self,
        model: xgb.Booster,
        X: pd.DataFrame,
        y: pd.Series,
        feature_names: list[str],
        date_index: pd.DatetimeIndex,
    ) -> float:
        """按日期分组计算每日 IC，然后线性回归求衰减速度。"""
        dmatrix = xgb.DMatrix(X[feature_names])
        predictions = model.predict(dmatrix)

        daily_ics: list[float] = []
        unique_dates = date_index.unique()[-self.decay_window :]
        for date in unique_dates:
            mask = date_index == date
            if mask.sum() < 10:
                continue
            day_preds = predictions[mask]
            day_labels = y.values[mask]
            ic = compute_spearmanr_safe(day_preds, day_labels)
            daily_ics.append(ic)

        return compute_ic_decay_speed(daily_ics, window=self.decay_window)

    def _compute_smart_weights(
        self,
        ic_decay_speed: float,
        a_ic: float,
        b_ic: float,
    ) -> tuple[float, float]:
        """计算 Model A 和 Model B 的集成权重。"""
        if ic_decay_speed < -self.decay_threshold:
            weight_b = self.weight_boost_on_decay
            weight_a = 1.0 - weight_b
        else:
            weight_a = self.weight_normal
            weight_b = 1.0 - weight_a

        if b_ic <= 0:
            weight_b = self.min_weight_b
            weight_a = 1.0 - weight_b
        elif a_ic > 0 and b_ic / a_ic < 0.5:
            weight_b *= 0.5
            weight_a = 1.0 - weight_b

        total = weight_a + weight_b
        return weight_a / total, weight_b / total

    def _get_b_train_count(self) -> int:
        """从磁盘读取 B 模型累计训练次数。"""
        meta_path = self.cache_dir / "dual" / "dual_meta.json"
        if meta_path.exists():
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                return meta.get("b_train_count", 0)
            except Exception:
                pass
        return 0

    def _save_smart(self, dual: SmartDualModel) -> None:
        """保存 SmartDualModel 到磁盘。"""
        save_dir = self.cache_dir / "dual"
        save_dir.mkdir(parents=True, exist_ok=True)

        dual.model_a.save_model(str(save_dir / "model_a.json"))
        if dual.model_b is not None:
            dual.model_b.save_model(str(save_dir / "model_b.json"))

        meta = {
            "timestamp": time.time(),
            "weight_a": dual.weight_a,
            "weight_b": dual.weight_b,
            "ic_decay_speed": dual.ic_decay_speed,
            "a_ic": dual.a_ic,
            "b_ic": dual.b_ic,
            "b_train_count": dual.b_train_count,
            "last_train_time": dual.last_train_time,
            "ewc_loss": dual.ewc_loss,
            "slide_detected": dual.slide_detected,
            "feature_names": dual.feature_names,
            "fisher_info": dual.fisher_info,
        }
        with open(save_dir / "dual_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        logger.info(
            "SmartDualModel saved: A_ic=%.4f, B_ic=%.4f, w_a=%.2f, w_b=%.2f, "
            "decay=%.4f, slide=%s",
            dual.a_ic,
            dual.b_ic,
            dual.weight_a,
            dual.weight_b,
            dual.ic_decay_speed,
            dual.slide_detected,
        )

    @classmethod
    def load(
        cls,
        cache_dir: Path,
        config: dict[str, Any] | None = None,
    ) -> tuple[SmartIncrementalLearner, SmartDualModel | None]:
        """从磁盘加载 SmartIncrementalLearner 和模型。

        Returns (learner, dual_model)。dual_model 为 None 表示无缓存。
        """
        learner = cls(cache_dir, config)
        save_dir = cache_dir / "ml" / "dual"
        meta_path = save_dir / "dual_meta.json"

        if not meta_path.exists():
            return learner, None

        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)

            model_a = xgb.Booster()
            model_a.load_model(str(save_dir / "model_a.json"))

            model_b = None
            model_b_path = save_dir / "model_b.json"
            if model_b_path.exists():
                model_b = xgb.Booster()
                model_b.load_model(str(model_b_path))

            dual = SmartDualModel(
                model_a=model_a,
                model_b=model_b,
                weight_a=meta.get("weight_a", 0.7),
                weight_b=meta.get("weight_b", 0.3),
                fisher_info=meta.get("fisher_info", {}),
                feature_names=meta.get("feature_names", []),
                a_ic=meta.get("a_ic", 0.0),
                b_ic=meta.get("b_ic", 0.0),
                ic_decay_speed=meta.get("ic_decay_speed", 0.0),
                b_train_count=meta.get("b_train_count", 0),
                last_train_time=meta.get("last_train_time", ""),
                ewc_loss=meta.get("ewc_loss", 0.0),
                slide_detected=meta.get("slide_detected", False),
            )
            return learner, dual
        except Exception as e:
            logger.warning("Failed to load SmartDualModel: %s", e)
            return learner, None
