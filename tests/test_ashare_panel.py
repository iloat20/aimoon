# tests/test_ashare_panel.py
import pandas as pd
from aimoon.factors.ashare import build_panel


def _kline(n=80):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0,
         "volume": 1000.0, "turnover": 0.5, "amount": 1e6},
        index=idx,
    )


def test_build_panel_returns_wide_dict():
    klines = {"000001": _kline(), "000002": _kline()}
    panel = build_panel(klines, min_rows=60)
    assert panel is not None
    assert set(["open", "high", "low", "close", "volume"]).issubset(panel.keys())
    assert panel["close"].shape == (80, 2)


def test_build_panel_skips_short_stocks():
    klines = {"000001": _kline(30), "000002": _kline(80)}
    panel = build_panel(klines, min_rows=60)
    assert panel is not None
    assert "000001" not in panel["close"].columns


def test_build_panel_none_when_too_few():
    assert build_panel({}, min_rows=60) is None
