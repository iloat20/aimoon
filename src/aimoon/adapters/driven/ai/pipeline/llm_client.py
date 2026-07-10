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

# 思考模式官方参数: thinking:{type:enabled/disabled},默认 enabled。
# reasoning_effort 仅思考模式生效(有效档 high/max;low/medium→high,xhigh→max)。
# 思考模式下 temperature/top_p/presence_penalty/frequency_penalty 被忽略(不报错)。
# 关闭思考(thinking:disabled)时,这些采样参数恢复生效,故改回传 temperature。
#
# 前缀缓存(最大免费杠杆): DeepSeek 对「相同前缀」自动缓存,命中后输入按折扣计费。
#   缓存命中输入 ¥0.02/百万  vs  未命中 ¥1.0/百万  ——  相差 50 倍!
# 本流水线每条消息的 system 段(analysis.md / compile.md / direct.md 固定长文本)位于
# 最前,天然成为稳定缓存前缀;同一标的复跑时 stock_md + tables_md 前缀也一致 → 自动命中
# 同一系统前缀,省下大头输入 token。故:保持 system 提示稳定、勿在系统段注入易变内容,
# 即可零成本复用缓存。思考 token(reasoning_content)按「输出」计价(¥2/百万 flash),
# 是主要成本来源 —— 想省钱优先降 effort(high→max 一档)或直接关思考(扩写阶段)。
def _resolve_thinking(
    settings: object,
    thinking_override: bool | None,
    reasoning_effort: str,
) -> dict[str, object]:
    """Compute the thinking-related request-body fields.

    Returns a dict with keys:
      - ``thinking``: always present (``{"type": "enabled" | "disabled"}``).
      - ``reasoning_effort``: present only when thinking is enabled.
      - ``temperature``: present only when thinking is disabled (thinking mode
        ignores it; sampling params only matter for non-thinking expansion).

    Resolution order: an explicit per-call ``thinking_override`` wins; otherwise
    fall back to ``deepseek_thinking_enabled``, then the legacy
    ``deepseek_reasoner_enabled`` alias; absent all, v4-* models default to enabled.
    """
    explicit = getattr(settings, "deepseek_thinking_enabled", None)
    if explicit is None:
        explicit = getattr(settings, "deepseek_reasoner_enabled", None)
    if thinking_override is not None:
        enabled = thinking_override
    else:
        # deepseek-v4-flash / v4-pro 官方默认思考开启
        enabled = True if explicit is None else bool(explicit)

    if enabled:
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": reasoning_effort,
            "temperature": None,
        }
    return {
        "thinking": {"type": "disabled"},
        "reasoning_effort": None,
        "temperature": getattr(settings, "deepseek_temperature", 0.3),
    }


def _apply_thinking(
    body: dict[str, object],
    settings: object,
    thinking_override: bool | None,
    reasoning_effort: str,
) -> None:
    """Mutate ``body`` in place with the resolved thinking fields."""
    resolved = _resolve_thinking(settings, thinking_override, reasoning_effort)
    body["thinking"] = resolved["thinking"]
    if resolved["reasoning_effort"] is not None:
        body["reasoning_effort"] = resolved["reasoning_effort"]
    if resolved["temperature"] is not None:
        body["temperature"] = resolved["temperature"]


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
        thinking: bool | None = None,
    ) -> dict:
        """单次 LLM 调用 wrapper,带 DeepSeek 思考模式 + 300s timeout。

        通过 thinking 开关(官方默认 enabled)+ reasoning_effort 控制思考强度。
        思考模式下 temperature 被忽略(本方法在关闭思考时回传 temperature)。
        """
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model,
            "messages": messages,
            "max_tokens": max_tokens or settings.deepseek_max_tokens,
        }
        _apply_thinking(body, settings, thinking, reasoning_effort)
        eff = body.get("reasoning_effort")
        with logphase(f"llm(thinking={body['thinking']}, effort={eff}, mt={body['max_tokens']})"):
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
        thinking: bool | None = None,
    ) -> str:
        """流式 LLM 调用,实时打印 ``##`` 章节进度,返回拼接后的完整正文。

        与 ``call_llm_with_stream``(非流式、返回 message dict)不同,本方法面向
        长文生成场景(COMPILE / DIRECT),用 SSE 流持续输出进度并只回收 content 文本。
        """
        analyzer = self.analyzer
        settings = analyzer._provided_settings or analyzer._settings
        body: dict[str, object] = {
            "model": settings.deepseek_model,
            "messages": messages,
            "max_tokens": max_tokens or settings.deepseek_max_tokens,
            "stream": True,
        }
        _apply_thinking(body, settings, thinking, reasoning_effort)
        eff = body.get("reasoning_effort")
        with logphase(f"llm-stream(thinking={body['thinking']}, effort={eff})"):
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
