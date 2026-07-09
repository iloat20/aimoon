"""Strip DeepSeek DSML XML tool-call markup from model output text.

Both ``analyzer.py`` and the v2 ``orchestrator.py`` used to carry identical
private copies of this logic; this module is the single source of truth.
"""

from __future__ import annotations

import re

_DSML_TOOL_CALLS = re.compile(
    r"<｜｜DSML｜｜tool_calls>.*?</｜｜DSML｜｜tool_calls>", re.DOTALL
)
_DSML_INVOKE = re.compile(r"<｜｜DSML｜｜invoke.*?</｜｜DSML｜｜invoke>", re.DOTALL)


def strip_xml_tool_calls(text: str) -> str:
    """Remove XML-style tool-call markup from response text."""
    text = _DSML_TOOL_CALLS.sub("", text)
    text = _DSML_INVOKE.sub("", text)
    return text.strip()
