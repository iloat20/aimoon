"""Markdown prompt loader for the AI pipeline.

Prompts live alongside this module as ``*.md`` files (e.g. ``analysis.md``,
``direct.md``). :func:`load_prompt` reads one by filename and returns its
text, returning an empty string when the file is missing so callers can fail
loudly on their own terms.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Load a markdown prompt file by name (relative to this package).

    Args:
        name: Filename including ``.md`` extension, e.g. ``"domain_knowledge.md"``.

    Returns:
        The file's UTF-8 text, or ``""`` if the file does not exist.
    """
    p = PROMPTS_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else ""
