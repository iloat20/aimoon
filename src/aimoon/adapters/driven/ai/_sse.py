"""Shared SSE stream reader for DeepSeek streaming responses.

Unifies the previously-duplicated readers in ``analyzer.py``
(``_collect_stream``) and ``pipeline/orchestrator.py`` (``_collect_content_stream``).
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


async def collect_sse_content(resp: object, verbose: bool = False) -> str:
    """Read SSE stream, optionally print ``##`` section headers as they arrive, return full text.

    Only the ``delta.content`` chunks are collected (reasoning middleware is
    ignored). Uses ``splitlines()`` for O(n) buffer processing.

    When ``verbose`` is False (default), no report content is printed to the
    terminal — the full text is still accumulated and returned for the HTML
    report. Set ``verbose=True`` to restore the streaming progress display.
    """
    full_text: list[str] = []
    buffer = ""
    state = {"section": ""}

    def _emit(line_text: str) -> None:
        """Print one complete/partial line, handling ``##`` section banners.

        No-op unless ``verbose`` is True (report is still accumulated/returned).
        """
        if not verbose:
            return
        header_match = re.match(r"^##\s+(.+)", line_text)
        if header_match:
            if state["section"]:
                print()
            section_name = header_match.group(1).strip()
            state["section"] = section_name
            print(f"\n{'─' * 40}")
            print(f"  {section_name}")
            print(f"{'─' * 40}")
        elif state["section"] and line_text.strip():
            stripped = line_text.rstrip()
            if len(stripped) > 120:
                stripped = stripped[:117] + "..."
            print(f"  {stripped}")

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

        # 保留换行符切分: 每遇到完整 \n 即 flush 用于流式打印, 行尾残留留在
        # buffer 直到下个 delta 补全。splitlines() 会丢弃 \n 导致跨 delta 行被合并
        # (第二个 "##" 头被裹进正文不再识别为 banner)。
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            _emit(line + "\n")

    # Flush the final partial line for streaming display.
    # NOTE: do NOT append ``buffer`` to ``full_text`` here — it is already included
    # via the per-delta ``full_text.append(content)`` above; re-appending would
    # duplicate the report's last sentence.
    if buffer.strip():
        _emit(buffer)

    return "".join(full_text)
