"""Shared SSE stream reader for DeepSeek streaming responses.

Unifies the previously-duplicated readers in ``analyzer.py``
(``_collect_stream``) and ``pipeline/orchestrator.py`` (``_collect_content_stream``).
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


async def collect_sse_content(resp: object) -> str:
    """Read SSE stream, print ``##`` section headers as they arrive, return full text.

    Only the ``delta.content`` chunks are collected (reasoning middleware is
    ignored). Uses ``splitlines()`` for O(n) buffer processing.
    """
    full_text: list[str] = []
    buffer = ""
    current_section = ""

    async for line in resp.aiter_lines():  # type: ignore[attr-defined]
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        content = delta.get("content", "")
        if not content:
            continue

        full_text.append(content)
        buffer += content

        # O(n) splitlines replaces O(n^2) ``while "\n" in buffer`` loop
        *lines, buffer = buffer.splitlines()
        for line_text in lines:
            line_text = line_text + "\n"
            header_match = re.match(r"^##\s+(.+)", line_text)
            if header_match:
                if current_section:
                    print()
                section_name = header_match.group(1).strip()
                current_section = section_name
                print(f"\n{'─' * 40}")
                print(f"  {section_name}")
                print(f"{'─' * 40}")
            elif current_section and line_text.strip():
                stripped = line_text.rstrip()
                if len(stripped) > 120:
                    stripped = stripped[:117] + "..."
                print(f"  {stripped}")

    if buffer.strip():
        full_text.append(buffer)

    return "".join(full_text)
