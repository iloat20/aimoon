"""Tests for capital-flow data source (主力 N 日净流入).

Covers the East Money fflow-kline parser ``parse_em_fflow_klines`` and the
cache-fallback behaviour of ``AkshareFinancialAdapter.fetch_capital_flow``.
"""

from __future__ import annotations

import asyncio

import pytest

from aimoon.adapters.driven.common.cache import DiskTtlCache
from aimoon.adapters.driven.financial.akshare_adapter import (
    AkshareFinancialAdapter,
    parse_em_fflow_klines,
)


def _kline(date: str, main: float) -> str:
    # date,主力净流入-净额,占比,散户,...,  列[1] 即主力净流入-净额(元)
    return f"{date},{main},1.0,2.0,3.0,4.0"


def test_parse_em_fflow_klines_basic_5rows() -> None:
    payload = {
        "rc": 0,
        "data": {
            "klines": [
                _kline("2026-06-26", -100_273_936.0),
                _kline("2026-06-29", 50_000_000.0),
                _kline("2026-06-30", -20_000_000.0),
                _kline("2026-07-01", 30_000_000.0),
                _kline("2026-07-02", 40_000_000.0),
            ]
        },
    }
    res = parse_em_fflow_klines(payload)
    assert res["recent_date"] == "2026-07-02"
    # 5日: -100273936 + 50000000 - 20000000 + 30000000 + 40000000 = -273936
    assert res["main_net_5d"] == pytest.approx(-273_936.0)
    # 3日取最后 3 行: -20000000 + 30000000 + 40000000 = 50000000
    assert res["main_net_3d"] == pytest.approx(50_000_000.0)
    # 不足 10/20 日则不返回该键
    assert "main_net_10d" not in res
    assert "main_net_20d" not in res


def test_parse_em_fflow_klines_25rows_all_windows() -> None:
    rows = [_kline(f"2026-01-{i:02d}", float(i * 1_000_000)) for i in range(1, 26)]
    payload = {"rc": 0, "data": {"klines": rows}}
    res = parse_em_fflow_klines(payload)
    assert res["main_net_5d"] == pytest.approx(sum(i * 1_000_000 for i in range(21, 26)))
    assert res["main_net_10d"] == pytest.approx(sum(i * 1_000_000 for i in range(16, 26)))
    assert res["main_net_20d"] == pytest.approx(sum(i * 1_000_000 for i in range(6, 26)))
    assert res["main_net_3d"] == pytest.approx(sum(i * 1_000_000 for i in range(23, 26)))


def test_parse_em_fflow_klines_empty_raises() -> None:
    with pytest.raises(ValueError):
        parse_em_fflow_klines({"rc": 0, "data": {}})
    with pytest.raises(ValueError):
        parse_em_fflow_klines({"rc": 0, "data": {"klines": []}})


def test_parse_em_fflow_klines_malformed_row_treated_zero() -> None:
    # 某行列[1] 非数字 → 按 0 处理,不整体崩溃
    payload = {
        "rc": 0,
        "data": {
            "klines": [
                _kline("2026-07-01", 10_000_000.0),
                "2026-07-02,not_a_number,1,2,3,4",
                _kline("2026-07-03", 20_000_000.0),
            ]
        },
    }
    res = parse_em_fflow_klines(payload)
    # 3 行数据: 无 5日 键;3日 = 10M + 0(非数字) + 20M = 30M
    assert "main_net_5d" not in res
    assert res["main_net_3d"] == pytest.approx(30_000_000.0)


def test_fetch_capital_flow_returns_cache_on_live_failure(
    tmp_path: object,
) -> None:
    """缓存命中即返回缓存值,实时拉取失败时也不会退化成 {}。

    用隔离的临时缓存目录,避免污染真实的 ``cache/akshare_capital_flow``
    (否则会影响后续真实报告生成,误把测试夹具当真实数据)。
    """
    adapter = AkshareFinancialAdapter()
    adapter._capital_flow_cache = DiskTtlCache(namespace="test", cache_dir=tmp_path)
    cached = {
        "recent_date": "2026-07-23",
        "main_net_5d": -123_456_789.0,
        "main_net_3d": -10_000_000.0,
    }
    asyncio.run(adapter._capital_flow_cache.aset("flow:000651", cached))

    # 让实时拉取(直连 EM + akshare 兜底)全部失败
    async def _fail(self: AkshareFinancialAdapter, symbol: str) -> dict:
        return {}

    import aimoon.adapters.driven.financial.akshare_adapter as mod

    orig = mod.AkshareFinancialAdapter._fetch_capital_flow_em_direct
    mod.AkshareFinancialAdapter._fetch_capital_flow_em_direct = _fail
    try:
        result = asyncio.run(adapter.fetch_capital_flow("000651"))
    finally:
        mod.AkshareFinancialAdapter._fetch_capital_flow_em_direct = orig

    assert result.get("main_net_5d") == pytest.approx(-123_456_789.0)
