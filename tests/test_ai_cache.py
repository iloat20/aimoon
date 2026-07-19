"""独立单测: AI 分析/骨架磁盘缓存(ai/cache.py)。

补 D2 文档指出的测试缺口 —— 之前 ai/cache.py 仅有少量间接覆盖,
本次为 set/get 生命周期 + analysis/skeleton 双键隔离(防 2026-07-14 的 key 碰撞 bug 回归) 补独立用例。

用 tmp 目录隔离模块级单例 ``_cache``,不污染真实缓存命名空间。
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from aimoon.adapters.driven.ai import cache as ai_cache
from aimoon.adapters.driven.common.cache import DiskTtlCache


@pytest.fixture
def isolated_cache(monkeypatch: pytest.MonkeyPatch) -> DiskTtlCache:
    """把模块级单例 _cache 重定向到 tmp 目录,避免污染真实 ai_analysis 命名空间。"""
    tmp_dir = Path(tempfile.mkdtemp(prefix="aimoon_ai_cache_"))
    c = DiskTtlCache(namespace="ai_analysis", ttl_seconds=86400, cache_dir=tmp_dir)
    monkeypatch.setattr(ai_cache, "_cache", c)
    yield c
    import shutil

    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_analysis_set_get_roundtrip(isolated_cache: DiskTtlCache) -> None:
    ai_cache.set_analysis_cache("600519", "full report text")
    assert ai_cache.get_analysis_cache("600519") == "full report text"


def test_analysis_miss_returns_none(isolated_cache: DiskTtlCache) -> None:
    assert ai_cache.get_analysis_cache("000001") is None


def test_skeleton_set_get_roundtrip(isolated_cache: DiskTtlCache) -> None:
    ai_cache.set_skeleton_cache("600519", '{"skeleton": true}')
    assert ai_cache.get_skeleton_cache("600519") == '{"skeleton": true}'


def test_skeleton_miss_returns_none(isolated_cache: DiskTtlCache) -> None:
    assert ai_cache.get_skeleton_cache("000001") is None


def test_analysis_and_skeleton_keys_do_not_collide(
    isolated_cache: DiskTtlCache,
) -> None:
    """analysis:* 与 skeleton:* 必须用不同 key,否则终稿会覆盖骨架(历史 bug)。"""
    ai_cache.set_analysis_cache("600519", "FINAL_REPORT")
    ai_cache.set_skeleton_cache("600519", "JSON_SKELETON")
    # 各自仍能读到自己的值,不被对方覆盖
    assert ai_cache.get_analysis_cache("600519") == "FINAL_REPORT"
    assert ai_cache.get_skeleton_cache("600519") == "JSON_SKELETON"


def test_analysis_expiry_returns_none(isolated_cache: DiskTtlCache) -> None:
    """短 TTL 写入后过期 → get 返回 None(静默失效,不抛错)。

    注: ``set_analysis_cache`` 内部会用 21600/86400 覆盖 payload 的 ttl 字段,
    因此不能直接靠外部 ttl_seconds 触发过期;这里走底层 _cache.aset 注入
    一个 ttl=1 的 payload,验证 get_analysis_cache 的过期分支能正确返回 None。
    """
    today = __import__("datetime").datetime.now().strftime("%Y%m%d")
    # aset 是 async(线程池卸载),必须 await 才真正落盘。
    import asyncio

    asyncio.run(
        isolated_cache.aset(
            f"analysis:600519:{today}", {"report_text": "ephemeral", "ttl": 1}
        )
    )
    assert ai_cache.get_analysis_cache("600519") == "ephemeral"
    time.sleep(1.2)
    assert ai_cache.get_analysis_cache("600519") is None
