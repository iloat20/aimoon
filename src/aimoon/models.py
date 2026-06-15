"""核心数据模型 — Signal 和 ScoredStock"""

from __future__ import annotations

from dataclasses import dataclass, field

# 建议阈值和置信度由 hybrid_scorer.get_suggestion 统一管理


@dataclass(frozen=True)
class Signal:
    """一个评分信号。name 机器可读，label 人类可读。

    category 决定信号在混合评分中的分组：
    - "ml"      — ML 模型信号（由 create_ml_signal 创建）
    - "alpha"   — Alpha Zoo 因子信号
    - "momentum"— 技术指标信号（动量/趋势/成交量等，默认值）
    """

    name: str
    label: str
    score: int
    category: str = "momentum"


@dataclass(frozen=True)
class ScoredStock:
    """一只股票的完整评分结果。

    注意: frozen=True 仅冻结字段绑定，不冻结字段值本身的可变性。
    ``rps`` 字段虽然默认为 dict，但通过 ``field(default_factory=dict)``
    确保每个实例获得独立副本。调用方应避免直接修改 rps 的内容；
    如需更新，使用 ``dataclasses.replace`` 或 ``ScoredStock`` 的 ``replace``。
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
    ml_score: int | None = None  # ML 模型直接给出的分数，优先于 100 分制
    hybrid_score: int | None = None  # 混合评分（0-100）
    total_score: int = 0  # 由 scoring 层计算并注入

    def replace(self, **changes: object) -> ScoredStock:
        """Return a copy with the given fields replaced.

        Example::

            new_stock = scored.replace(signals=tuple(adj_signals))
        """
        from dataclasses import replace as _replace

        return _replace(self, **changes)  # type: ignore[arg-type]
