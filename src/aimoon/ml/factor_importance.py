"""因子重要性评估框架 — SHAP 值 + Lasso 自适应选择。

提供数据驱动的因子重要性评估方法，用于：
1. 评估每个因子对 ML 模型的贡献（SHAP 值）
2. 自动选择有效因子，去除冗余（Lasso 正则化）
3. 生成因子重要性报告
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# ── Lasso 自适应因子选择 ──


def adaptive_factor_selection(
    X: pd.DataFrame,
    y: pd.Series,
    alpha: float = 0.01,
    cv: int = 5,
    random_state: int = 42,
) -> list[str]:
    """使用 Lasso L1 正则化自动选择有效因子。

    Lasso 会将不重要因子的系数压缩到 0，实现自动特征选择。

    Parameters
    ----------
    X : pd.DataFrame
        特征矩阵（行=样本，列=因子）。
    y : pd.Series
        目标变量（前瞻收益）。
    alpha : float
        正则化强度。越大越稀疏（更少因子被选中）。默认 0.01。
    cv : int
        交叉验证折数。默认 5。
    random_state : int
        随机种子。

    Returns
    -------
    list[str]
        被选中的因子名称列表。
    """
    from sklearn.linear_model import LassoCV

    if X.empty or y.empty:
        return []

    # 标准化特征
    X_std = (X - X.mean()) / X.std()
    X_std = X_std.fillna(0.0)

    try:
        lasso = LassoCV(cv=cv, random_state=random_state, max_iter=5000)
        lasso.fit(X_std, y)

        # 选择系数非零的因子
        coef_series = pd.Series(lasso.coef_, index=X.columns)
        selected = coef_series[coef_series != 0].index.tolist()

        logger.info(
            "Lasso 选择: %d/%d 因子 (alpha=%.4f, best_alpha=%.4f)",
            len(selected),
            len(X.columns),
            alpha,
            lasso.alpha_,
        )
        return selected

    except Exception as exc:
        logger.warning("Lasso 因子选择失败: %s, 返回所有因子", exc)
        return list(X.columns)


# ── 排列重要性评估 ──


def compute_permutation_importance(
    model: Any,
    X: pd.DataFrame,
    y: pd.Series,
    n_repeats: int = 10,
    random_state: int = 42,
    scoring: str = "neg_mean_squared_error",
) -> pd.Series:
    """使用排列重要性评估因子对 ML 模型的贡献。

    通过随机打乱每个因子的值，观察模型性能下降程度来衡量重要性。

    Parameters
    ----------
    model : Any
        已训练的 sklearn 兼容模型。
    X : pd.DataFrame
        特征矩阵。
    y : pd.Series
        目标变量。
    n_repeats : int
        排列重复次数。默认 10。
    random_state : int
        随机种子。
    scoring : str
        评估指标。

    Returns
    -------
    pd.Series
        因子名称 -> 重要性均值（降序排列）。
    """
    try:
        from sklearn.inspection import permutation_importance
    except ImportError:
        logger.warning("sklearn.inspection 不可用，跳过排列重要性")
        return pd.Series(dtype=float)

    if X.empty or y.empty:
        return pd.Series(dtype=float)

    # 填充 NaN
    X_filled = X.fillna(0.0)

    try:
        result = permutation_importance(
            model,
            X_filled,
            y,
            n_repeats=n_repeats,
            random_state=random_state,
            scoring=scoring,
        )
        importance = pd.Series(result.importances_mean, index=X.columns)
        importance = importance.sort_values(ascending=False)

        logger.info(
            "排列重要性: top-5 = %s",
            importance.head(5).to_dict(),
        )
        return importance

    except Exception as exc:
        logger.warning("排列重要性计算失败: %s", exc)
        return pd.Series(dtype=float)


# ── 综合因子重要性报告 ──


def generate_factor_importance_report(
    model: Any,
    X: pd.DataFrame,
    y: pd.Series,
    icir_weights: dict[str, float] | None = None,
    factor_names: list[str] | None = None,
) -> pd.DataFrame:
    """生成综合因子重要性报告。

    整合多种评估方法，生成包含以下列的报告：
    - icir_weight: ICIR 权重
    - lasso_selected: 是否被 Lasso 选中
    - permutation_importance: 排列重要性
    - composite_score: 综合得分

    Parameters
    ----------
    model : Any
        已训练的 ML 模型。
    X : pd.DataFrame
        特征矩阵。
    y : pd.Series
        目标变量。
    icir_weights : dict[str, float] | None
        ICIR 权重字典。
    factor_names : list[str] | None
        因子名称列表。默认使用 X.columns。

    Returns
    -------
    pd.DataFrame
        因子重要性报告，按综合得分降序排列。
    """
    factors = factor_names or list(X.columns)
    report = pd.DataFrame(index=factors)

    # 1. ICIR 权重
    if icir_weights:
        report["icir_weight"] = pd.Series(icir_weights).reindex(factors).fillna(0.0)
    else:
        report["icir_weight"] = 0.0

    # 2. Lasso 选择
    try:
        lasso_selected = adaptive_factor_selection(X, y)
        report["lasso_selected"] = report.index.isin(lasso_selected).astype(int)
    except Exception:
        report["lasso_selected"] = 0

    # 3. 排列重要性
    perm_importance = compute_permutation_importance(model, X, y)
    report["permutation_importance"] = perm_importance.reindex(factors).fillna(0.0)

    # 4. 综合得分（归一化后加权平均）
    for col in ["icir_weight", "permutation_importance"]:
        max_val = report[col].abs().max()
        if max_val > 0:
            report[f"{col}_norm"] = report[col] / max_val
        else:
            report[f"{col}_norm"] = 0.0

    # 综合得分 = 0.4 * ICIR + 0.3 * Lasso + 0.3 * 排列重要性
    report["composite_score"] = (
        0.4 * report.get("icir_weight_norm", 0.0)
        + 0.3 * report["lasso_selected"]
        + 0.3 * report.get("permutation_importance_norm", 0.0)
    )

    # 按综合得分降序排列
    report = report.sort_values("composite_score", ascending=False)

    logger.info(
        "因子重要性报告: %d 因子, top-5 = %s",
        len(report),
        report["composite_score"].head(5).to_dict(),
    )
    return report
