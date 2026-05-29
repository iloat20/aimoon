"""核心数据模型 — Signal 和 ScoredStock"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Signal:
    """一个评分信号。name 机器可读，label 人类可读。"""
    name: str
    label: str
    score: int


@dataclass(frozen=True)
class ScoredStock:
    """一只股票的完整评分结果。"""
    code: str
    name: str
    price: float
    pct_change: float
    turnover: float
    pe: float = 0.0
    pb: float = 0.0
    market_cap_yi: float = 0.0
    signals: tuple[Signal, ...] = ()
    rps: dict[str, float] = field(default_factory=dict)

    @property
    def total_score(self) -> int:
        return sum(s.score for s in self.signals)

    @property
    def suggestion(self) -> tuple[str, str]:
        """返回 (建议, 置信度)。"""
        t = self.total_score
        if t >= 8:   return "强烈买入", "高"
        if t >= 5:   return "买入", "中高"
        if t >= 2:   return "建议买入", "中"
        if t >= 0:   return "观望", "低"
        if t >= -3:  return "谨慎", "中"
        if t >= -6:  return "建议卖出", "中高"
        return "强烈卖出", "高"
