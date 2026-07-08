"""Browser factory abstraction — replaces module-level Playwright singleton.

Protocol-based: SocialMediaOrchestrator depends on this interface.
PlaywrightBrowserFactory manages a shared browser with async-lock double-check,
keeping the browser warm across collect() calls to avoid ~1s cold-start per run.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class BrowserFactory(Protocol):
    """浏览器工厂 — 替换模块级 Playwright 单例。"""

    async def acquire(self) -> Any:
        """获取浏览器实例（共享单例）。"""
        ...

    async def release(self, browser: Any) -> None:
        """释放浏览器（默认空操作，保持热启动）。"""
        ...

    async def shutdown(self) -> None:
        """关闭浏览器和 Playwright，在应用退出时调用。"""
        ...


class PlaywrightBrowserFactory:
    """生产实现 — 内部管理 Playwright 单例 + asyncio.Lock 双重检查。

    browser 在 acquire() 首次调用时懒创建，release() 空操作保持热启动，
    shutdown() 统一关闭。线程安全（asyncio.Lock 双重检查模式）。
    """

    def __init__(self) -> None:
        self._pw_instance: Any | None = None
        self._browser: Any | None = None
        self._lock: asyncio.Lock | None = None

    async def acquire(self) -> Any:
        if self._browser is not None:
            return self._browser
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            # Double-check after acquiring the lock.
            if self._browser is not None:
                return self._browser
            from playwright.async_api import async_playwright

            self._pw_instance = await async_playwright().start()
            self._browser = await self._pw_instance.chromium.launch(headless=True)
            logger.info("[browser_factory] Playwright browser started (shared)")
            return self._browser

    async def release(self, browser: Any) -> None:
        """空操作 — 保持热启动，浏览器在 shutdown() 时统一关闭。"""

    async def shutdown(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as e:
                logger.debug("[browser_factory] browser close error: %s", e)
            self._browser = None
        if self._pw_instance is not None:
            try:
                await self._pw_instance.stop()
            except Exception as e:
                logger.debug("[browser_factory] playwright stop error: %s", e)
            self._pw_instance = None
        self._lock = None
