"""Shared Playwright browser lifecycle management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

# Lazy import — playwright is heavy
_playwright = None


def _get_playwright():
    global _playwright
    if _playwright is None:
        from playwright.async_api import async_playwright
        _playwright = async_playwright
    return _playwright


@asynccontextmanager
async def browser_context(
    browser=None, *, headless: bool = True, locale: str = "zh-CN"
) -> AsyncIterator:
    """Context manager for a Playwright browser page.

    Creates a browser if none provided. Automatically cleans up.
    Yields (page, browser, playwright).

    Usage:
        async with browser_context() as (page, browser, pw):
            await page.goto("https://...")
    """
    pw = None
    context = None
    owns_browser = browser is None
    try:
        if owns_browser:
            async_playwright = _get_playwright()
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(locale=locale)
        page = await context.new_page()
        yield page
    finally:
        if context:
            await context.close()
        if owns_browser and browser is not None:
            await browser.close()
        if owns_browser and pw:
            await pw.stop()
