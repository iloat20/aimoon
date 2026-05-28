"""Technical indicators calculation module"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aimoon.config import CONFIG


class TechnicalIndicators:
    """Calculate technical indicators for K-line data"""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()
        self._close = pd.to_numeric(df["close"], errors="coerce")
        self._high = pd.to_numeric(df["high"], errors="coerce")
        self._low = pd.to_numeric(df["low"], errors="coerce")
        self._volume = pd.to_numeric(df["volume"], errors="coerce")

    def ma(self, period: int) -> pd.Series:
        return self._close.rolling(window=period).mean()

    def ema(self, period: int) -> pd.Series:
        return self._close.ewm(span=period, adjust=False).mean()

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

    def rsi(self, period: int | None = None) -> pd.Series:
        period = period or CONFIG.rsi_period
        delta = self._close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss
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

    def macd(self):
        ema_fast = self.ema(CONFIG.macd_fast)
        ema_slow = self.ema(CONFIG.macd_slow)
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=CONFIG.macd_signal, adjust=False).mean()
        macd_hist = 2 * (dif - dea)
        return dif, dea, macd_hist

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
    def kdj(self):
        period = CONFIG.kdj_period
        low_min = self._low.rolling(window=period).min()
        high_max = self._high.rolling(window=period).max()
        rsv = (self._close - low_min) / (high_max - low_min) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        j = 3 * k - 2 * d
        return k, d, j

    def kdj_golden_cross(self) -> bool:
        k, d, _ = self.kdj()
        if len(k) < 2 or pd.isna(k.iloc[-1]):
            return False
        return k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1]

    def kdj_oversold(self) -> bool:
        _, _, j = self.kdj()
        return not pd.isna(j.iloc[-1]) and j.iloc[-1] < 0

    def kdj_overbought(self) -> bool:
        _, _, j = self.kdj()
        return not pd.isna(j.iloc[-1]) and j.iloc[-1] > 100
    def bollinger(self):
        period = CONFIG.boll_period
        mid = self.ma(period)
        std = self._close.rolling(window=period).std()
        upper = mid + CONFIG.boll_std * std
        lower = mid - CONFIG.boll_std * std
        return upper, mid, lower

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

    def volume_ratio(self) -> float:
        vol_ma = self._volume.rolling(window=CONFIG.volume_ma_period).mean()
        if pd.isna(vol_ma.iloc[-1]) or vol_ma.iloc[-1] == 0:
            return 1.0
        return float(self._volume.iloc[-1] / vol_ma.iloc[-1])

    def volume_expanding(self) -> bool:
        return self.volume_ratio() > 1.5

    def volume_shrinking(self) -> bool:
        return self.volume_ratio() < 0.5

    def add_all_indicators(self) -> pd.DataFrame:
        df = self.df
        df["ma5"] = self.ma(5)
        df["ma10"] = self.ma(10)
        df["ma20"] = self.ma(20)
        df["ma60"] = self.ma(60)
        df["rsi14"] = self.rsi(14)
        dif, dea, hist = self.macd()
        df["macd_dif"] = dif
        df["macd_dea"] = dea
        df["macd_hist"] = hist
        k, d, j = self.kdj()
        df["kdj_k"] = k
        df["kdj_d"] = d
        df["kdj_j"] = j
        upper, mid, lower = self.bollinger()
        df["boll_upper"] = upper
        df["boll_mid"] = mid
        df["boll_lower"] = lower
        df["vol_ratio"] = self._volume / self._volume.rolling(window=CONFIG.volume_ma_period).mean()
        return df