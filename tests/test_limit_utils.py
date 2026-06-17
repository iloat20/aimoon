"""Tests for A-share limit up/down detection utilities."""

from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.data.limit_utils import (
    _is_limit_at_open,
    can_buy_at_open,
    can_sell_at_close,
    can_sell_at_open,
    detect_price_limit,
    is_limit_down,
    is_limit_up,
)


def _make_kline(close_prices: list[float], open_prices: list[float] | None = None) -> pd.DataFrame:
    n = len(close_prices)
    if open_prices is None:
        open_prices = close_prices
    dates = pd.date_range("2025-06-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": open_prices,
            "high": np.maximum(close_prices, open_prices) * 1.01,
            "low": np.minimum(close_prices, open_prices) * 0.99,
            "close": close_prices,
            "volume": np.full(n, 1e6),
        },
        index=dates,
    )


class TestDetectPriceLimit:
    def test_no_limit(self):
        assert detect_price_limit(10.0, 10.5) is None

    def test_limit_up(self):
        assert detect_price_limit(10.0, 11.0) == "limit_up"

    def test_limit_down(self):
        assert detect_price_limit(10.0, 9.0) == "limit_down"

    def test_st_5pct_limit(self):
        assert detect_price_limit(10.0, 10.3, is_st=True) is None
        assert detect_price_limit(10.0, 10.5, is_st=True) == "limit_up"

    def test_star_20pct_limit(self):
        assert detect_price_limit(10.0, 11.9, is_star=True) is None
        assert detect_price_limit(10.0, 12.0, is_star=True) == "limit_up"

    def test_prev_close_zero(self):
        assert detect_price_limit(0.0, 10.0) is None


class TestIsLimitUpDown:
    def test_limit_up_by_close(self):
        kline = _make_kline([10.0, 11.0])
        date = kline.index[1]
        assert is_limit_up(kline, date) is True
        assert is_limit_down(kline, date) is False

    def test_limit_down_by_close(self):
        kline = _make_kline([10.0, 9.0])
        date = kline.index[1]
        assert is_limit_down(kline, date) is True
        assert is_limit_up(kline, date) is False

    def test_no_limit_by_close(self):
        kline = _make_kline([10.0, 10.5])
        date = kline.index[1]
        assert is_limit_up(kline, date) is False
        assert is_limit_down(kline, date) is False

    def test_first_row_no_prev(self):
        kline = _make_kline([10.0])
        date = kline.index[0]
        assert is_limit_up(kline, date) is False
        assert is_limit_down(kline, date) is False

    def test_date_not_in_index(self):
        kline = _make_kline([10.0, 11.0])
        assert is_limit_up(kline, "2099-01-01") is False


class TestIsLimitAtOpen:
    def test_limit_up_at_open(self):
        """Open at 11.0 vs prev close 10.0 → limit-up."""
        kline = _make_kline([10.0, 11.0], open_prices=[10.0, 11.0])
        date = kline.index[1]
        assert _is_limit_at_open(kline, date, check_up=True) is True
        assert _is_limit_at_open(kline, date, check_up=False) is False

    def test_limit_down_at_open(self):
        """Open at 9.0 vs prev close 10.0 → limit-down."""
        kline = _make_kline([10.0, 9.0], open_prices=[10.0, 8.99])
        date = kline.index[1]
        assert _is_limit_at_open(kline, date, check_up=False) is True
        assert _is_limit_at_open(kline, date, check_up=True) is False

    def test_no_limit_at_open(self):
        kline = _make_kline([10.0, 10.5], open_prices=[10.0, 10.3])
        date = kline.index[1]
        assert _is_limit_at_open(kline, date, check_up=True) is False
        assert _is_limit_at_open(kline, date, check_up=False) is False

    def test_first_row_returns_false(self):
        kline = _make_kline([10.0])
        date = kline.index[0]
        assert _is_limit_at_open(kline, date, check_up=True) is False
        assert _is_limit_at_open(kline, date, check_up=False) is False

    def test_date_not_in_index(self):
        kline = _make_kline([10.0, 11.0])
        assert _is_limit_at_open(kline, "2099-01-01", check_up=True) is False


class TestCanBuyAtOpen:
    def test_can_buy_normal_day(self):
        kline = _make_kline([10.0, 10.5])
        assert can_buy_at_open(kline, kline.index[1]) is True

    def test_cannot_buy_limit_up(self):
        kline = _make_kline([10.0, 11.0], open_prices=[10.0, 11.0])
        assert can_buy_at_open(kline, kline.index[1]) is False

    def test_can_buy_limit_down(self):
        """Limit-down opens are buyable."""
        kline = _make_kline([10.0, 9.0], open_prices=[10.0, 8.99])
        assert can_buy_at_open(kline, kline.index[1]) is True


class TestCanSellAtOpen:
    def test_can_sell_normal_day(self):
        kline = _make_kline([10.0, 10.5])
        assert can_sell_at_open(kline, kline.index[1]) is True

    def test_cannot_sell_limit_down(self):
        kline = _make_kline([10.0, 9.0], open_prices=[10.0, 8.99])
        assert can_sell_at_open(kline, kline.index[1]) is False

    def test_can_sell_limit_up(self):
        """Limit-up opens are sellable."""
        kline = _make_kline([10.0, 11.0], open_prices=[10.0, 11.0])
        assert can_sell_at_open(kline, kline.index[1]) is True


class TestCanSellAtClose:
    def test_can_sell_normal_day(self):
        kline = _make_kline([10.0, 10.5])
        assert can_sell_at_close(kline, kline.index[1]) is True

    def test_cannot_sell_limit_down(self):
        kline = _make_kline([10.0, 9.0])
        assert can_sell_at_close(kline, kline.index[1]) is False

    def test_can_sell_limit_up(self):
        kline = _make_kline([10.0, 11.0])
        assert can_sell_at_close(kline, kline.index[1]) is True
