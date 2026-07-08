"""Phase 1 基础设施层测试 — Container / ProgressReporter / HttpClient / BrowserFactory。"""

from __future__ import annotations

import pytest

from aimoon.core.application.browser_factory import PlaywrightBrowserFactory
from aimoon.core.application.container import Container
from aimoon.core.application.http_client import FakeHttpClient, HttpResponse
from aimoon.core.application.progress import (
    CliProgressReporter,
    NullProgressReporter,
    RecordingProgressReporter,
)

# ── Container ──────────────────────────────────────────────────────────


class _FakeService:
    def __init__(self, value: int = 42) -> None:
        self.value = value


def test_container_register_and_resolve():
    """注册工厂后 resolve 返回单例。"""
    c = Container()
    c.register(_FakeService, lambda: _FakeService(99))
    svc = c.resolve(_FakeService)
    assert svc.value == 99
    # 第二次 resolve 返回同一实例（单例）
    assert c.resolve(_FakeService) is svc


def test_container_override():
    """override 替换实现，resolve 返回覆盖实例。"""
    c = Container()
    c.register(_FakeService, lambda: _FakeService(1))
    mock = _FakeService(999)
    c.override(_FakeService, mock)
    assert c.resolve(_FakeService) is mock


def test_container_reset():
    """reset 清除单例和覆盖，保留工厂注册。"""
    c = Container()
    c.register(_FakeService, lambda: _FakeService(1))
    first = c.resolve(_FakeService)
    c.override(_FakeService, _FakeService(2))
    c.reset()
    second = c.resolve(_FakeService)
    assert second is not first
    assert second.value == 1  # 工厂仍注册，回到工厂产出


def test_container_resolve_unregistered():
    """resolve 未注册类型抛 KeyError。"""
    c = Container()
    with pytest.raises(KeyError):
        c.resolve(_FakeService)


# ── ProgressReporter ──────────────────────────────────────────────────


def test_null_reporter_silent(capsys):
    """NullProgressReporter 不产生任何输出。"""
    r = NullProgressReporter()
    r.report("hello")
    r.progress("stage", current=1, total=3)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_cli_reporter_prints(capsys):
    """CliProgressReporter 调用 print。"""
    r = CliProgressReporter()
    r.report(" 采集行情...")
    captured = capsys.readouterr()
    assert "采集行情" in captured.out


def test_recording_reporter_records():
    """RecordingProgressReporter 记录所有调用供断言。"""
    r = RecordingProgressReporter()
    r.report("msg1", level="info")
    r.report("msg2", level="warning")
    r.progress("采集K线", current=2, total=5)
    assert len(r.messages) == 2
    assert r.messages[0] == ("info", "msg1")
    assert r.messages[1] == ("warning", "msg2")
    assert r.progress_calls == [("采集K线", 2, 5)]


# ── HttpClient ────────────────────────────────────────────────────────


def test_http_response_json():
    """HttpResponse.json() 解析 JSON。"""
    resp = HttpResponse(status_code=200, text='{"key": "value"}')
    assert resp.ok is True
    assert resp.json() == {"key": "value"}


def test_http_response_not_ok():
    """非 2xx 状态码 ok=False。"""
    resp = HttpResponse(status_code=404, text="not found")
    assert resp.ok is False


@pytest.mark.asyncio
async def test_fake_http_client_get():
    """FakeHttpClient 按 URL 模式返回预设响应。"""
    fake = FakeHttpClient()
    fake.add_response("sinajs.cn", HttpResponse(status_code=200, text="data"))
    resp = await fake.get("https://hq.sinajs.cn/list=sh600519")
    assert resp.status_code == 200
    assert resp.text == "data"
    assert fake.calls == [("get", "https://hq.sinajs.cn/list=sh600519")]


@pytest.mark.asyncio
async def test_fake_http_client_no_mock():
    """无匹配 mock 时抛 ConnectionError。"""
    fake = FakeHttpClient()
    with pytest.raises(ConnectionError):
        await fake.get("https://unknown.url/")


@pytest.mark.asyncio
async def test_fake_http_client_post():
    """FakeHttpClient post 同样按 URL 模式匹配。"""
    fake = FakeHttpClient()
    fake.add_response("api/deepseek", HttpResponse(status_code=200, text="{}"))
    resp = await fake.post("https://api/deepseek.com/v1/chat", json={"x": 1})
    assert resp.status_code == 200
    assert len(fake.calls) == 1


# ── BrowserFactory ────────────────────────────────────────────────────


class _FakeBrowser:
    """测试用浏览器桩。"""
    def __init__(self) -> None:
        self.closed = False
    async def close(self) -> None:
        self.closed = True


class _FakePlaywright:
    """测试用 Playwright 桩。"""
    def __init__(self) -> None:
        self.stopped = False
    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_browser_factory_state_management():
    """PlaywrightBrowserFactory 状态管理：初始为空，shutdown 后清空。"""
    factory = PlaywrightBrowserFactory()
    # 初始状态：browser 和 pw_instance 都是 None
    assert factory._browser is None
    assert factory._pw_instance is None

    # shutdown 在未启动时不报错（幂等）
    await factory.shutdown()
    assert factory._browser is None
    assert factory._pw_instance is None


@pytest.mark.asyncio
async def test_browser_factory_singleton_logic():
    """PlaywrightBrowserFactory 单例逻辑：手动设置 browser 后 acquire 直接返回。"""
    factory = PlaywrightBrowserFactory()
    # 模拟已启动状态
    fake_browser = object()
    factory._browser = fake_browser

    # acquire 应直接返回已有的 browser，不触发 Playwright 启动
    b1 = await factory.acquire()
    b2 = await factory.acquire()
    assert b1 is fake_browser
    assert b2 is fake_browser

    # 清理
    factory._browser = None
    factory._pw_instance = None

