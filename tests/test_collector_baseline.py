"""Baseline tests for collector stream buffer and exception handling.

Verifies that collectors handle malformed data, network failures,
and edge cases without silent data loss or unexpected crashes.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from aimoon.adapters.driven.collectors.base import (
    BaseCollector,
    CollectorRegistry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# QuoteCollector — narrow except verification
# ---------------------------------------------------------------------------


class TestQuoteCollectorExceptionHandling:
    """Verify QuoteCollector narrows exception types properly."""

    def setup_method(self):
        from aimoon.adapters.driven.collectors.quote import _quote_cache

        _quote_cache.clear()

    def test_fetch_sina_network_error_returns_fallback(self):
        """Sina network error should not silently swallow — must try tencent."""
        from aimoon.adapters.driven.collectors.quote import QuoteCollector

        async def _run_test() -> dict:
            collector = QuoteCollector()
            result = {"sina_called": False, "tencent_called": False}

            async def mock_sina(symbol):
                result["sina_called"] = True
                raise httpx.ConnectError("connection refused")

            async def mock_tencent(symbol, name=""):
                result["tencent_called"] = True
                from aimoon.core.domain.entities.quote import StockQuote

                return StockQuote(
                    symbol=symbol,
                    name="贵州茅台",
                    price=100.0,
                    source="腾讯",
                    updated_at="2026-01-01 00:00:00",
                )

            with (
                patch.object(collector, "_fetch_sina", side_effect=mock_sina),
                patch.object(collector, "_fetch_tencent", side_effect=mock_tencent),
            ):
                quote = await collector.fetch("600519")

            result["source"] = quote.source
            return result

        res = _run(_run_test())
        assert res["sina_called"] is True
        assert res["tencent_called"] is True
        assert res["source"] == "腾讯"

    def test_fetch_all_sources_failed_returns_placeholder(self):
        """When both Sina and Tencent fail, should return placeholder."""
        from aimoon.adapters.driven.collectors.quote import QuoteCollector
        from aimoon.core.domain.entities.quote import StockQuote

        async def _run_test():
            collector = QuoteCollector()

            async def mock_sina(symbol):
                return None  # empty data

            async def mock_tencent(symbol, name=""):
                return None

            with (
                patch.object(collector, "_fetch_sina", side_effect=mock_sina),
                patch.object(collector, "_fetch_tencent", side_effect=mock_tencent),
            ):
                quote = await collector.fetch("600519")

            assert isinstance(quote, StockQuote)
            assert quote.source == "all_failed"

        _run(_run_test())


# ---------------------------------------------------------------------------
# CapitalFlowCollector — HTTP dead code removal verification
# ---------------------------------------------------------------------------


class TestCapitalFlowDeadCodeRemoval:
    """Verify East Money HTTP push2his dead code has been removed."""

    def test_no_push2his_reference(self):
        """push2his.eastmoney.com must not appear in capital_flow source."""
        import inspect

        from aimoon.adapters.driven.collectors import capital_flow

        source = inspect.getsource(capital_flow)
        assert "push2his.eastmoney.com" not in source

    def test_no_fetch_eastmoney_flow_http(self):
        """_fetch_eastmoney_flow_http method must not exist."""
        from aimoon.adapters.driven.collectors.capital_flow import CapitalFlowCollector

        assert not hasattr(CapitalFlowCollector, "_fetch_eastmoney_flow_http")

    def test_capital_flow_module_no_eastmoney_http(self):
        """Module must not contain HTTP eastmoney fallback logic."""
        import inspect

        from aimoon.adapters.driven.collectors import capital_flow

        source = inspect.getsource(capital_flow)
        assert "eastmoney_flow_http" not in source


# ---------------------------------------------------------------------------
# CninfoCollector — exception handling
# ---------------------------------------------------------------------------


class TestCninfoExceptionHandling:
    """Verify CninfoCollector uses typed exceptions."""

    def test_collect_httpx_error_returns_failed(self):
        """HTTP errors should return CollectResult with failed status."""
        from aimoon.adapters.driven.collectors.cninfo import CninfoCollector
        from aimoon.core.domain.value_objects.collect_result import CollectResult

        async def _run_test():
            collector = CninfoCollector()

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

                result = await collector.collect("600519", "贵州茅台")

            assert isinstance(result, CollectResult)
            assert result.status == "failed"

        _run(_run_test())


# ---------------------------------------------------------------------------
# ResearchReportCollector — logging verification
# ---------------------------------------------------------------------------


class TestResearchReportExceptionLogging:
    """Verify ResearchReportCollector logs exceptions."""

    def test_fetch_akshare_failure_logs_warning(self, caplog):
        """akshare failure must produce a log message."""
        from aimoon.adapters.driven.collectors.research_report import (
            ResearchReportCollector,
        )

        async def _run_test():
            collector = ResearchReportCollector()

            with patch.object(
                collector,
                "_fetch_df",
                side_effect=RuntimeError("akshare crashed"),
            ):
                result = await collector.fetch("600519")

            assert result.source == "all_failed"

        with caplog.at_level(
            logging.WARNING, logger="aimoon.adapters.driven.collectors.research_report"
        ):
            _run(_run_test())

        assert any("research_report" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# SilentFailure context manager
# ---------------------------------------------------------------------------


class TestSilentFailure:
    """Verify silent_failure context manager catches only expected exceptions."""

    def test_network_errors_silent(self):
        from aimoon.adapters.driven.common.retry import silent_failure

        with silent_failure("test_ctx"):
            raise ConnectionError("reset by peer")
        # No exception propagated — correct

    def test_value_error_logged(self, caplog):
        """silent_failure logs non-network errors at WARNING level."""
        from aimoon.adapters.driven.common.retry import silent_failure

        with caplog.at_level(logging.WARNING):
            with silent_failure("test_ctx"):
                raise ValueError("bad data")

        # At least one log record from the silent_failure module logger
        assert any("test_ctx" in r.message for r in caplog.records)

    def test_keyboard_interrupt_not_caught(self):
        """KeyboardInterrupt must propagate."""
        from aimoon.adapters.driven.common.retry import silent_failure

        with pytest.raises(KeyboardInterrupt):
            with silent_failure("test_ctx"):
                raise KeyboardInterrupt


# ---------------------------------------------------------------------------
# CollectorRegistry — exception narrowing
# ---------------------------------------------------------------------------


class TestCollectorRegistryErrorHandling:
    """Verify CollectorRegistry._collect_one uses typed exceptions."""

    def test_network_error_returns_failed_result(self):
        """Collector raising ConnectionError should return failed CollectResult."""

        class BrokenCollector(BaseCollector):
            name = "broken"

            async def collect(self, symbol, stock_name=""):
                raise ConnectionError("reset")

        async def _run_test():
            registry = CollectorRegistry()
            collector = BrokenCollector()
            registry.register(collector)
            results = await registry.collect_all("600519")
            assert len(results) == 1
            assert results[0].status == "failed"

        _run(_run_test())


# ---------------------------------------------------------------------------
# Timeout/Stream buffer tests
# ---------------------------------------------------------------------------


class TestCollectorTimeoutHandling:
    """Verify collectors handle timeouts without resource leak."""

    def test_collector_registry_timeout_handling(self):
        """Collector that exceeds timeout should get timeout or failed status."""
        from aimoon.core.domain.value_objects.collect_result import CollectResult

        class SlowCollector(BaseCollector):
            name = "slow"

            async def collect(self, symbol, stock_name=""):
                await asyncio.sleep(100)  # will be interrupted by timeout
                return CollectResult(
                    platform=self.name,
                    status="success",
                    posts=[],
                    count=0,
                    elapsed_ms=100000,
                )

        async def _run_test():
            registry = CollectorRegistry()
            collector = SlowCollector()
            registry.register(collector)
            results = await registry.collect_all("600519", timeout=0.01)
            assert len(results) == 1
            # asyncio.wait_for raises asyncio.TimeoutError which gets
            # collected as a generic Exception in the except clause
            assert results[0].status in ("timeout", "failed")

        _run(_run_test())


# ---------------------------------------------------------------------------
# Shared HTTP client — must not be double-opened / closed by collectors
# ---------------------------------------------------------------------------


class TestSharedHttpClientLifecycle:
    """回归: collector 不得对共享 httpx.AsyncClient 做 async with。

    pipeline.py 创建【一个】共享 client 传给所有并发 collector。若 collector
    对其做 `async with`，第二次进入会抛 'Cannot open a client instance more
    than once'，并会把共享 client 从其它 collector 底下关掉。collector 必须直接
    复用传入的 client、仅在自己创建时才关闭。
    """

    def test_cninfo_does_not_close_shared_client(self):
        from aimoon.adapters.driven.collectors.cninfo import CninfoCollector

        async def _run_test():
            client = httpx.AsyncClient(timeout=5.0)

            async def _fake_post(url, **kw):
                class _R:
                    status_code = 200

                    def json(self):
                        return {"announcements": []}

                return _R()

            client.post = _fake_post  # 桩掉真实网络
            try:
                collector = CninfoCollector(http_client=client)
                await collector.collect("600519", "贵州茅台")
                await collector.collect("600519", "贵州茅台")
                # 共享 client 应由 owner(pipeline) 关闭, collector 不得关
                assert client.is_closed is False
            finally:
                await client.aclose()

        _run(_run_test())

    def test_guba_does_not_close_shared_client(self):
        from aimoon.adapters.driven.collectors.eastmoney_playwright import (
            GubaCollector,
        )

        async def _run_test():
            client = httpx.AsyncClient(timeout=5.0)

            async def _fake_get(url, **kw):
                class _R:
                    status_code = 200
                    text = "<html></html>"

                return _R()

            client.get = _fake_get
            try:
                collector = GubaCollector(http_client=client)
                await collector._fetch_guba_html("600519")
                await collector._fetch_guba_html("600519")
                assert client.is_closed is False
            finally:
                await client.aclose()

        _run(_run_test())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
