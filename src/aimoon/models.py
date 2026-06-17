"""核心数据模型 — ScoredStock（ML-only 评分）

简化：删除 hybrid_score，total_score = ml_score 或 0。
signals 保留但置为空元组，不再驱动评分。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Signal:
    """一个评分信号。保留供向后兼容，新系统不再使用。"""

    name: str
    label: str
    score: int
    category: str = "momentum"


@dataclass(frozen=True)
class ScoredStock:
    """一只股票的完整评分结果。

    简化设计：
    - total_score = ml_score 或 0
    - hybrid_score 已删除
    - signals 保留但置为空元组
    - 无 ML 模型时 ml_score=None, total_score=0
    """

    code: str
    name: str
    price: float
    pct_change: float = 0.0
    turnover: float | None = None
    pe: float | None = None
    pb: float | None = None
    market_cap_yi: float | None = None
    signals: tuple[Signal, ...] = ()
    rps: dict[str, float] = field(default_factory=dict)
    ml_score: int | None = None  # ML 百分位 0-100，最终分数
    total_score: int = 0  # total_score = ml_score，无 ML 时为 0

    def replace(self, **changes: object) -> ScoredStock:
        """Return a copy with the given fields replaced."""
        from dataclasses import replace as _replace

        return _replace(self, **changes)  # type: ignore[arg-type]

