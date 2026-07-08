"""HTTP client abstraction — decouples collectors from httpx.

Protocol-based: collectors depend on this interface. HttpxClient wraps httpx
for production; FakeHttpClient serves tests by URL-pattern matching.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class HttpResponse:
    """HTTP 响应值对象。"""

    status_code: int
    text: str

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class HttpClient(Protocol):
    """HTTP 客户端抽象 — 采集器依赖此接口。"""

    async def get(self, url: str, **kwargs: Any) -> HttpResponse: ...

    async def post(self, url: str, **kwargs: Any) -> HttpResponse: ...

    async def aclose(self) -> None: ...


class HttpxClient:
    """生产实现 — 包装 httpx.AsyncClient。"""

    def __init__(self, timeout: float = 30.0, **kwargs: Any) -> None:
        import httpx

        self._client = httpx.AsyncClient(timeout=timeout, **kwargs)

    async def get(self, url: str, **kwargs: Any) -> HttpResponse:
        resp = await self._client.get(url, **kwargs)
        return HttpResponse(status_code=resp.status_code, text=resp.text)

    async def post(self, url: str, **kwargs: Any) -> HttpResponse:
        resp = await self._client.post(url, **kwargs)
        return HttpResponse(status_code=resp.status_code, text=resp.text)

    async def aclose(self) -> None:
        await self._client.aclose()


class FakeHttpClient:
    """测试用 — 根据 URL 模式返回预设响应。"""

    def __init__(self) -> None:
        self._responses: dict[str, HttpResponse] = {}
        self.calls: list[tuple[str, str]] = []  # (method, url)

    def add_response(self, url_pattern: str, response: HttpResponse) -> None:
        """注册一个 URL 子串模式对应的响应。"""
        self._responses[url_pattern] = response

    async def get(self, url: str, **kwargs: Any) -> HttpResponse:
        self.calls.append(("get", url))
        for pattern, resp in self._responses.items():
            if pattern in url:
                return resp
        raise ConnectionError(f"No mock for GET {url}")

    async def post(self, url: str, **kwargs: Any) -> HttpResponse:
        self.calls.append(("post", url))
        for pattern, resp in self._responses.items():
            if pattern in url:
                return resp
        raise ConnectionError(f"No mock for POST {url}")

    async def aclose(self) -> None:
        pass
