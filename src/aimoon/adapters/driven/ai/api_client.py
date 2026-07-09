"""DeepSeek HTTP/SSE client + tool-calling loop.

Wraps the DeepSeek chat completions API. The ``_http`` client is injected
(httpx.AsyncClient) so the transport can be mocked in tests.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .post_processor import parse_xml_tool_calls, strip_xml_tool_calls
from .web_search_tool import execute_web_search, get_tool_definitions

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 0


class DeepSeekApiClient:
    """Thin transport wrapper around the DeepSeek chat completions API."""

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        settings: Any,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self._settings = settings
        self._http = http_client or httpx.AsyncClient(
            timeout=180.0,
            limits=httpx.Limits(max_keepalive_connections=5),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def call_with_tools(
        self, messages: list[dict]
    ) -> tuple[list[dict], bool] | None:
        """Send a non-streaming request; if model requests tool calls,
        execute them and append results to messages.

        Returns:
            (updated_messages, should_break) if tool calls were processed,
            None if model returned a final text response (no tool calls).
        """
        settings = self._settings
        resp = await self._http.post(
            self.api_url,
            headers=self._headers(),
            json={
                "model": settings.deepseek_model,
                "messages": messages,
                "temperature": settings.deepseek_temperature,
                "max_tokens": settings.deepseek_max_tokens,
                "tools": get_tool_definitions(),
                "tool_choice": "auto",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            content = message.get("content", "")
            xml_calls = parse_xml_tool_calls(content)
            if xml_calls:
                tool_calls = [
                    {"id": f"xml_{i}", "function": tc}
                    for i, tc in enumerate(xml_calls)
                ]
                message["tool_calls"] = tool_calls
                message["content"] = strip_xml_tool_calls(content)

        if not tool_calls:
            return None

        messages.append(message)

        for tc in tool_calls:
            fn = tc["function"]
            fn_name = fn["name"]
            try:
                fn_args = json.loads(fn["arguments"])
            except json.JSONDecodeError:
                fn_args = {}

            print(f"\n 🔍 联网搜索: {fn_args.get('query', fn_name)}")
            result = await execute_web_search(fn_args.get("query", ""))
            print(f"    → 获取到 {len(result)} 字符结果")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

        return messages, False

    async def stream_final_response(self, messages: list[dict]) -> str:
        """Send a streaming request and return the full accumulated text."""
        settings = self._settings
        async with self._http.stream(
            "POST",
            self.api_url,
            headers=self._headers(),
            json={
                "model": settings.deepseek_model,
                "messages": messages,
                "temperature": settings.deepseek_temperature,
                "max_tokens": settings.deepseek_max_tokens,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            from .._sse import collect_sse_content

            return await collect_sse_content(resp)
