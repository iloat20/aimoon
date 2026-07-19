"""Pipeline v2 扩展工具集(6 个纯函数工具 + 1 个组合工具)。

每个工具遵循失败降级契约:异常 / 数据缺失时返回 ``{"__partial__": "<reason>"}``,
绝不抛异常到上游 orchestrator。
"""
from __future__ import annotations

from aimoon.adapters.driven.ai.tools.business_moat import run as run_business_moat
from aimoon.adapters.driven.ai.tools.fcf_dividend import run as run_fcf_dividend
from aimoon.adapters.driven.ai.tools.financial_temporal import run as run_financial_temporal
from aimoon.adapters.driven.ai.tools.margin_of_safety import run as run_margin_of_safety
from aimoon.adapters.driven.ai.tools.peer_compare import run as run_peer_compare
from aimoon.adapters.driven.ai.tools.risk_quant import run as run_risk_quant
from aimoon.adapters.driven.ai.tools.sentiment import run as run_sentiment
from aimoon.adapters.driven.ai.tools.technicals import run as run_technicals

__all__ = [
    "run_technicals",
    "run_financial_temporal",
    "run_peer_compare",
    "run_business_moat",
    "run_risk_quant",
    "run_sentiment",
    "run_margin_of_safety",
    "TOOL_RUNNERS",
]

# 便捷名映射(和 PhaseSpec.tools 名称一致)
TOOL_RUNNERS = {
    "technicals": run_technicals,
    "financial_temporal": run_financial_temporal,
    "peer_compare": run_peer_compare,
    "business_moat": run_business_moat,
    "fcf_dividend": run_fcf_dividend,
    "risk_quant": run_risk_quant,
    "sentiment": run_sentiment,
    "valuation": run_margin_of_safety,
}
