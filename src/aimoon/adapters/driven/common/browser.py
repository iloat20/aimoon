"""Shared Playwright browser session management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from playwright.async_api import Browser, Playwright


@asynccontextmanager
async def browser_session(
    shared_browser: Browser | None = None,
    context_options: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[Browser, Any]]:
    """Manage a Playwright browser session.

    If shared_browser is provided, uses it without closing.
    Otherwise, launches a new browser and closes it on exit.

    Args:
        shared_browser: Optional shared browser instance to reuse.
        context_options: Extra keyword arguments for ``browser.new_context()``,
            merged on top of the default ``locale="zh-CN"``.

    Yields:
        (browser, context) tuple.
    """
    from playwright.async_api import async_playwright

    owns_browser = shared_browser is None
    pw: Playwright | None = None
    browser = shared_browser
    try:
        if owns_browser:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
        assert browser is not None  # noqa: S101
        ctx_kwargs: dict[str, Any] = {"locale": "zh-CN"}
        if context_options:
            ctx_kwargs.update(context_options)
        context = await browser.new_context(**ctx_kwargs)
        try:
            yield browser, context
        finally:
            await context.close()
    finally:
        if owns_browser:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()
