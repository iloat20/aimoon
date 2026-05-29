"""Technical indicators calculation module with lazy caching"""
from __future__ import annotations

import pandas as pd

from aimoon.config import CONFIG


class TechnicalIndicators:
    """Calculate technical indicators for K-line data with lazy caching.

    Parameters
    ----------
    df : pd.DataFrame
        K-line data with columns: close, high, low, volume.
    start_idx : int, optional
        If given, only rows from ``start_idx`` onward are used for indicator
        calculations.  ``add_all_indicators()`` always operates on the full
        DataFrame regardless of this parameter.
    """

    def __init__(self, df: pd.DataFrame, start_idx: int = 0) -> None:
        self._df_original = df
        self._start_idx = start_idx

        sliced = df.iloc[start_idx:]
        self._close = pd.to_numeric(sliced["close"], errors="coerce")
        self._high = pd.to_numeric(sliced["high"], errors="coerce")
        self._low = pd.to_numeric(sliced["low"], errors="coerce")
        self._volume = pd.to_numeric(sliced["volume"], errors="coerce")

        # Lazy caches
        self._ma_cache: dict[int, pd.Series] = {}
        self._ema_cache: dict[int, pd.Series] = {}
        self._macd_cache: tuple[pd.Series, pd.Series, pd.Series] | None = None
        self._kdj_cache: tuple[pd.Series, pd.Series, pd.Series] | None = None
        self._boll_cache: tuple[pd.Series, pd.Series, pd.Series] | None = None

    # ------------------------------------------------------------------
    # Moving averages
    # ------------------------------------------------------------------

    def ma(self, period: int) -> pd.Series:
        if period not in self._ma_cache:
            self._ma_cache[period] = self._close.rolling(window=period).mean()
        return self._ma_cache[period]

    def ema(self, period: int) -> pd.Series:
        if period not in self._ema_cache:
            self._ema_cache[period] = self._close.ewm(span=period, adjust=False).mean()
        return self._ema_cache[period]

    def ma_trend(self) -> str:
        ma_s = self.ma(CONFIG.ma_short)
        ma_m = self.ma(CONFIG.ma_mid)
        ma_l = self.ma(CONFIG.ma_long)
        if pd.isna(ma_s.iloc[-1]) or pd.isna(ma_m.iloc[-1]) or pd.isna(ma_l.iloc[-1]):
            return "neutral"
        if ma_s.iloc[-1] > ma_m.iloc[-1] > ma_l.iloc[-1]:
            return "bullish"
        if ma_s.iloc[-1] < ma_m.iloc[-1] < ma_l.iloc[-1]:
            return "bearish"
        return "neutral"

    def ma_golden_cross(self) -> bool:
        ma_s = self.ma(CONFIG.ma_short)
        ma_m = self.ma(CONFIG.ma_mid)
        if len(ma_s) < 2 or pd.isna(ma_s.iloc[-1]):
            return False
        return ma_s.iloc[-2] <= ma_m.iloc[-2] and ma_s.iloc[-1] > ma_m.iloc[-1]

    def ma_death_cross(self) -> bool:
        ma_s = self.ma(CONFIG.ma_short)
        ma_m = self.ma(CONFIG.ma_mid)
        if len(ma_s) < 2 or pd.isna(ma_s.iloc[-1]):
            return False
        return ma_s.iloc[-2] >= ma_m.iloc[-2] and ma_s.iloc[-1] < ma_m.iloc[-1]

    # ------------------------------------------------------------------
    # RSI
    # ------------------------------------------------------------------

    def rsi(self, period: int | None = None) -> pd.Series:
        period = period or CONFIG.rsi_period
        delta = self._close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        return 100.0 - (100.0 / (1.0 + rs))

    def rsi_signal(self) -> str:
        r = self.rsi()
        if pd.isna(r.iloc[-1]):
            return "neutral"
        if r.iloc[-1] < 30:
            return "oversold"
        if r.iloc[-1] > 70:
            return "overbought"
        return "neutral"

    # ------------------------------------------------------------------
    # MACD (cached)
    # ------------------------------------------------------------------

    def macd(self) -> tuple[pd.Series, pd.Series, pd.Series]:
        if self._macd_cache is None:
            ema_fast = self.ema(CONFIG.macd_fast)
            ema_slow = self.ema(CONFIG.macd_slow)
            dif = ema_fast - ema_slow
            dea = dif.ewm(span=CONFIG.macd_signal, adjust=False).mean()
            macd_hist = 2 * (dif - dea)
            self._macd_cache = (dif, dea, macd_hist)
        return self._macd_cache

    def macd_golden_cross(self) -> bool:
        dif, dea, _ = self.macd()
        if len(dif) < 2 or pd.isna(dif.iloc[-1]):
            return False
        return dif.iloc[-2] <= dea.iloc[-2] and dif.iloc[-1] > dea.iloc[-1]

    def macd_death_cross(self) -> bool:
        dif, dea, _ = self.macd()
        if len(dif) < 2 or pd.isna(dif.iloc[-1]):
            return False
        return dif.iloc[-2] >= dea.iloc[-2] and dif.iloc[-1] < dea.iloc[-1]

    def macd_above_zero(self) -> bool:
        dif, _, _ = self.macd()
        return not pd.isna(dif.iloc[-1]) and dif.iloc[-1] > 0

    # ------------------------------------------------------------------
    # KDJ (cached)
    # ------------------------------------------------------------------

    def kdj(self) -> tuple[pd.Series, pd.Series, pd.Series]:
        if self._kdj_cache is None:
            period = CONFIG.kdj_period
            low_min = self._low.rolling(window=period).min()
            high_max = self._high.rolling(window=period).max()
            denom = (high_max - low_min).replace(0, 1e-10)
            rsv = (self._close - low_min) / denom * 100
            k = rsv.ewm(com=2, adjust=False).mean()
            d = k.ewm(com=2, adjust=False).mean()
            j = 3 * k - 2 * d
            self._kdj_cache = (k, d, j)
        return self._kdj_cache

    def kdj_golden_cross(self) -> bool:
        k, d, _ = self.kdj()
        if len(k) < 2 or pd.isna(k.iloc[-1]):
            return False
        return k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1]

    def kdj_death_cross(self) -> bool:
        k, d, _ = self.kdj()
        if len(k) < 2 or pd.isna(k.iloc[-1]):
            return False
        return k.iloc[-2] >= d.iloc[-2] and k.iloc[-1] < d.iloc[-1]

    def kdj_oversold(self) -> bool:
        _, _, j = self.kdj()
        return not pd.isna(j.iloc[-1]) and j.iloc[-1] < 0

    def kdj_overbought(self) -> bool:
        _, _, j = self.kdj()
        return not pd.isna(j.iloc[-1]) and j.iloc[-1] > 100

    # ------------------------------------------------------------------
    # Bollinger (cached)
    # ------------------------------------------------------------------

    def bollinger(self) -> tuple[pd.Series, pd.Series, pd.Series]:
        if self._boll_cache is None:
            period = CONFIG.boll_period
            mid = self.ma(period)
            std = self._close.rolling(window=period).std()
            upper = mid + CONFIG.boll_std * std
            lower = mid - CONFIG.boll_std * std
            self._boll_cache = (upper, mid, lower)
        return self._boll_cache

    def bollinger_position(self) -> str:
        upper, mid, lower = self.bollinger()
        c = self._close.iloc[-1]
        if pd.isna(upper.iloc[-1]):
            return "middle"
        if c > upper.iloc[-1]:
            return "above"
        if c < lower.iloc[-1]:
            return "below"
        return "middle"

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------

    def volume_ratio(self) -> float:
        vol_ma = self._volume.rolling(window=CONFIG.volume_ma_period).mean()
        if pd.isna(vol_ma.iloc[-1]) or vol_ma.iloc[-1] == 0:
            return 1.0
        return float(self._volume.iloc[-1] / vol_ma.iloc[-1])

    def volume_expanding(self) -> bool:
        return self.volume_ratio() > 1.5

    def volume_shrinking(self) -> bool:
        return self.volume_ratio() < 0.5

    # ------------------------------------------------------------------
    # Momentum
    # ------------------------------------------------------------------

    def roc(self, period: int = 10) -> pd.Series:
        """Rate of Change - 价格变化率（%）。"""
        return self._close.pct_change(periods=period) * 100

    def roc_signal(self, period: int = 10) -> float:
        """最新ROC值。"""
        r = self.roc(period)
        return float(r.iloc[-1]) if not pd.isna(r.iloc[-1]) else 0.0

    def momentum_acceleration(self, short: int = 5, long: int = 20) -> float:
        """短周期ROC - 长周期ROC，正值表示动量加速。"""
        s = self.roc(short)
        l = self.roc(long)
        if pd.isna(s.iloc[-1]) or pd.isna(l.iloc[-1]):
            return 0.0
        return float(s.iloc[-1] - l.iloc[-1])

    def new_high(self, days: int = 20) -> bool:
        """价格是否创N日新高。"""
        if len(self._close) < days:
            return False
        return float(self._close.iloc[-1]) >= float(self._close.iloc[-days:].max())

    def new_low(self, days: int = 20) -> bool:
        """价格是否创N日新低。"""
        if len(self._close) < days:
            return False
        return float(self._close.iloc[-1]) <= float(self._close.iloc[-days:].min())

    def adx(self, period: int = 14) -> float:
        """ADX 趋势强度指标。ADX > 25 表示强趋势。"""
        if len(self._close) < period * 2:
            return 0.0
        high = self._high
        low = self._low
        close = self._close
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)
        adx_val = dx.ewm(span=period, adjust=False).mean()
        return float(adx_val.iloc[-1]) if not pd.isna(adx_val.iloc[-1]) else 0.0

    # ------------------------------------------------------------------
    # ATR (Average True Range)
    # ------------------------------------------------------------------

    def atr(self, period: int = 14) -> pd.Series:
        """ATR — 平均真实波幅。"""
        tr = pd.concat([
            self._high - self._low,
            (self._high - self._close.shift(1)).abs(),
            (self._low - self._close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    def atr_pct(self, period: int = 14) -> float:
        """ATR 占收盘价百分比（波动率代理）。"""
        a = self.atr(period)
        if pd.isna(a.iloc[-1]) or self._close.iloc[-1] == 0:
            return 0.0
        return float(a.iloc[-1] / self._close.iloc[-1] * 100)

    # ------------------------------------------------------------------
    # OBV (On-Balance Volume)
    # ------------------------------------------------------------------

    def obv(self) -> pd.Series:
        """OBV — 能量潮指标。"""
        direction = self._close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        return (self._volume * direction).cumsum()

    def obv_slope(self, n: int = 10) -> float:
        """OBV 的 N 日线性回归斜率（归一化）。"""
        o = self.obv()
        if len(o) < n:
            return 0.0
        recent = o.iloc[-n:].values
        x = range(n)
        slope = (n * sum(i * v for i, v in enumerate(recent)) - sum(x) * sum(recent)) / (n * sum(i**2 for i in x) - sum(x)**2)
        mean_vol = float(self._volume.iloc[-n:].mean())
        if mean_vol == 0:
            return 0.0
        return slope / mean_vol * 100

    # ------------------------------------------------------------------
    # VWAP (Volume-Weighted Average Price)
    # ------------------------------------------------------------------

    def vwap(self, n: int = 20) -> pd.Series:
        """N 日滚动 VWAP。"""
        typical = (self._high + self._low + self._close) / 3
        tv = typical * self._volume
        return tv.rolling(n).sum() / self._volume.rolling(n).sum().replace(0, 1e-10)

    def vwap_deviation(self, n: int = 20) -> float:
        """收盘价相对 VWAP 的偏离百分比。"""
        v = self.vwap(n)
        if pd.isna(v.iloc[-1]) or v.iloc[-1] == 0:
            return 0.0
        return float((self._close.iloc[-1] - v.iloc[-1]) / v.iloc[-1] * 100)

    # ------------------------------------------------------------------
    # 收益偏度
    # ------------------------------------------------------------------

    def return_skew(self, n: int = 20) -> float:
        """N 日收益序列的偏度。正偏 = 右尾厚。"""
        ret = self._close.pct_change().iloc[-n:]
        if len(ret) < 3 or ret.std() == 0:
            return 0.0
        return float(ret.skew())

    # ------------------------------------------------------------------
    # 动量持久度
    # ------------------------------------------------------------------

    def momentum_persistence(self, n: int = 20) -> float:
        """近 N 日中正收益天数占比（0-1）。"""
        ret = self._close.pct_change().iloc[-n:]
        if len(ret) == 0:
            return 0.5
        return float((ret > 0).sum() / len(ret))

    # ------------------------------------------------------------------
    # 上涨/下跌成交量比
    # ------------------------------------------------------------------

    def up_down_volume_ratio(self, n: int = 20) -> float:
        """上涨日均成交量 / 下跌日均成交量。>1 = 上涨时量大。"""
        ret = self._close.pct_change().iloc[-n:]
        vol = self._volume.iloc[-n:]
        up_vol = vol[ret > 0].mean() if (ret > 0).any() else 0
        down_vol = vol[ret < 0].mean() if (ret < 0).any() else 0
        if down_vol == 0:
            return 2.0 if up_vol > 0 else 1.0
        return float(up_vol / down_vol)

    # ------------------------------------------------------------------
    # 最大回撤及恢复
    # ------------------------------------------------------------------

    def max_drawdown(self, n: int = 60) -> float:
        """近 N 日最大回撤百分比（0-100）。"""
        c = self._close.iloc[-n:]
        if len(c) < 2:
            return 0.0
        peak = c.cummax()
        dd = (c - peak) / peak * 100
        return float(dd.min())

    def drawdown_recovery(self, n: int = 60) -> float:
        """从 N 日内最大回撤低点的反弹幅度（%）。"""
        c = self._close.iloc[-n:]
        if len(c) < 2:
            return 0.0
        peak = c.cummax()
        dd = (c - peak) / peak
        min_idx = dd.idxmin()
        min_val = c.loc[min_idx]
        current = c.iloc[-1]
        if min_val == 0:
            return 0.0
        return float((current - min_val) / min_val * 100)

    # ------------------------------------------------------------------
    # 新高新低计数
    # ------------------------------------------------------------------

    def high_low_count(self, n: int = 60) -> tuple[int, int]:
        """近 N 日新高/新低次数。"""
        c = self._close.iloc[-n:]
        if len(c) < 2:
            return 0, 0
        highs = sum(1 for i in range(1, len(c)) if c.iloc[i] >= c.iloc[:i].max())
        lows = sum(1 for i in range(1, len(c)) if c.iloc[i] <= c.iloc[:i].min())
        return highs, lows

    # ------------------------------------------------------------------
    # Full indicator DataFrame (always uses the original, unsliced data)
    # ------------------------------------------------------------------

    def add_all_indicators(self) -> pd.DataFrame:
        df = self._df_original.copy()
        close = pd.to_numeric(df["close"], errors="coerce")
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        volume = pd.to_numeric(df["volume"], errors="coerce")

        df["ma5"] = close.rolling(window=5).mean()
        df["ma10"] = close.rolling(window=10).mean()
        df["ma20"] = close.rolling(window=20).mean()
        df["ma60"] = close.rolling(window=60).mean()
        df["rsi14"] = close.diff().pipe(
            lambda d: 100.0 - 100.0 / (1.0 + d.where(d > 0, 0.0).ewm(com=13, min_periods=14).mean()
                                        / (-d).where(d < 0, 0.0).ewm(com=13, min_periods=14).mean().replace(0, 1e-10))
        )
        ema_fast = close.ewm(span=CONFIG.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=CONFIG.macd_slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=CONFIG.macd_signal, adjust=False).mean()
        df["macd_dif"] = dif
        df["macd_dea"] = dea
        df["macd_hist"] = 2 * (dif - dea)

        period = CONFIG.kdj_period
        low_min = low.rolling(window=period).min()
        high_max = high.rolling(window=period).max()
        rsv = (close - low_min) / (high_max - low_min).replace(0, 1e-10) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        df["kdj_k"] = k
        df["kdj_d"] = d
        df["kdj_j"] = 3 * k - 2 * d

        boll_mid = close.rolling(window=CONFIG.boll_period).mean()
        boll_std = close.rolling(window=CONFIG.boll_period).std()
        df["boll_upper"] = boll_mid + CONFIG.boll_std * boll_std
        df["boll_mid"] = boll_mid
        df["boll_lower"] = boll_mid - CONFIG.boll_std * boll_std
        df["vol_ratio"] = volume / volume.rolling(window=CONFIG.volume_ma_period).mean()
        return df


TechInd = TechnicalIndicators
