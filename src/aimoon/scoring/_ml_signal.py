"""ML信号创建 — 消除 screener.py 和 service.py 中的重复逻辑。

Score 范围: ml_score (0-100) 映射为 alpha_score [-40, +40]。
hybrid_scorer._compute_ml_score 期望 Signal.score 为 alpha_score。
"""

from __future__ import annotations

from aimoon.models import Signal


def create_ml_signal(ml_score: int) -> Signal | None:
    """将 ML 百分位分数 (0-100) 转换为 Signal。

    映射: ml_score 0-100 => alpha_score [-40, +40]
    公式: alpha_score = clamp((ml_score - 50) * 0.8, -40, 40)
    """
    alpha_score = int((ml_score - 50) * 0.8)
    alpha_score = max(-40, min(40, alpha_score))

    if ml_score >= 80:
        desc = f"ml_rank_{ml_score}(强烈看多)"
    elif ml_score >= 60:
        desc = f"ml_rank_{ml_score}(看多)"
    elif ml_score <= 20:
        desc = f"ml_rank_{ml_score}(强烈看空)"
    elif ml_score <= 40:
        desc = f"ml_rank_{ml_score}(看空)"
    else:
        desc = f"ml_rank_{ml_score}(中性)"

    return Signal("ml_rank", desc, alpha_score, category="ml")
