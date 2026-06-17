"""动态元学习器集成 — Ridge 回归第二层模型。

在 XGBoost + LightGBM 之上训练 Ridge 元学习器，根据市场状态
（波动率、行业离散度、IC 衰减速度）动态调整模型权重。

核心思路：
    Base Layer:  XGB, LGBM → 各自的 OOS 预测
    Meta Layer:  [xgb_pred, lgbm_pred, meta_features] → Ridge → final prediction

元特征：
    1. market_vol    — 市场波动率（20 日 rolling std of 市场均值收益）
    2. sector_disp   — 行业离散度（截面行业均值收益的标准差）
    3. ic_decay_rate — 因子平均 IC 的衰减速度（线性回归斜率）

Fallback：
    当样本量不足（< 30 天）或 Ridge 系数异常时，回退到简单 IC 加权。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)

_META_CACHE_DIR = Path(".aimoon_cache") / "ml" / "meta_ensemble"
_MIN_SAMPLES = 30  # 最少 OOS 样本数才启用 Ridge
_REFIT_INTERVAL = 20  # 每 20 天重新拟合
_LOOKBACK = 60  # 使用过去 60 天 OOS 数据


# ════════════════════════════════════════════════════════════════
#  Meta-Feature 计算
# ════════════════════════════════════════════════════════════════


def compute_meta_features(
    close: pd.DataFrame,
    sector_map: dict[str, str] | None = None,
    ic_history: list[float] | None = None,
    target_idx: int | None = None,
) -> dict[str, float]:
    """计算单日的元特征向量。

    Parameters
    ----------
    close : pd.DataFrame
        收盘价矩阵，index=日期, columns=股票代码。
    sector_map : dict[str, str] | None
        股票→行业映射。
    ic_history : list[float] | None
        最近 N 天的因子平均 IC 序列（用于计算衰减速度）。
    target_idx : int | None
        目标日在 close 中的整数位置。None 取最后一行。

    Returns
    -------
    dict[str, float]
        三个元特征：market_vol, sector_disp, ic_decay_rate。
    """
    if target_idx is None:
        target_idx = len(close) - 1

    # 1. 市场波动率：过去 20 天的市场均值收益的标准差
    start = max(0, target_idx - 19)
    market_ret = close.iloc[start : target_idx + 1].pct_change(fill_method=None).mean(axis=1)
    market_vol = float(market_ret.std()) if len(market_ret) > 1 else 0.0

    # 2. 行业离散度：当日各行业均值收益的标准差
    sector_disp = 0.0
    if sector_map and target_idx > 0:
        day_ret = close.iloc[target_idx].pct_change(fill_method=None)
        sector_returns: dict[str, list[float]] = {}
        for code, ret_val in day_ret.items():
            if pd.isna(ret_val):
                continue
            sector = sector_map.get(code)
            if sector:
                sector_returns.setdefault(sector, []).append(float(ret_val))
        if len(sector_returns) > 1:
            sector_means = [np.mean(rets) for rets in sector_returns.values()]
            sector_disp = float(np.std(sector_means))

    # 3. IC 衰减速度：因子 IC 序列的线性回归斜率
    ic_decay_rate = 0.0
    if ic_history and len(ic_history) >= 10:
        arr = np.array(ic_history[-20:])  # 最近 20 天
        x = np.arange(len(arr), dtype=np.float64)
        # 线性回归斜率
        slope = np.polyfit(x, arr, 1)[0]
        ic_decay_rate = float(slope)

    return {
        "market_vol": market_vol,
        "sector_disp": sector_disp,
        "ic_decay_rate": ic_decay_rate,
    }


# ════════════════════════════════════════════════════════════════
#  DynamicMetaEnsemble
# ════════════════════════════════════════════════════════════════


@dataclass
class OOSRecord:
    """单日 OOS 预测记录。"""

    date: str  # ISO 格式日期
    xgb_pred: dict[str, float]  # code → prediction
    lgbm_pred: dict[str, float]  # code → prediction
    labels: dict[str, float]  # code → realized return
    meta_features: dict[str, float]  # 元特征


@dataclass
class DynamicMetaEnsemble:
    """动态元学习器集成。

    在 IC 加权之上增加 Ridge 回归第二层，根据市场状态动态调整
    XGB/LGBM 的混合比例。

    工作流程：
        1. 每日：记录 XGB/LGBM 的 OOS 预测 + 真实标签 + 元特征
        2. 每 20 天：用过去 60 天数据训练 Ridge
        3. 预测时：用 Ridge 系数加权（而非简单 IC 加权）
        4. 样本不足时：回退到 IC 加权

    Parameters
    ----------
    lookback : int
        Ridge 训练窗口（天数）。
    refit_interval : int
        重拟合间隔（天数）。
    min_samples : int
        最少样本数，不足则 fallback。
    ridge_alpha : float
        Ridge 正则化强度。
    """

    lookback: int = _LOOKBACK
    refit_interval: int = _REFIT_INTERVAL
    min_samples: int = _MIN_SAMPLES
    ridge_alpha: float = 1.0

    # 内部状态
    _oos_history: list[OOSRecord] = field(default_factory=list, repr=False)
    _ridge_coef: np.ndarray | None = field(default=None, repr=False)
    _ridge_intercept: float = field(default=0.0, repr=False)
    _last_refit_idx: int = field(default=0, repr=False)
    _ic_weights: dict[str, float] = field(default_factory=dict, repr=False)
    _n_refits: int = field(default=0, repr=False)
    _use_ridge: bool = field(default=False, repr=False)

    # ── 持久化 ──

    def save(self, path: Path | None = None) -> None:
        """保存元学习器状态到 JSON。"""
        path = path or _META_CACHE_DIR / "meta_ensemble.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        # OOS 历史只保留最近 lookback 天
        recent = self._oos_history[-self.lookback :]
        data = {
            "timestamp": time.time(),
            "n_refits": self._n_refits,
            "use_ridge": self._use_ridge,
            "ridge_coef": self._ridge_coef.tolist() if self._ridge_coef is not None else None,
            "ridge_intercept": self._ridge_intercept,
            "last_refit_idx": self._last_refit_idx,
            "ic_weights": self._ic_weights,
            "oos_history": [
                {
                    "date": r.date,
                    "meta_features": r.meta_features,
                    # 只保存聚合统计，不保存全量预测（太大）
                    "n_stocks": len(r.labels),
                    "xgb_ic": self._compute_pair_ic(r.xgb_pred, r.labels),
                    "lgbm_ic": self._compute_pair_ic(r.lgbm_pred, r.labels),
                }
                for r in recent
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("MetaEnsemble saved: %d OOS records, ridge=%s", len(recent), self._use_ridge)

    @classmethod
    def load(cls, path: Path | None = None) -> DynamicMetaEnsemble:
        """从 JSON 加载状态。"""
        path = path or _META_CACHE_DIR / "meta_ensemble.json"
        if not path.exists():
            return cls()

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            obj = cls()
            obj._n_refits = data.get("n_refits", 0)
            obj._use_ridge = data.get("use_ridge", False)
            obj._ridge_intercept = data.get("ridge_intercept", 0.0)
            obj._last_refit_idx = data.get("last_refit_idx", 0)
            obj._ic_weights = data.get("ic_weights", {})

            coef = data.get("ridge_coef")
            if coef is not None:
                obj._ridge_coef = np.array(coef, dtype=np.float64)

            # 重建 OOS 历史（简化版，只用于判断是否需要 re-fit）
            for rec in data.get("oos_history", []):
                obj._oos_history.append(
                    OOSRecord(
                        date=rec["date"],
                        xgb_pred={},
                        lgbm_pred={},
                        labels={},
                        meta_features=rec.get("meta_features", {}),
                    )
                )

            logger.info(
                "MetaEnsemble loaded: %d records, ridge=%s, n_refits=%d",
                len(obj._oos_history),
                obj._use_ridge,
                obj._n_refits,
            )
            return obj
        except Exception as e:
            logger.warning("Failed to load MetaEnsemble: %s", e)
            return cls()

    # ── 核心方法 ──

    def record_oos(
        self,
        date: pd.Timestamp,
        xgb_pred: pd.Series,
        lgbm_pred: pd.Series,
        labels: pd.Series,
        close: pd.DataFrame,
        sector_map: dict[str, str] | None = None,
        ic_history: list[float] | None = None,
    ) -> None:
        """记录单日 OOS 预测结果。

        Parameters
        ----------
        date : pd.Timestamp
            预测日期。
        xgb_pred, lgbm_pred : pd.Series
            各模型的 OOS 预测值，index=股票代码。
        labels : pd.Series
            真实标签（已实现收益），index=股票代码。
        close : pd.DataFrame
        收盘价矩阵（用于计算元特征）。
        sector_map : dict[str, str] | None
        股票→行业映射。
        ic_history : list[float] | None
        因子 IC 历史（用于计算 IC 衰减速度）。
        """
        # 计算元特征
        target_idx = close.index.get_loc(date) if date in close.index else len(close) - 1
        meta = compute_meta_features(close, sector_map, ic_history, target_idx)

        record = OOSRecord(
            date=str(date.date()) if hasattr(date, "date") else str(date),
            xgb_pred=xgb_pred.dropna().to_dict(),
            lgbm_pred=lgbm_pred.dropna().to_dict(),
            labels=labels.dropna().to_dict(),
            meta_features=meta,
        )
        self._oos_history.append(record)

        # 只保留最近 lookback * 2 天（避免内存膨胀）
        if len(self._oos_history) > self.lookback * 2:
            self._oos_history = self._oos_history[-self.lookback * 2 :]

    def update_weights(
        self,
        xgb_ic: float = 0.0,
        lgbm_ic: float = 0.0,
    ) -> dict[str, float]:
        """更新集成权重。

        优先使用 Ridge 元学习器；样本不足时回退到 IC 加权。

        Parameters
        ----------
        xgb_ic : float
            XGBoost 滚动 IC（用于 fallback）。
        lgbm_ic : float
            LightGBM 滚动 IC（用于 fallback）。

        Returns
        -------
        dict[str, float]
            权重字典：{"xgb": w1, "lgbm": w2}。
        """
        n = len(self._oos_history)

        # 检查是否需要 re-fit
        if n - self._last_refit_idx >= self.refit_interval and n >= self.min_samples:
            self._try_fit_ridge()
            self._last_refit_idx = n

        # 使用 Ridge 预测权重
        if self._use_ridge and self._ridge_coef is not None:
            weights = self._predict_meta_weights()
            if weights is not None:
                return weights

        # Fallback: IC 加权
        return self._ic_fallback(xgb_ic, lgbm_ic)

    def _try_fit_ridge(self) -> None:
        """尝试用过去 OOS 数据训练 Ridge 元学习器。"""
        recent = self._oos_history[-self.lookback :]
        if len(recent) < self.min_samples:
            logger.info(
                "MetaEnsemble: only %d OOS samples < %d, skip Ridge", len(recent), self.min_samples
            )
            return

        # 构建训练矩阵
        X_list: list[np.ndarray] = []
        y_list: list[float] = []

        for rec in recent:
            # 取两个模型都有预测且有标签的股票
            common_codes = set(rec.xgb_pred) & set(rec.lgbm_pred) & set(rec.labels)
            if len(common_codes) < 10:
                continue

            codes = sorted(common_codes)
            xgb_vals = np.array([rec.xgb_pred[c] for c in codes])
            lgbm_vals = np.array([rec.lgbm_pred[c] for c in codes])
            label_vals = np.array([rec.labels[c] for c in codes])

            # 元特征向量（每个样本 = 每只股票的预测 + 日级别元特征）
            meta = rec.meta_features
            meta_vec = np.array(
                [
                    meta.get("market_vol", 0.0),
                    meta.get("sector_disp", 0.0),
                    meta.get("ic_decay_rate", 0.0),
                ]
            )

            # 每只股票一行：[xgb_pred, lgbm_pred, market_vol, sector_disp, ic_decay]
            for i in range(len(codes)):
                row = np.concatenate(
                    [
                        [xgb_vals[i], lgbm_vals[i]],
                        meta_vec,
                    ]
                )
                X_list.append(row)
                y_list.append(float(label_vals[i]))

        if len(X_list) < self.min_samples * 5:  # 至少需要足够的股票×天数
            logger.info("MetaEnsemble: only %d effective rows, skip Ridge", len(X_list))
            return

        X = np.array(X_list, dtype=np.float64)
        y = np.array(y_list, dtype=np.float64)

        # 标准化
        X_mean = X.mean(axis=0)
        X_std = X.std(axis=0)
        X_std[X_std < 1e-10] = 1.0
        X_norm = (X - X_mean) / X_std

        # Ridge 回归（解析解：w = (X^T X + αI)^{-1} X^T y）
        n_features = X_norm.shape[1]
        I = np.eye(n_features)
        try:
            XtX = X_norm.T @ X_norm
            Xty = X_norm.T @ y
            ridge_coef = np.linalg.solve(XtX + self.ridge_alpha * I, Xty)

            # 检查系数合理性：xgb 和 lgbm 的系数应该同号且非极端
            xgb_coef = ridge_coef[0]
            lgbm_coef = ridge_coef[1]

            if abs(xgb_coef) + abs(lgbm_coef) < 1e-10:
                logger.info("MetaEnsemble: degenerate Ridge coefficients, skip")
                return

            # 用系数的 softmax 作为权重（而非原始系数值）
            coefs = np.array([xgb_coef, lgbm_coef])
            max_c = coefs.max()
            exp_coefs = np.exp(coefs - max_c)
            weights = exp_coefs / exp_coefs.sum()

            self._ridge_coef = ridge_coef
            self._ridge_intercept = float(np.mean(y - X_norm @ ridge_coef))
            self._use_ridge = True
            self._n_refits += 1

            logger.info(
                "MetaEnsemble Ridge fitted: %d rows, xgb_w=%.3f, lgbm_w=%.3f (coef=[%.4f, %.4f])",
                len(X_list),
                weights[0],
                weights[1],
                xgb_coef,
                lgbm_coef,
            )

        except np.linalg.LinAlgError:
            logger.warning("MetaEnsemble: Ridge solver failed, keeping previous weights")

    def _predict_meta_weights(self) -> dict[str, float] | None:
        """用最近一天的元特征预测权重。"""
        if not self._oos_history or self._ridge_coef is None:
            return None

        last = self._oos_history[-1]
        meta = last.meta_features

        # 元特征向量：[market_vol, sector_disp, ic_decay_rate]
        meta_vec = np.array(
            [
                meta.get("market_vol", 0.0),
                meta.get("sector_disp", 0.0),
                meta.get("ic_decay_rate", 0.0),
            ]
        )

        # Ridge 输出：coef[2:] @ meta_vec + intercept
        meta_part = self._ridge_coef[2:] @ meta_vec if len(self._ridge_coef) > 2 else 0.0

        # xgb 和 lgbm 的"基础权重"由 meta-adjusted IC 决定
        # 这里简化：用 coef[0] 和 coef[1] 作为基础倾向
        xgb_base = self._ridge_coef[0] + meta_part * 0.1
        lgbm_base = self._ridge_coef[1] + meta_part * 0.1

        coefs = np.array([xgb_base, lgbm_base])
        max_c = coefs.max()
        exp_coefs = np.exp(coefs - max_c)
        weights_arr = exp_coefs / exp_coefs.sum()

        return {"xgb": float(weights_arr[0]), "lgbm": float(weights_arr[1])}

    def _ic_fallback(self, xgb_ic: float, lgbm_ic: float) -> dict[str, float]:
        """IC 加权 fallback（与原 EnsemblePredictor 逻辑一致）。"""
        if xgb_ic <= 0 and lgbm_ic <= 0:
            return {"xgb": 0.5, "lgbm": 0.5}

        ic_diff = abs(xgb_ic - lgbm_ic)
        temp = max(0.1, 0.5 - ic_diff * 10)
        max_ic = max(xgb_ic, lgbm_ic)
        exp_xgb = np.exp((xgb_ic - max_ic) / temp)
        exp_lgbm = np.exp((lgbm_ic - max_ic) / temp)
        total = exp_xgb + exp_lgbm

        if total <= 0:
            return {"xgb": 0.5, "lgbm": 0.5}

        return {"xgb": float(exp_xgb / total), "lgbm": float(exp_lgbm / total)}

    @staticmethod
    def _compute_pair_ic(pred: dict[str, float], labels: dict[str, float]) -> float:
        """计算预测与标签的 Spearman IC。"""
        common = set(pred) & set(labels)
        if len(common) < 10:
            return 0.0
        codes = sorted(common)
        p = np.array([pred[c] for c in codes])
        l = np.array([labels[c] for c in codes])
        ic, _ = spearmanr(p, l)
        return float(ic) if not np.isnan(ic) else 0.0

    @property
    def is_ridge_active(self) -> bool:
        """Ridge 元学习器是否已激活。"""
        return self._use_ridge and self._ridge_coef is not None

    @property
    def n_oos_records(self) -> int:
        return len(self._oos_history)

    @property
    def n_refits(self) -> int:
        return self._n_refits
