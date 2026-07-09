"""Smoke test for the 6 pipeline v2 tools' broad-tolerance contract (P3#18).

Every tool must degrade gracefully to ``{"__partial__": "<reason>"}`` instead of
raising, when fed missing/degenerate input.  This guards the ``@tool_safe``
wrapper and each tool's explicit early-return path so a single bad input can
never abort the analysis pipeline.
"""

import asyncio
import inspect

import pytest

from aimoon.adapters.driven.ai.tools import TOOL_RUNNERS


@pytest.mark.parametrize("name", list(TOOL_RUNNERS.keys()))
def test_tool_runners_degrade_on_missing_input(name):
    """Each tool fed all-None required args must return a ``__partial__`` dict.

    部分工具(如 peer_compare)的 run 是 async 的,返回 coroutine,这里统一 await。
    """
    run = TOOL_RUNNERS[name]
    sig = inspect.signature(run)
    n_required = len(
        [p for p in sig.parameters.values() if p.default is inspect.Parameter.empty]
    )
    # Required positional params get None; optional params keep their defaults.
    result = run(*([None] * n_required))
    if inspect.iscoroutine(result):
        result = asyncio.run(result)

    assert isinstance(result, dict), f"{name} 未返回 dict(返回 {type(result)})"
    assert "__partial__" in result, (
        f"{name} 在输入缺失时未降级为 partial: {result}"
    )


def test_financial_temporal_emits_eps_and_bvps():
    """BUG-2: financial_temporal 必须透传 eps/bvps,否则报告 EPS 列恒为 N/A。

    修复前 _serialize 漏 emit eps/bvps(FinancialData 实体其实有该字段),
    渲染层 y.get('eps') 取到 None -> 'N/A'。
    """
    from aimoon.adapters.driven.ai.pipeline.table_renderer import (
        render_financial_temporal,
    )
    from aimoon.adapters.driven.ai.tools.financial_temporal import (
        run as financial_temporal_run,
    )
    from aimoon.core.domain.entities.financial import FinancialData

    fd = FinancialData(
        symbol="600519",
        report_period="2023年报",
        revenue=100.0,
        net_profit=50.0,
        equity=400.0,
        operating_cf=60.0,
        eps=12.34,
        bvps=20.5,
    )
    result = financial_temporal_run([fd])
    assert isinstance(result, dict) and "__partial__" not in result
    year0 = result["years"][0]
    assert year0["eps"] == 12.34, f"eps 未透传: {year0}"
    assert year0["bvps"] == 20.5, f"bvps 未透传: {year0}"

    rendered = render_financial_temporal(result)
    # 修复后 EPS 列应出现真实数值 12.34,而非 'N/A'
    assert "12.34" in rendered, f"渲染未包含 eps 值(修复前为 N/A): {rendered}"


class _FakeTencentResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeTencentClient:
    def __init__(self, payload):
        self._payload = payload

    async def get(self, url, params=None):
        return _FakeTencentResp(self._payload)

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_kline_tencent_volume_in_shares_not_x100():
    """BUG-1: 腾讯兜底源成交量必须保持「手」,与档1/档2(akshare)及万手图一致。

    回归点: 腾讯原始 volume=12345(手) 绝不能被 ×100。修复前 ×100 会变成股,
    导致腾讯兜底源成交量比正常源大 100 倍。
    """
    from aimoon.adapters.driven.collectors.kline import KlineCollector
    from aimoon.core.domain.services.symbols import to_sina_symbol

    tsymbol = to_sina_symbol("600519")  # 'sh600519'
    payload = {
        "code": 0,
        "data": {
            tsymbol: {
                "qfqday": [
                    ["2024-01-02", "100.0", "105.0", "107.0", "99.0", "12345"],
                ]
            }
        },
    }
    collector = KlineCollector(days=180, client=_FakeTencentClient(payload))
    result = await collector._fetch_tencent("600519")
    assert result is not None, "腾讯兜底解析失败"
    assert result.source == "tencent(fqkline)"
    bar = result.bars[0]
    assert bar.volume == 12345.0, (
        f"腾讯兜底源成交量应为手(12345),实际 {bar.volume} —— 若被 ×100 则错"
    )
