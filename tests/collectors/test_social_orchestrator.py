"""SocialMediaOrchestrator 注入测试 — BrowserFactory + ProgressReporter。"""

from __future__ import annotations

from typing import Any

import pytest

from aimoon.adapters.driven.collectors.social_orchestrator import SocialMediaOrchestrator
from aimoon.core.application.progress import NullProgressReporter, RecordingProgressReporter


class _FakeBrowserFactory:
    """测试用浏览器工厂 — 不启动真实 Playwright。"""

    def __init__(self) -> None:
        self.acquire_count = 0
        self.shutdown_count = 0

    async def acquire(self) -> Any:
        self.acquire_count += 1
        return object()  # browser 桩

    async def release(self, browser: Any) -> None:
        pass

    async def shutdown(self) -> None:
        self.shutdown_count += 1


@pytest.mark.asyncio
async def test_social_orchestrator_uses_injected_browser_factory():
    """注入的 BrowserFactory.acquire 被调用，不触发真实 Playwright。"""
    fake_factory = _FakeBrowserFactory()
    reporter = NullProgressReporter()
    orch = SocialMediaOrchestrator(browser_factory=fake_factory, reporter=reporter)

    # collect 会调用 acquire，但采集器会失败（无真实数据源）→ 走 mock fallback
    posts, results = await orch.collect("600519", "贵州茅台")

    assert fake_factory.acquire_count >= 1
    # 无真实数据源时所有平台走 mock fallback
    assert len(results) == 3  # 股吧 + 巨潮 + 微信


@pytest.mark.asyncio
async def test_social_orchestrator_uses_injected_reporter():
    """注入的 RecordingProgressReporter 记录进度消息。"""
    fake_factory = _FakeBrowserFactory()
    reporter = RecordingProgressReporter()
    orch = SocialMediaOrchestrator(browser_factory=fake_factory, reporter=reporter)

    await orch.collect("600519", "贵州茅台")

    # 至少记录了"采集社交媒体舆情"消息
    assert any("采集社交媒体舆情" in msg for _, msg in reporter.messages)
    # 每个平台都有一条输出
    platform_msgs = [msg for _, msg in reporter.messages if "条" in msg]
    assert len(platform_msgs) >= 3


@pytest.mark.asyncio
async def test_social_orchestrator_shutdown_delegates():
    """shutdown 委托给注入的 BrowserFactory。"""
    fake_factory = _FakeBrowserFactory()
    orch = SocialMediaOrchestrator(browser_factory=fake_factory, reporter=NullProgressReporter())

    await orch.shutdown()
    assert fake_factory.shutdown_count == 1


@pytest.mark.asyncio
async def test_social_orchestrator_default_reporter_is_cli():
    """不注入 reporter 时默认用 CliProgressReporter（不报错）。"""
    fake_factory = _FakeBrowserFactory()
    orch = SocialMediaOrchestrator(browser_factory=fake_factory)
    # 不抛异常即通过
    await orch.collect("000001", "平安银行")
