"""Base collector and registry pattern for multi-platform data collection."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod

import httpx

from aimoon.adapters.driven.common.cache import DiskTtlCache
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

    Shared infrastructure (lazy httpx client, optional disk cache, resource
    teardown) lives here so concrete collectors only implement :meth:`fetch`
    (and any private ``_fetch_*`` helpers). Concrete collectors may override
    :meth:`_get_client` when they need custom client configuration (e.g. extra
    headers), and set ``_cache_namespace``/``_cache_ttl`` to opt into the
    lazily-created per-instance :attr:`_cache`.
    """

    name: str = "base"
    _default_timeout: float = 15.0
    _cache_namespace: str | None = None
    _cache_ttl: int = 3600

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client_provided = client is not None
        self._client = client
        self._cache_inst: DiskTtlCache | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create a shared :class:`httpx.AsyncClient`.

        Override in a subclass only when custom client config (headers, auth)
        is required; otherwise the shared default is used.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._default_timeout)
        return self._client

    @property
    def _cache(self) -> DiskTtlCache:
        """Lazily-created per-instance disk cache (opt-in via class attrs)."""
        if self._cache_inst is None:
            ns = self._cache_namespace or self.name
            self._cache_inst = DiskTtlCache(namespace=ns, ttl_seconds=self._cache_ttl)
        return self._cache_inst

    async def aclose(self) -> None:
        """Close the client if we created it (not when injected for sharing)."""
        if self._client is not None and not self._client_provided:
            await self._client.aclose()
            self._client = None

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
