"""LLM transport for the v2 pipeline (dedicated long-timeout client)."""

from __future__ import annotations

import logging

import httpx

from .._sse import collect_sse_content
from .timing import logphase
from .types import AnalyzerRuntime

logger = logging.getLogger(__name__)


class PipelineLlmClient:
    """Owns a dedicated long-timeout httpx client for LLM calls.

    Reuses TCP/TLS connections across ANALYSIS and COMPILE phases.
    Kept separate from the analyzer's short-timeout client so a slow COMPILE
    (480s) never starves the legacy path.
    """

    def __init__(
        self,
        analyzer: AnalyzerRuntime,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.analyzer = analyzer
        self._llm_http = http_client or httpx.AsyncClient(timeout=300.0)

    async def aclose(self) -> None:
        await self._llm_http.aclose()

    async def call_llm_with_stream(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str = "max",
    ) -> dict:
        """单次 LLM 调用 wrapper,带 DeepSeek 思考模式 + 300s timeout。

        使用 reasoning_effort 控制思考强度(max = 最深思考)。
        思考模式下 temperature/top_p 等参数无效,不传。
        """
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model,
            "messages": messages,
            "max_tokens": max_tokens or settings.deepseek_max_tokens,
            "reasoning_effort": reasoning_effort,
        }
        with logphase(f"llm(effort={reasoning_effort}, mt={body['max_tokens']})"):
            resp = await self._llm_http.post(
                analyzer.api_url,
                headers={
                    "Authorization": f"Bearer {analyzer.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if resp.status_code >= 400:
            logger.error("[pipeline] LLM HTTP %d: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

    async def stream_llm_content(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        reasoning_effort: str = "high",
    ) -> str:
        """流式 LLM 调用,实时打印 ``##`` 章节进度,返回拼接后的完整正文。

        与 ``call_llm_with_stream``(非流式、返回 message dict)不同,本方法面向
        长文生成场景(COMPILE),用 SSE 流持续输出进度并只回收 content 文本。
        """
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model,
            "messages": messages,
            "max_tokens": max_tokens or settings.deepseek_max_tokens,
            "reasoning_effort": reasoning_effort,
            "stream": True,
        }
        with logphase(f"llm-stream(effort={reasoning_effort})"):
            async with self._llm_http.stream(
                "POST",
                analyzer.api_url,
                headers={
                    "Authorization": f"Bearer {analyzer.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            ) as resp:
                resp.raise_for_status()
                return await collect_sse_content(resp)
