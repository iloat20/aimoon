"""Social media orchestrator — coordinates multi-platform social data collection."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from ..models.social import CollectResult
from .base import CollectorRegistry
from .eastmoney_playwright import GubaCollector
from .mock import mock_social_posts


class SocialMediaOrchestrator:
    """Manages social media collectors and their execution lifecycle."""

    async def collect(
        self, symbol: str, name: str
    ) -> tuple[list[Any], list[CollectResult]]:
        """Collect social media sentiment from multiple platforms.

        Returns (all_posts, collect_results).
        """
        print(" 采集社交媒体舆情...")

        from playwright.async_api import async_playwright

        registry = CollectorRegistry()

        guba_collector = GubaCollector()
        registry.register(guba_collector)

        from .cninfo import CninfoCollector

        registry.register(CninfoCollector())

        failed_platforms: list[str] = []
        playwright_collectors: list[Any] = [guba_collector]

        for p_name, module_path, cls_name in [
            ("今日头条", ".collectors.toutiao", "ToutiaoCollector"),
            ("微信公众号", ".collectors.wechat", "WechatCollector"),
        ]:
            try:
                mod = importlib.import_module(module_path, "aimoon")
                cls = getattr(mod, cls_name)
                collector = cls()
                registry.register(collector)
                playwright_collectors.append(collector)
            except Exception as e:
                logging.warning(
                    "[social_orchestrator_import_%s] %s: %s",
                    p_name,
                    type(e).__name__,
                    e,
                )
                failed_platforms.append(p_name)

        pw = None
        browser = None
        try:
            try:
                pw = await async_playwright().start()
                browser = await pw.chromium.launch(headless=True)
                for c in playwright_collectors:
                    if hasattr(c, "set_browser"):
                        c.set_browser(browser)
            except Exception as e:
                logging.warning(
                    "[social_orchestrator_browser_init] %s: %s",
                    type(e).__name__,
                    e,
                )

            raw_results = await registry.collect_all(symbol, name)
        finally:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()

        result_map = {r.platform: r for r in raw_results}

        all_posts: list[Any] = []
        collect_results: list[CollectResult] = []

        platform_order = ["东方财富股吧", "巨潮资讯", "今日头条", "微信公众号"]

        for p_name in platform_order:
            if p_name == "东方财富股吧":
                result = result_map.get(p_name)
                if result and result.status == "success" and result.count > 0:
                    all_posts.extend(result.posts)
                    source_tag = result.error or "股吧"
                    print(f"   东方财富股吧: {result.count}条 [{source_tag}]")
                    collect_results.append(result)
                else:
                    mock = mock_social_posts("东方财富股吧", symbol, name)
                    all_posts.extend(mock)
                    print(f"   东方财富股吧: {len(mock)}条 (mock)")
                    collect_results.append(
                        CollectResult(
                            platform="东方财富股吧",
                            status="success (mock)",
                            count=len(mock),
                            elapsed_ms=100,
                        )
                    )
            elif p_name == "巨潮资讯":
                result = result_map.get(p_name)
                if result and result.status == "success" and result.count > 0:
                    all_posts.extend(result.posts)
                    print(f"   巨潮资讯: {result.count}条 [真实数据]")
                    collect_results.append(result)
                else:
                    error_msg = result.error if result else "未知错误"
                    mock = mock_social_posts("巨潮资讯", symbol, name)
                    all_posts.extend(mock)
                    print(f"   巨潮资讯: {len(mock)}条 (mock) [{error_msg}]")
                    collect_results.append(
                        CollectResult(
                            platform="巨潮资讯",
                            status="success (mock)",
                            count=len(mock),
                            elapsed_ms=100,
                        )
                    )
            elif p_name in ("今日头条", "微信公众号"):
                if p_name in failed_platforms:
                    mock = mock_social_posts(p_name, symbol, name)
                    all_posts.extend(mock)
                    collect_results.append(
                        CollectResult(
                            platform=p_name,
                            status="success (mock)",
                            count=len(mock),
                            elapsed_ms=100,
                        )
                    )
                    print(f"   {p_name}: {len(mock)}条 (mock)")
                else:
                    result = result_map.get(p_name)
                    if result and result.status == "success" and result.count > 0:
                        all_posts.extend(result.posts)
                        print(f"   {p_name}: {result.count}条 [真实数据]")
                        collect_results.append(result)
                    elif result and result.status == "skipped":
                        skip_reason = result.error or "skipped"
                        mock = mock_social_posts(p_name, symbol, name)
                        all_posts.extend(mock)
                        collect_results.append(
                            CollectResult(
                                platform=p_name,
                                status="success (mock)",
                                count=len(mock),
                                elapsed_ms=100,
                            )
                        )
                        print(f"   {p_name}: {len(mock)}条 (mock) [{skip_reason}]")
                    else:
                        mock = mock_social_posts(p_name, symbol, name)
                        all_posts.extend(mock)
                        collect_results.append(
                            CollectResult(
                                platform=p_name,
                                status="success (mock)",
                                count=len(mock),
                                elapsed_ms=100,
                            )
                        )
                        print(f"   {p_name}: {len(mock)}条 (mock)")

        return all_posts, collect_results
