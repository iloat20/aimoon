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
    return await peer_run(name=name, self_fin=self_fin, search_fn=search_fn)


def parse_self_check_json(text: str) -> tuple[dict | None, list[str]]:
    """Parse self-check JSON from LLM response text.

    Tries ``json`` code fence first, then falls back to finding any JSON
    object containing a ``passed`` key.  Returns ``(parsed_dict, fixes_list)``
    or ``(None, [])`` on failure.
    """
    # 1. Prefer ```json fence
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, dict):
                fixes = parsed.get("fixes_needed", [])
                return parsed, [str(f) for f in fixes if isinstance(f, (str, int, float))]
        except (json.JSONDecodeError, ValueError):
            pass
    # 2. Fallback: find any { ... } containing "passed"
    for match in re.finditer(r"\{[^{}]*\}", text):
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict) and "passed" in parsed:
                fixes = parsed.get("fixes_needed", [])
                return parsed, [str(f) for f in fixes if isinstance(f, (str, int, float))]
        except (json.JSONDecodeError, ValueError):
            continue
    # 3. Last resort: find outermost braces
    last_brace = text.rfind("}")
    if last_brace > 0:
        first_brace = text.rfind("{", 0, last_brace)
        if first_brace >= 0:
            try:
                parsed = json.loads(text[first_brace : last_brace + 1])
                if isinstance(parsed, dict):
                    fixes = parsed.get("fixes_needed", [])
                    return parsed, [str(f) for f in fixes if isinstance(f, (str, int, float))]
            except (json.JSONDecodeError, ValueError):
                pass
    return None, []
