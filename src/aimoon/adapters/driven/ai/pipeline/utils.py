"""Low-level helpers for the v2 pipeline orchestrator."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from aimoon.core.domain.entities.financial import FinancialData

logger = logging.getLogger(__name__)


def partial(reason: str) -> dict[str, object]:
    return {"output": "", "tool_results": {}, "partial": True, "reason": reason}


def is_partial(tool_value: object) -> bool:
    return isinstance(tool_value, dict) and "__partial__" in tool_value


async def run_safe(fn: Callable[..., Any], *args: Any) -> dict[str, object]:
    try:
        result = fn(*args)
        return result if isinstance(result, dict) else {"__partial__": "bad_return"}
    except Exception as e:  # broad tolerance: never abort the pipeline
        logger.warning(
            "[pipeline] 工具 %s 异常 %s: %s",
            getattr(fn, "__name__", fn),
            type(e).__name__,
            e,
        )
        return {"__partial__": f"{type(e).__name__}"}


async def run_peer_compare(si: object, search_fn: Callable[..., Any]) -> dict:
    """委托 ``peer_compare.run`` 单一入口,保持 ``{peers, industry}`` 返回形状。

    ``search_fn`` 直接透传给工具,避免 orchestrator 层进入
    ``build_search_query``/``parse`` 的实现细节;
    所有未预期错误由工具内部 try/except 兜底为 ``{"__partial__": "no_data"}``。
    """
    from ..tools.peer_compare import run as peer_run

    name = str(getattr(si, "name", "") or getattr(si, "symbol", "") or "")
    self_fin = getattr(si, "financial", None)
    if not isinstance(self_fin, FinancialData):
        return {"__partial__": "no_data", "peers": [], "industry": ""}
    # 行情 PE 作为同行数据异常自检基准(标的 PE 与同行串号时触发告警)。
    quote = getattr(si, "quote", None)
    self_pe = float(getattr(quote, "pe", 0.0) or 0.0) or None
    return await peer_run(
        name=name, self_fin=self_fin, search_fn=search_fn, self_pe=self_pe
    )


def parse_skeleton_json(text: str) -> dict | None:
    """Extract a JSON object from LLM output text.

    Tries (in order):
    1. ```json code fence
    2. First { ... } block (greedy outermost)
    3. json.loads on the whole text

    Returns parsed dict or None on failure.
    """
    if not text or not text.strip():
        return None
    # 1. Prefer ```json fence
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    # 2. Outermost braces
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        try:
            parsed = json.loads(text[first : last + 1])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    # 3. Whole text
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None
