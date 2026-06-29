"""Social media orchestrator — coordinates multi-platform social data collection."""

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Any

from aimoon.core.domain.entities.social import SocialPost
from aimoon.core.domain.value_objects.collect_result import CollectResult

from .base import BaseCollector, CollectorRegistry
from .cninfo import CninfoCollector
from .eastmoney_playwright import GubaCollector
from .mock import mock_social_posts

# Module-level Playwright singleton for cold-start optimization.
# Reusing a single browser instance across collect() calls avoids the
# ~1s Playwright + Chromium launch cost on each invocation.
_pw_instance: Any | None = None
_pw_browser: Any | None = None
_pw_lock: asyncio.Lock | None = None


async def _get_shared_browser() -> tuple[object, object]:
    """Return a shared (playwright, browser) pair, creating them lazily.

    Uses a module-level singleton with async lock for thread safety.
    The browser stays open between collect() calls to avoid repeated
    Playwright cold-start overhead (~1s per launch).
    """
    global _pw_instance, _pw_browser, _pw_lock

    if _pw_browser is not None:
        return _pw_instance, _pw_browser

    if _pw_lock is None:
        _pw_lock = asyncio.Lock()

    async with _pw_lock:
        # Double-check after acquiring the lock.
        if _pw_browser is not None:
            return _pw_instance, _pw_browser

        from playwright.async_api import async_playwright

        _pw_instance = await async_playwright().start()
        _pw_browser = await _pw_instance.chromium.launch(headless=True)
        logging.info("[social_orchestrator] Playwright browser started (shared)")
        return _pw_instance, _pw_browser


async def close_shared_browser() -> None:
    """Close the shared Playwright browser.

    Call this during application shutdown to clean up resources.
    """
    global _pw_instance, _pw_browser, _pw_lock

    if _pw_browser is not None:
        try:
            await _pw_browser.close()
        except Exception as e:
            logging.debug("[social_orchestrator] browser close error: %s", e)
        _pw_browser = None
    if _pw_instance is not None:
        try:
            await _pw_instance.stop()
        except Exception as e:
            logging.debug("[social_orchestrator] playwright stop error: %s", e)
        _pw_instance = None
    _pw_lock = None


class SocialMediaOrchestrator:
    """Manages social media collectors and their execution lifecycle."""

    async def collect(self, symbol: str, name: str) -> tuple[list[SocialPost], list[CollectResult]]:
        """Collect social media sentiment from multiple platforms.

        Returns (all_posts, collect_results).
        """
        print(" 采集社交媒体舆情...")

        registry = CollectorRegistry()
        playwright_collectors: list[BaseCollector] = []
        failed_platforms: set[str] = set()

        guba_collector = GubaCollector()
        cninfo_collector = CninfoCollector()
        registry.register(guba_collector)
        registry.register(cninfo_collector)
        playwright_collectors.append(guba_collector)

        for p_name, module_path, cls_name in [
            ("今日头条", ".toutiao", "ToutiaoCollector"),
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
                    _pw, browser = await _get_shared_browser()
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

        platform_order = ["东方财富股吧", "巨潮资讯", "今日头条", "微信公众号"]

        for p_name in platform_order:
            result = result_map.get(p_name)
            is_failed = p_name in failed_platforms

            if not is_failed and result and result.status == "success" and result.count > 0:
                all_posts.extend(result.posts)
                collect_results.append(result)
                if p_name == "东方财富股吧":
                    source_tag = result.error or "股吧"
                    print(f"   东方财富股吧: {result.count}条 [{source_tag}]")
                elif p_name == "巨潮资讯":
                    print(f"   巨潮资讯: {result.count}条 [真实数据]")
                else:
                    print(f"   {p_name}: {result.count}条 [真实数据]")
            else:
                mock = mock_social_posts(p_name, symbol, name)
                # 模拟数据不发给 AI，仅用于展示
                collect_results.append(
                    CollectResult(
                        platform=p_name,
                        status="success (mock)",
                        count=len(mock),
                        elapsed_ms=100,
                    )
                )
                if p_name == "巨潮资讯":
                    error_msg = result.error if result else "未知错误"
                    print(f"   巨潮资讯: {len(mock)}条 (mock) [{error_msg}]")
                elif p_name in ("今日头条", "微信公众号") and result and result.status == "failed":
                    error_msg = result.error or "未知错误"
                    print(f"   {p_name}: {len(mock)}条 (mock) [采集失败: {error_msg}]")
                elif p_name in ("今日头条", "微信公众号") and is_failed:
                    print(f"   {p_name}: {len(mock)}条 (mock) [模块导入失败]")
                else:
                    print(f"   {p_name}: {len(mock)}条 (mock)")

        return all_posts, collect_results

    async def shutdown(self) -> None:
        """Clean up shared Playwright resources.

        Call this when the application exits.
        """
        await close_shared_browser()
