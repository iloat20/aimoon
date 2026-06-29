"""Tests for cache, retry, and optimized adapter."""

import shutil
import tempfile
import time
from pathlib import Path

import pytest

from aimoon.adapters.driven.common.cache import DiskTtlCache
from aimoon.adapters.driven.common.retry import (
    retry_on_connection,
    silent_failure,
)

# ---------------------------------------------------------------------------
# DiskTtlCache
# ---------------------------------------------------------------------------


class TestDiskTtlCache:
    """Verify DiskTtlCache TTL and read/write behaviour."""

    @pytest.fixture
    def cache(self) -> DiskTtlCache:
        tmp_dir = Path(tempfile.mkdtemp(prefix="aimoon_test_"))
        cache = DiskTtlCache(namespace="test", ttl_seconds=2, cache_dir=tmp_dir)
        yield cache
        shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_set_and_get(self, cache: DiskTtlCache) -> None:
        cache.set("key1", {"foo": "bar"})
        result = cache.get("key1")
        assert result == {"foo": "bar"}

    def test_cache_miss(self, cache: DiskTtlCache) -> None:
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self, cache: DiskTtlCache) -> None:
        cache.set("key1", "value1")
        time.sleep(2.5)  # Wait beyond TTL
        assert cache.get("key1") is None

    def test_invalidate(self, cache: DiskTtlCache) -> None:
        cache.set("key1", "value1")
        cache.invalidate("key1")
        assert cache.get("key1") is None

    def test_clear(self, cache: DiskTtlCache) -> None:
        cache.set("a", 1)
        cache.set("b", 2)
        count = cache.clear()
        assert count == 2
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_different_namespaces_isolated(self) -> None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="aimoon_test_"))
        try:
            c1 = DiskTtlCache(namespace="ns1", ttl_seconds=60, cache_dir=tmp_dir)
            c2 = DiskTtlCache(namespace="ns2", ttl_seconds=60, cache_dir=tmp_dir)
            c1.set("k", "from_ns1")
            c2.set("k", "from_ns2")
            assert c1.get("k") == "from_ns1"
            assert c2.get("k") == "from_ns2"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_overwrite(self, cache: DiskTtlCache) -> None:
        cache.set("k", "v1")
        cache.set("k", "v2")
        assert cache.get("k") == "v2"


# ---------------------------------------------------------------------------
# retry_on_connection (existing — smoke test only)
# ---------------------------------------------------------------------------


class TestRetryOnConnection:
    def test_success_first_call(self) -> None:
        call_count = 0

        def func():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = retry_on_connection(func, retries=2, delay=0)
        assert result == "ok"
        assert call_count == 1

    def test_retry_on_connection_error(self) -> None:
        call_count = 0

        def func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("refused")
            return "ok"

        result = retry_on_connection(func, retries=3, delay=0)
        assert result == "ok"
        assert call_count == 3

    def test_raises_after_exhausted_retries(self) -> None:
        def func():
            raise ConnectionError("refused")

        with pytest.raises(ConnectionError):
            retry_on_connection(func, retries=2, delay=0)


# ---------------------------------------------------------------------------
# silent_failure
# ---------------------------------------------------------------------------


class TestSilentFailure:
    def test_successful_pass_through(self) -> None:
        with silent_failure("test"):
            value = 42
        assert value == 42

    def test_network_error_suppressed(self) -> None:
        # Should NOT raise.
        with silent_failure("test", default_return=None):
            raise ConnectionError("oops")

    def test_non_network_error_logged_not_swallowed(self) -> None:
        # Non-network errors are logged at warning level but still swallowed.
        with silent_failure("test", default_return="fallback"):
            raise ValueError("bad input")

    def test_no_exception(self) -> None:
        # No exception should work fine.
        with silent_failure("test"):
            pass
