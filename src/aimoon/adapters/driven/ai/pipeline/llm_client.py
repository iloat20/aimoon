"""LLM transport for the v2 pipeline (dedicated long-timeout client)."""

from __future__ import annotations

import logging

import httpx

from .._sse import collect_sse_content
from .timing import logphase
from .types import AnalyzerRuntime

logger = logging.getLogger(__name__)

# Dedicated long-timeout client for LLM calls (analyzer path uses short-timeout).
LLM_CLIENT_TIMEOUT = 500.0  # > COMPILE_TIMEOUT(480), 让 orchestrator 的 asyncio.wait_for 先触发

# reasoning_effort 仅 DeepSeek 思考(reasoner)模型支持; 普通 chat 模型传此参数
# 会被 API 拒绝。据此守卫,既保证 reasoner 质量,又允许用户用 DEEPSEEK_MODEL=deepseek-chat
# (更便宜、更快) 作为成本档位。
#
# 前缀缓存: DeepSeek 对已发送过的「相同前缀」自动缓存(命中后处理被跳过、输入按折扣计费)。
# 本流水线每条消息的 system 段(analysis.md / compile.md / self_check.md 固定文本)位于
# 最前,天然成为稳定缓存前缀;同一标的复跑时 stock_md + tables_md 前缀也一致 → 自动命中。
# 因此无需任何额外参数,重复分析同一股票即可显著省 token。
_REASONER_HINTS = ("reasoner",)


def _is_reasoner_model(model: str | None) -> bool:
    name = (model or "").lower()
    return any(hint in name for hint in _REASONER_HINTS)


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
        self._llm_http = http_client or httpx.AsyncClient(timeout=LLM_CLIENT_TIMEOUT)

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
        }
        if _is_reasoner_model(settings.deepseek_model):
            body["reasoning_effort"] = reasoning_effort
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
            "stream": True,
        }
        if _is_reasoner_model(settings.deepseek_model):
            body["reasoning_effort"] = reasoning_effort
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
