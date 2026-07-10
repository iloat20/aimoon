"""repo/orchestrator close() 链路测试 — 确保浏览器在同一事件循环内关闭。

目的：验证 CompositeStockAnalysisRepository.close() → CollectorOrchestrator.close()
→ SocialMediaOrchestrator.shutdown() → BrowserFactory.shutdown() 的委托链完整，
从而避免进程退出时 Windows ProactorEventLoop 的 "unclosed transport" 收尾噪声。
"""

from __future__ import annotations

from typing import Any

import pytest

from aimoon.adapters.driven.collectors.composite_repo import (
    CompositeStockAnalysisRepository,
)
from aimoon.adapters.driven.collectors.orchestrator import CollectorOrchestrator
from aimoon.adapters.driven.collectors.social_orchestrator import (
    SocialMediaOrchestrator,
)


class _FakeSocial:
    def __init__(self) -> None:
        self.shutdown_called = False

    async def shutdown(self) -> None:
        self.shutdown_called = True


class _FakeBrowserFactory:
    def __init__(self) -> None:
        self.shutdown_count = 0

    async def acquire(self) -> Any:
        return object()

    async def release(self, browser: Any) -> None:
        pass

    async def shutdown(self) -> None:
        self.shutdown_count += 1


@pytest.mark.asyncio
async def test_collector_orchestrator_close_delegates_to_social():
    orch = CollectorOrchestrator(social_collector=_FakeSocial())
    await orch.close()
    assert orch._social_collector.shutdown_called


@pytest.mark.asyncio
async def test_composite_repo_close_delegates_to_orchestrator():
    fake = _FakeSocial()
    repo = CompositeStockAnalysisRepository(social_collector=fake)
    await repo.close()
    assert fake.shutdown_called


@pytest.mark.asyncio
async def test_social_orchestrator_shutdown_closes_factory():
    fake = _FakeBrowserFactory()
    orch = SocialMediaOrchestrator(browser_factory=fake)
    await orch.shutdown()
    assert fake.shutdown_count == 1
