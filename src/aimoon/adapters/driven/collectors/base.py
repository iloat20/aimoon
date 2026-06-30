"""Base collector and registry pattern for multi-platform data collection."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod

import httpx

from aimoon.core.domain.entities.social import SocialPost
from aimoon.core.domain.value_objects.collect_result import CollectResult

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """Abstract base for social media platform collectors."""

    name: str = "base"

    @abstractmethod
    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        """Collect social data for a given stock symbol."""

    def _ok(self, posts: list[SocialPost], elapsed: float) -> CollectResult:
        return CollectResult(
            platform=self.name,
            status="success",
            posts=posts,
            count=len(posts),
            elapsed_ms=elapsed,
        )

    def _fail(self, error: str, elapsed: float) -> CollectResult:
        return CollectResult(platform=self.name, status="failed", error=error, elapsed_ms=elapsed)

    def _timeout_msg(self, elapsed: float) -> CollectResult:
        return CollectResult(
            platform=self.name, status="timeout", error="采集超时", elapsed_ms=elapsed
        )


class DataCollector[T](ABC):
    """Abstract base for non-social data collectors (quote, K-line, fund flow, etc.).

    Returns typed data models directly rather than CollectResult.
    """

    name: str = "base"

    @abstractmethod
    async def fetch(self, symbol: str, **kwargs: object) -> T:
        """Fetch data for a stock symbol; return typed model."""


class CollectorRegistry:
    """Manages all collectors and runs them concurrently."""

    def __init__(self):
        self._collectors: dict[str, BaseCollector] = {}

    def register(self, collector: BaseCollector) -> None:
        self._collectors[collector.name] = collector

    def get(self, name: str) -> BaseCollector | None:
        return self._collectors.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._collectors.keys())

    async def collect_all(
        self, symbol: str, stock_name: str = "", timeout: float = 60.0
    ) -> list[CollectResult]:
        """Run all collectors concurrently with per-collector timeout."""
        tasks = []
        for c in self._collectors.values():
            task = asyncio.ensure_future(self._collect_one(c, symbol, stock_name))
            tasks.append(task)

        raw_results = await asyncio.gather(
            *[asyncio.wait_for(t, timeout=timeout) for t in tasks],
            return_exceptions=True,
        )

        processed: list[CollectResult] = []
        for col, res in zip(self._collectors.values(), raw_results):
            if isinstance(res, Exception):
                processed.append(CollectResult(platform=col.name, status="failed", error=str(res)))
            elif isinstance(res, CollectResult):
                processed.append(res)

        return processed

    async def _collect_one(
        self, collector: BaseCollector, symbol: str, stock_name: str
    ) -> CollectResult:
        t0 = time.monotonic()
        try:
            result = await collector.collect(symbol, stock_name)
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.debug("[%s] completed in %dms", collector.name, elapsed)
            return result
        except TimeoutError:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.warning("[%s] timeout in %dms", collector.name, elapsed)
            return collector._timeout_msg(elapsed)
        except (ConnectionError, OSError, httpx.HTTPError) as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.warning("[%s] failed in %dms: %s", collector.name, elapsed, e)
            return collector._fail(str(e), elapsed)
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.warning("[%s] failed in %dms: %s", collector.name, elapsed, e)
            return collector._fail(str(e), elapsed)
