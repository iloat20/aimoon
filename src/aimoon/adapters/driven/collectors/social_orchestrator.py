"""Social media orchestrator — coordinates multi-platform social data collection."""

from __future__ import annotations

import importlib
import logging

import httpx

from aimoon.core.application.browser_factory import BrowserFactory, PlaywrightBrowserFactory
from aimoon.core.application.progress import CliProgressReporter, ProgressReporter
from aimoon.core.domain.entities.social import SocialPost
from aimoon.core.domain.value_objects.collect_result import CollectResult

from .base import BaseCollector, CollectorRegistry
from .cninfo import CninfoCollector
from .eastmoney_playwright import GubaCollector
from .mock import mock_social_posts

logger = logging.getLogger(__name__)

# Module-level default browser factory for backward compatibility.
# PipelineOrchestrator calls close_shared_browser() without holding a reference
# to the SocialMediaOrchestrator instance, so we keep a process-wide default.
_default_browser_factory: BrowserFactory = PlaywrightBrowserFactory()


async def _get_shared_browser() -> tuple[object, object]:
    """Return a shared (playwright, browser) pair, creating them lazily.

    Delegates to the module-level default factory. Kept for backward
    compatibility — callers only use the browser, pw_instance is returned
    but unused.
    """
    browser = await _default_browser_factory.acquire()
    pw = getattr(_default_browser_factory, "_pw_instance", None)
    return pw, browser


async def close_shared_browser() -> None:
    """Close the shared Playwright browser (backward compat).

    Delegates to the default factory's shutdown().
    """
    await _default_browser_factory.shutdown()


class SocialMediaOrchestrator:
    """Manages social media collectors and their execution lifecycle.

    Args:
        browser_factory: Browser lifecycle manager. Defaults to the module-level
            shared factory (kept warm across calls). Tests can inject a fake.
        reporter: Progress output. Defaults to CliProgressReporter (print).
            Tests can inject NullProgressReporter or RecordingProgressReporter.
    """

    def __init__(
        self,
        browser_factory: BrowserFactory | None = None,
        reporter: ProgressReporter | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._browser_factory = browser_factory or _default_browser_factory
        self._reporter = reporter or CliProgressReporter()
        self._http = http_client

    async def collect(self, symbol: str, name: str) -> tuple[list[SocialPost], list[CollectResult]]:
        """Collect social media sentiment from multiple platforms.

        Returns (all_posts, collect_results).
        """
        self._reporter.report(" 采集社交媒体舆情...")

        registry = CollectorRegistry()
        playwright_collectors: list[BaseCollector] = []
        failed_platforms: set[str] = set()

        guba_collector = GubaCollector(http_client=self._http)
        cninfo_collector = CninfoCollector(http_client=self._http)
        registry.register(guba_collector)
        registry.register(cninfo_collector)
        playwright_collectors.append(guba_collector)

        for p_name, module_path, cls_name in [
            ("微信公众号", ".wechat", "WechatCollector"),
        ]:
            try:
                mod = importlib.import_module(module_path, "aimoon.adapters.driven.collectors")
                cls = getattr(mod, cls_name)
                collector = cls()
                registry.register(collector)
                playwright_collectors.append(collector)
            except (ImportError, AttributeError) as e:
                logging.warning(
                    "[social_orchestrator_import_%s] %s: %s",
                    p_name,
                    type(e).__name__,
                    e,
                )
                failed_platforms.add(p_name)

        try:
            if playwright_collectors:
                try:
                    browser = await self._browser_factory.acquire()
                    for c in playwright_collectors:
                        if hasattr(c, "set_browser") and callable(getattr(c, "set_browser", None)):
                            c.set_browser(browser)
                except Exception as e:
                    logging.warning(
                        "[social_orchestrator_browser_init] %s: %s",
                        type(e).__name__,
                        e,
                    )

            raw_results = await registry.collect_all(symbol, name)
        finally:
            # Don't close the shared browser — keep it warm for next calls.
            pass

        result_map = {r.platform: r for r in raw_results}

        all_posts: list[SocialPost] = []
        collect_results: list[CollectResult] = []

        platform_order = ["东方财富股吧", "巨潮资讯", "微信公众号"]

        for p_name in platform_order:
            result = result_map.get(p_name)
            is_failed = p_name in failed_platforms

            if not is_failed and result and result.status == "success" and result.count > 0:
                all_posts.extend(result.posts)
                collect_results.append(result)
                if p_name == "东方财富股吧":
                    source_tag = result.error or "股吧"
                    self._reporter.report(f"   东方财富股吧: {result.count}条 [{source_tag}]")
                elif p_name == "巨潮资讯":
                    self._reporter.report(f"   巨潮资讯: {result.count}条 [真实数据]")
                else:
                    self._reporter.report(f"   {p_name}: {result.count}条 [真实数据]")
            else:
                mock = mock_social_posts(p_name, symbol, name)
                # 模拟数据不发给 AI，仅用于展示
                collect_results.append(
                    CollectResult(
                        platform=p_name,
                        status="failed",
                        count=len(mock),
                        elapsed_ms=100,
                        error=(result.error if result else "采集失败") or "采集失败",
                    )
                )
                if p_name == "巨潮资讯":
                    error_msg = result.error if result else "未知错误"
                    self._reporter.report(f"   巨潮资讯: {len(mock)}条 (mock) [{error_msg}]")
                elif p_name in ("微信公众号",) and result and result.status == "failed":
                    error_msg = result.error or "未知错误"
                    self._reporter.report(
                        f"   {p_name}: {len(mock)}条 (mock) [采集失败: {error_msg}]"
                    )
                elif p_name in ("微信公众号",) and is_failed:
                    self._reporter.report(f"   {p_name}: {len(mock)}条 (mock) [模块导入失败]")
                else:
                    self._reporter.report(f"   {p_name}: {len(mock)}条 (mock)")

        return all_posts, collect_results

    async def shutdown(self) -> None:
        """Clean up shared Playwright resources.

        Call this when the application exits.
        """
        await self._browser_factory.shutdown()
