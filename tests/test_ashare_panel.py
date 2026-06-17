# tests/test_ashare_panel.py
import math

import numpy as np
import pandas as pd
import pytest
from aimoon.factors.ashare import (
    build_panel,
    compute_amihud_20d,
    compute_ashare_factors,
    compute_bp,
    compute_div_yield,
    compute_ep,
    compute_mom_60d,
    compute_northbound_chg_20d,
    compute_rev_20d,
    compute_rev_5d,
    compute_sector_mom_20d,
    compute_turnover_20d,
    compute_vol_20d,
    robust_zscore,
)


def _kline(n=80):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0,
         "volume": 1000.0, "turnover": 0.5, "amount": 1e6},
        index=idx,
    )


# ---- build_panel ----

def test_build_panel_returns_wide_dict():
    klines = {"000001": _kline(), "000002": _kline()}
    panel = build_panel(klines, min_rows=60)
    assert panel is not None
    assert {"open", "high", "low", "close", "volume"}.issubset(panel.keys())
    assert panel["close"].shape == (80, 2)


def test_build_panel_skips_short_stocks():
    klines = {"000001": _kline(30), "000002": _kline(80)}
    panel = build_panel(klines, min_rows=60)
    assert panel is not None
    assert "000001" not in panel["close"].columns


def test_build_panel_none_when_empty():
    assert build_panel({}, min_rows=60) is None


# ---- robust_zscore ----

def test_robust_zscore_series():
    s = pd.Series([1.0, 2.0, 3.0, 100.0])  # 100 是离群值
    z = robust_zscore(s, clip=3.0)
    assert abs(z.loc[3]) <= 3.0  # 被 clip
    assert abs(z.mean()) < 0.5   # 中位数归零


def test_robust_zscore_all_same():
    s = pd.Series([5.0, 5.0, 5.0])
    z = robust_zscore(s, clip=3.0)
    assert (z == 0.0).all()


def test_robust_zscore_empty():
    assert robust_zscore(pd.Series([], dtype=float), clip=3.0).empty


def test_robust_zscore_dataframe():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [10.0, 20.0, 30.0]})
    z = robust_zscore(df, clip=3.0)
    assert z.shape == (3, 2)


# ---- 因子计算辅助函数 ----

def _panel(days=80, n_stocks=3, base_close=10.0):
    idx = pd.date_range("2024-01-01", periods=days, freq="D")
    close = pd.DataFrame(
        {f"s{i}": base_close + np.random.default_rng(42).random(days).cumsum() * 0.1
         for i in range(n_stocks)},
        index=idx,
    )
    turnover = pd.DataFrame(
        {f"s{i}": 0.3 + np.random.default_rng(42 + i).random(days) * 0.3
         for i in range(n_stocks)},
        index=idx,
    )
    amount = close * turnover * 1e7
    panel = {"close": close, "turnover": turnover, "amount": amount}
    return panel


# ---- 价格类因子 ----

def test_rev_5d_shape():
    panel = _panel(80, 3)
    f = compute_rev_5d(panel["close"])
    assert f.shape == (80, 3)
    assert not f.isna().all().all()


def test_rev_20d_shape():
    panel = _panel(80, 3)
    f = compute_rev_20d(panel["close"])
    assert f.shape == (80, 3)


def test_vol_20d_shape():
    panel = _panel(80, 3)
    f = compute_vol_20d(panel["close"])
    assert f.shape == (80, 3)


def test_mom_60d_shape():
    panel = _panel(80, 3)
    f = compute_mom_60d(panel["close"])
    assert f.shape == (80, 3)


# ---- 换手率 + Amihud ----

def test_turnover_20d_shape():
    panel = _panel(80, 3)
    f = compute_turnover_20d(panel["turnover"])
    assert f.shape == (80, 3)


def test_amihud_20d_shape():
    panel = _panel(80, 3)
    f = compute_amihud_20d(panel["close"], panel["amount"])
    assert f.shape == (80, 3)
    # z-score 后可为负，只需不全是 NaN
    assert not f.isna().all().all()


# ---- 基本面因子 ----

def test_ep_shape():
    pe = pd.DataFrame({"s0": [10.0, 15.0], "s1": [20.0, 25.0]})
    f = compute_ep(pe)
    assert f.shape == (2, 2)


def test_ep_div_by_zero():
    pe = pd.DataFrame({"s0": [0.0, 15.0]})
    f = compute_ep(pe)
    # PE=0 → EP=inf, z-score 后应为 0（MAD=0 时全归零）
    assert not f.isna().any().any()


def test_bp_shape():
    pb = pd.DataFrame({"s0": [1.0, 1.5], "s1": [2.0, 2.5]})
    f = compute_bp(pb)
    assert f.shape == (2, 2)


def test_div_yield_shape():
    dy = pd.DataFrame({"s0": [0.03, 0.04], "s1": [0.05, 0.06]})
    f = compute_div_yield(dy)
    assert f.shape == (2, 2)


# ---- 北向 + 板块 ----

def test_northbound_chg_20d():
    nb = pd.DataFrame({"s0": range(100), "s1": range(100)})
    f = compute_northbound_chg_20d(nb)
    assert f.shape == (100, 2)


def test_sector_mom_20d():
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    close = pd.DataFrame(
        {"a": range(80), "b": range(80), "c": range(80)},
        index=idx,
    )
    sector_map = {"a": "金融", "b": "金融", "c": "科技"}
    f = compute_sector_mom_20d(close, sector_map)
    assert f.shape == (80, 3)
    # 同板块 a 和 b 的值应相同
    np.testing.assert_array_almost_equal(f["a"], f["b"])


# ---- compute_ashare_factors ----

def test_compute_ashare_factors_basic():
    panel = _panel(80, 3)
    sector_map = {"s0": "金融", "s1": "科技", "s2": "医药"}
    pe = pd.DataFrame({c: 15.0 for c in panel["close"].columns}, index=panel["close"].index)
    pb = pd.DataFrame({c: 2.0 for c in panel["close"].columns}, index=panel["close"].index)
    div = pd.DataFrame({c: 0.03 for c in panel["close"].columns}, index=panel["close"].index)
    factors = compute_ashare_factors(panel, sector_map, pe, pb, div)
    # 应该有：4 价格 + 2 换手/amihud + 3 基本面 + 1 北向 + 1 板块 = 11
    # 但没有 northbound 字段，所以实际 10
    assert len(factors) == 10
    for fid, df in factors.items():
        assert df.shape == (80, 3), f"{fid} shape mismatch"


def test_compute_ashare_factors_no_fundamentals():
    panel = _panel(80, 2)
    factors = compute_ashare_factors(panel)
    # 只有价格 + 换手 + Amihud = 6
    assert len(factors) == 6


def test_compute_ashare_factors_empty_close():
    panel = {"close": pd.DataFrame()}
    assert compute_ashare_factors(panel) == {}
