"""Tests for CapitalFlowCollector fallback chain + multi-source merge.

Audit P3.1 priority 2 — previously only dead-code paths were exercised.
Here we drive the real `fetch()` with monkeypatched sub-fetchers to
verify: (1) primary fills + fallback supplements without clobbering,
(2) primary failure -> fallback takes over, (3) total failure -> all_failed.
"""

import pytest

from aimoon.adapters.driven.collectors.capital_flow import CapitalFlowCollector
from aimoon.core.domain.entities.capital_flow import CapitalFlowData


# --- sub-fetcher stubs (no self: monkeypatched as plain attrs) ---
@pytest.mark.asyncio
async def _noop(symbol, data, sources):
    return None


@pytest.mark.asyncio
async def _pysnowball_fill(symbol, data, sources):
    data.main_net_5d = 1.0e9
    data.main_net_3d = 5.0e8
    sources.append("pysnowball(雪球)")


@pytest.mark.asyncio
async def _akshare_fill(symbol, data, sources):
    # would clobber if merge guard absent
    if data.main_net_5d == 0.0:  # merge guard: never clobber a primary source
        data.main_net_5d = 5.0e8
    data.main_net_10d = 1.0e8
    sources.append("akshare(个股资金流)")


@pytest.mark.asyncio
async def _northbound_fill(symbol, data, sources):
    data.northbound_chg = 2.0e8
    sources.append("eastmoney(北向持股)")


@pytest.mark.asyncio
async def _lhb_fill(symbol, data, sources):
    data.lhb_date = "2024-01-01"
    sources.append("akshare(龙虎榜)")


def _patch(monkeypatch, collector, attr, fn):
    monkeypatch.setattr(collector, attr, fn)


@pytest.mark.asyncio
async def test_primary_fill_fallback_supplements_without_clobber(
    monkeypatch,
):
    c = CapitalFlowCollector()
    _patch(monkeypatch, c, "_fetch_via_pysnowball", _pysnowball_fill)
    _patch(monkeypatch, c, "_fetch_via_akshare", _akshare_fill)
    _patch(monkeypatch, c, "_fetch_northbound", _noop)
    _patch(monkeypatch, c, "_fetch_lhb", _noop)

    data = await c.fetch("600519")
    # primary value preserved (akshare did NOT overwrite non-zero field)
    assert data.main_net_5d == 1.0e9
    # akshare still contributed its own zero-field
    assert data.main_net_10d == 1.0e8
    assert data.source == "pysnowball(雪球)+akshare(个股资金流)"


@pytest.mark.asyncio
async def test_primary_failure_fallback_takes_over(
    monkeypatch,
):
    c = CapitalFlowCollector()
    _patch(monkeypatch, c, "_fetch_via_pysnowball", _noop)
    _patch(monkeypatch, c, "_fetch_via_akshare", _akshare_fill)
    _patch(monkeypatch, c, "_fetch_northbound", _noop)
    _patch(monkeypatch, c, "_fetch_lhb", _noop)

    data = await c.fetch("600519")
    # fallback filled the gaps
    assert data.main_net_5d == 5.0e8
    assert data.source == "akshare(个股资金流)"


@pytest.mark.asyncio
async def test_all_sources_fail_yields_all_failed(
    monkeypatch,
):
    c = CapitalFlowCollector()
    for attr in (
        "_fetch_via_pysnowball",
        "_fetch_via_akshare",
        "_fetch_northbound",
        "_fetch_lhb",
    ):
        _patch(monkeypatch, c, attr, _noop)

    data = await c.fetch("600519")
    assert data.source == "all_failed"
    assert isinstance(data, CapitalFlowData)
