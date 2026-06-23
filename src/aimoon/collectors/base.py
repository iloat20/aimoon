"""Base collector and registry pattern for multi-platform data collection."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any

from ..models.social import CollectResult, SocialPost


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
        return CollectResult(
            platform=self.name, status="failed", error=error, elapsed_ms=elapsed
        )

    def _timeout_msg(self, elapsed: float) -> CollectResult:
        return CollectResult(
            platform=self.name, status="timeout", error="采集超时", elapsed_ms=elapsed
        )


class BaseDataCollector[T](ABC):
    """Abstract base for non-social data collectors (quote, K-line, fund flow, etc.).

    Returns typed data models directly rather than CollectResult.
    """

    name: str = "base"

    @abstractmethod
    async def fetch(self, symbol: str, **kwargs: Any) -> T:
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

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        processed: list[CollectResult] = []
        for col, res in zip(self._collectors.values(), raw_results):
            if isinstance(res, Exception):
                processed.append(
                    CollectResult(platform=col.name, status="failed", error=str(res))
                )
            elif isinstance(res, CollectResult):
                processed.append(res)

        return processed

    async def _collect_one(
        self, collector: BaseCollector, symbol: str, stock_name: str
    ) -> CollectResult:
        t0 = time.monotonic()
        try:
            return await collector.collect(symbol, stock_name)
        except TimeoutError:
            return collector._timeout_msg((time.monotonic() - t0) * 1000)
        except Exception as e:
            return collector._fail(str(e), (time.monotonic() - t0) * 1000)
