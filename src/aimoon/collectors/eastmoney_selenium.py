"""East Money Guba (东方财富股吧) — Selenium-based collector.

More robust than akshare/HTTP for scraping individual stock posts.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from ..models.social import CollectResult, SocialPost
from .base import BaseCollector


class SeleniumGubaCollector(BaseCollector):
    """Collects stock posts from 东方财富股吧 using Selenium.

    Uses headless Chrome to render the guba page and extract post data
    from the DOM, avoiding API-based WAF/cookie issues.
    """

    name = "东方财富股吧"

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._driver: Optional["webdriver.Chrome"] = None  # noqa: F821

    def _ensure_driver(self):
        """Lazy init Chrome WebDriver."""
        if self._driver is not None:
            return self._driver
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        if self._headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        opts.add_argument("--lang=zh-CN")

        self._driver = webdriver.Chrome(options=opts)
        self._driver.implicitly_wait(10)
        return self._driver

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()

        # Check Chrome availability early
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            import selenium.common.exceptions as exc
        except ImportError:
            return self._fail("selenium 未安装: pip install selenium", (time.monotonic() - t0) * 1000)

        try:
            driver = self._ensure_driver()
        except Exception as e:
            msg = str(e)
            if "chromedriver" in msg.lower() or "executable" in msg.lower():
                msg = "ChromeDriver 未找到。请安装 Chrome 浏览器和对应版本的 ChromeDriver:\n  https://googlechromelabs.github.io/chrome-for-testing/"
            return self._fail(f"Selenium不可用: {msg}", (time.monotonic() - t0) * 1000)
        market = "1" if symbol.startswith("6") else "0"
        url = f"https://guba.eastmoney.com/list,{symbol},f_{market}.html"

        try:
            driver.get(url)
            time.sleep(3)  # Wait for JS rendering

            posts: list[SocialPost] = []
            from selenium.webdriver.common.by import By

            # Post list in .listitem rows
            article_rows = driver.find_elements(By.CSS_SELECTOR, ".listitem")
            if not article_rows:
                article_rows = driver.find_elements(By.CSS_SELECTOR, "[class*='article']")

            for row in article_rows[:10]:
                try:
                    # Title link
                    title_el = row.find_element(By.CSS_SELECTOR, "a[title], a[data-cntitle]")
                    title = title_el.get_attribute("title") or title_el.get_attribute("data-cntitle") or title_el.text or ""
                    href = title_el.get_attribute("href") or ""

                    # Author
                    author = ""
                    try:
                        author_el = row.find_element(By.CSS_SELECTOR, ".author a")
                        author = author_el.text or ""
                    except Exception:
                        pass

                    # Read / view count
                    reads = 0
                    try:
                        read_el = row.find_element(By.CSS_SELECTOR, ".read")
                        txt = read_el.text.strip()
                        if "万" in txt:
                            reads = int(float(txt.replace("万", "")) * 10000)
                        else:
                            reads = int(txt or "0")
                    except Exception:
                        pass

                    # Comment count
                    comments = 0
                    try:
                        comment_el = row.find_element(By.CSS_SELECTOR, ".reply")
                        txt = comment_el.text.strip()
                        comments = int(txt or "0")
                    except Exception:
                        pass

                    if title and len(title) >= 4:
                        posts.append(SocialPost(
                            platform="东方财富股吧",
                            title=title.strip()[:100],
                            content=title.strip(),
                            url=href,
                            author=author,
                            published_at=datetime.now().isoformat(),
                            likes=reads,
                            comments=comments,
                            shares=0,
                            views=reads,
                        ))
                except Exception:
                    continue

            elapsed = (time.monotonic() - t0) * 1000
            if posts:
                return self._ok(posts, elapsed)
            # Fallback: try screenshot/log for debugging
            return self._fail("页面解析为空", elapsed)

        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            return self._fail(f"Selenium失败: {e}", elapsed)

    async def close(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
