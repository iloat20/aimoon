"""Tests for the technicals tool (Task 5)."""
from __future__ import annotations

import pytest

from aimoon.adapters.driven.ai.tools.technicals import run
from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.kline import KlineData
from aimoon.core.domain.value_objects.kline_bar import KlineBar


def _bar(date: str, close: float, volume: float = 1000.0) -> KlineBar:
    """Build a valid KlineBar with OHLC consistent with validation rules."""
    open_ = close - 1.0
    high = close + 2.0
    low = close - 2.0
    return KlineBar(
        date=date,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        amount=volume * close,
    )


def _trending_up_kline(n: int = 60) -> KlineData:
    bars = [_bar(f"2025-01-{i + 1:02d}", 100.0 + i * 1.5) for i in range(n)]
    return KlineData(symbol="600519", bars=bars)


def _trending_down_kline(n: int = 60) -> KlineData:
    bars = [_bar(f"2025-01-{i + 1:02d}", 200.0 - i * 1.5) for i in range(n)]
    return KlineData(symbol="600519", bars=bars)


def _sideways_kline(n: int = 60) -> KlineData:
    # 近直线 + 极小抖动 → MA20 斜率约 0，趋势判为 震荡。
    bars = [_bar(f"2025-01-{i + 1:02d}", 100.0 + 0.001 * (((i * 7) % 5) - 2)) for i in range(n)]
    return KlineData(symbol="600519", bars=bars)


def _flow() -> CapitalFlowData:
    return CapitalFlowData(
        symbol="600519",
        main_net_5d=1.2e8,
        main_net_3d=4.0e7,
        main_net_10d=2.0e8,
        main_net_20d=3.5e8,
    )


def test_happy_path_uptrend_detected_as_bull() -> None:
    out = run(_trending_up_kline(), _flow())

    assert "__partial__" not in out
    assert out["bar_count"] == 60
    assert out["ma5"] > 0 and out["ma20"] > 0
    assert out["ma60"] > 0
    assert out["trend"] == "多头"
    macd = out["macd"]
    assert "macd" in macd and "signal" in macd and "histogram" in macd
    assert 0 <= out["rsi14"] <= 100
    bb = out["bollinger"]
    assert bb["upper"] >= bb["mid"] >= bb["lower"]
    assert out["volume_ratio_5"] > 0
    assert out["main_net_5d"] == pytest.approx(1.2e8)
    assert out["main_net_20d"] == pytest.approx(3.5e8)


def test_happy_path_downtrend_detected_as_bear() -> None:
    out = run(_trending_down_kline(), _flow())

    assert "__partial__" not in out
    assert out["trend"] == "空头"
    assert out["ma5"] > 0


def test_happy_path_sideways_detected_as_consolidation() -> None:
    out = run(_sideways_kline(), _flow())

    assert "__partial__" not in out
    assert out["trend"] == "震荡"


def test_partial_when_insufficient_bars() -> None:
    kline = KlineData(symbol="600519", bars=[_bar("2025-01-01", 100.0) for _ in range(5)])
    out = run(kline, _flow())

    assert out["__partial__"] == "insufficient_bars"
    assert out["bar_count"] == 5


def test_partial_when_no_bars() -> None:
    out = run(KlineData(symbol="600519", bars=[]), _flow())

    assert out["__partial__"] == "insufficient_bars"
    assert out["bar_count"] == 0


def test_partial_when_kline_is_none_does_not_raise() -> None:
    out = run(None, _flow())
    assert "__partial__" in out
