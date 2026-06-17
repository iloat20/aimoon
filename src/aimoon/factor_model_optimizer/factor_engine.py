"""因子引擎 — 可参数化因子生成，严格防止未来信息泄露。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FactorDefinition:
    """单个因子的参数化定义。"""

    name: str
    category: str  # price / volatility / volume / composite
    params: dict[str, Any]
    compute_fn: Callable[..., pd.DataFrame] = field(compare=False)

    def with_params(self, **overrides: Any) -> FactorDefinition:
        """返回更新了参数的新定义。"""
        merged = {**self.params, **overrides}
        return FactorDefinition(
            name=self.name,
            category=self.category,
            params=merged,
            compute_fn=self.compute_fn,
        )


def _safe_series(arr: np.ndarray) -> np.ndarray:
    """将 inf 替换为 NaN。"""
    return np.where(np.isinf(arr), np.nan, arr)


def _rolling_rank(series: pd.Series, window: int) -> pd.Series:
    """滚动排名：当前值在过去 window 天中的百分位。"""

    def _rank_last(x: np.ndarray) -> float:
        if np.isnan(x).all():
            return np.nan
        last = x[-1]
        if np.isnan(last):
            return np.nan
        valid = x[~np.isnan(x)]
        if len(valid) == 0:
            return np.nan
        less = (valid < last).sum()
        eq = (valid == last).sum()
        return (less + 0.5 * (eq + 1)) / len(valid)

    return series.rolling(window=window, min_periods=window).apply(_rank_last, raw=True)


# ── 价格类因子 ──────────────────────────────────────────────────────────────


def _momentum(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """过去 window 日收益率。"""
    return close.pct_change(periods=window)


def _ma_distance(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """价格 / 均线 - 1。"""
    ma = close.rolling(window=window, min_periods=window).mean()
    result = close / ma - 1.0
    return result.replace([np.inf, -np.inf], np.nan)


def _bollinger_position(close: pd.DataFrame, window: int, num_std: float = 2.0) -> pd.DataFrame:
    """布林带位置：(close - mid) / (num_std * std)。"""
    mid = close.rolling(window=window, min_periods=window).mean()
    std = close.rolling(window=window, min_periods=window).std(ddof=1)
    band = num_std * std
    result = (close - mid) / band
    return result.replace([np.inf, -np.inf], np.nan)


def _return_skew(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """过去 window 日收益率的偏度。"""
    ret = close.pct_change()
    skew = ret.rolling(window=window, min_periods=window).skew()
    return skew


def _return_kurt(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """过去 window 日收益率的峰度。"""
    ret = close.pct_change()
    kurt = ret.rolling(window=window, min_periods=window).kurt()
    return kurt


# ── 波动类因子 ──────────────────────────────────────────────────────────────


def _atr(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, window: int) -> pd.DataFrame:
    """ATR / close 归一化。"""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(window=window, min_periods=window).mean()
    result = atr / close
    return result.replace([np.inf, -np.inf], np.nan)


def _historical_volatility(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """历史波动率（年化）：std(日收益率) * sqrt(252)。"""
    ret = close.pct_change()
    vol = ret.rolling(window=window, min_periods=window).std(ddof=1) * np.sqrt(252)
    return vol


def _hl_spread(
    high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, window: int
) -> pd.DataFrame:
    """过去 window 日均 (high - low) / close。"""
    spread = (high - low) / close.replace(0, np.nan)
    avg_spread = spread.rolling(window=window, min_periods=window).mean()
    return avg_spread.replace([np.inf, -np.inf], np.nan)


# ── 量价类因子 ──────────────────────────────────────────────────────────────


def _obv_slope(volume: pd.DataFrame, close: pd.DataFrame, window: int) -> pd.DataFrame:
    """OBV 的 window 日线性回归斜率（归一化）。"""
    # OBV: sign(close.diff()) * volume 的累积
    sign_ret = np.sign(close.diff())
    obv = (sign_ret * volume).cumsum()

    def _slope(arr: np.ndarray) -> float:
        if np.isnan(arr).all() or len(arr) < 2:
            return np.nan
        valid_mask = ~np.isnan(arr)
        valid = arr[valid_mask]
        if len(valid) < 2:
            return np.nan
        x = np.arange(len(valid), dtype=np.float64)
        x_mean = x.mean()
        y_mean = valid.mean()
        denom = ((x - x_mean) ** 2).sum()
        if denom == 0:
            return 0.0
        return float(((x - x_mean) * (valid - y_mean)).sum() / denom)

    slope = obv.rolling(window=window, min_periods=window).apply(_slope, raw=True)
    # 归一化：除以 rolling mean of OBV abs
    scale = obv.abs().rolling(window=window, min_periods=window).mean()
    result = slope / scale.replace(0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


def _vwap_deviation(
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    window: int,
) -> pd.DataFrame:
    """VWAP 偏离度：close / VWAP - 1。"""
    typical = (high + low + close) / 3.0
    vol_safe = volume.replace(0, np.nan)
    vwap_num = (typical * vol_safe).rolling(window=window, min_periods=window).sum()
    vwap_den = vol_safe.rolling(window=window, min_periods=window).sum()
    vwap = vwap_num / vwap_den
    result = close / vwap - 1.0
    return result.replace([np.inf, -np.inf], np.nan)


def _volume_ratio(volume: pd.DataFrame, window: int) -> pd.DataFrame:
    """量比：当日成交量 / 过去 window 日平均成交量。"""
    avg_vol = volume.rolling(window=window, min_periods=window).mean()
    result = volume / avg_vol.replace(0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


def _amount_ma_ratio(amount: pd.DataFrame, window: int) -> pd.DataFrame:
    """成交额比：当日成交额 / 过去 window 日平均成交额。"""
    avg_amount = amount.rolling(window=window, min_periods=window).mean()
    result = amount / avg_amount.replace(0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


# ── 复合因子 ────────────────────────────────────────────────────────────────


def _ma_crossover(close: pd.DataFrame, fast_window: int, slow_window: int) -> pd.DataFrame:
    """均线交叉因子：ma_fast / ma_slow - 1。"""
    ma_fast = close.rolling(window=fast_window, min_periods=fast_window).mean()
    ma_slow = close.rolling(window=slow_window, min_periods=slow_window).mean()
    result = ma_fast / ma_slow.replace(0, np.nan) - 1.0
    return result.replace([np.inf, -np.inf], np.nan)


def _rsi_volume_combo(
    close: pd.DataFrame, volume: pd.DataFrame, rsi_window: int = 14, vol_window: int = 20
) -> pd.DataFrame:
    """RSI 与成交量变化的组合因子。"""
    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(window=rsi_window, min_periods=rsi_window).mean()
    avg_loss = loss.rolling(window=rsi_window, min_periods=rsi_window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)

    # 成交量变化率
    vol_change = volume.pct_change(periods=vol_window)

    # 组合：RSI 偏离 50 的方向 × 成交量变化
    result = (rsi - 50.0) / 50.0 * vol_change
    return result.replace([np.inf, -np.inf], np.nan)


def _momentum_volatility_combo(
    close: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    mom_window: int = 20,
    vol_window: int = 20,
) -> pd.DataFrame:
    """动量 × 波动率调整因子。"""
    mom = close.pct_change(periods=mom_window)
    vol = close.pct_change().rolling(window=vol_window, min_periods=vol_window).std()
    result = mom / vol.replace(0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


# ── 因子注册表模板 ───────────────────────────────────────────────────────────


def _build_factor_templates() -> dict[str, FactorDefinition]:
    """返回所有预定义因子的模板（不含具体参数）。"""
    return {
        # 价格类
        "momentum": FactorDefinition(
            name="momentum",
            category="price",
            params={"window": 20},
            compute_fn=_momentum,
        ),
        "ma_distance": FactorDefinition(
            name="ma_distance",
            category="price",
            params={"window": 20},
            compute_fn=_ma_distance,
        ),
        "bollinger_position": FactorDefinition(
            name="bollinger_position",
            category="price",
            params={"window": 20, "num_std": 2.0},
            compute_fn=_bollinger_position,
        ),
        "return_skew": FactorDefinition(
            name="return_skew",
            category="price",
            params={"window": 20},
            compute_fn=_return_skew,
        ),
        "return_kurt": FactorDefinition(
            name="return_kurt",
            category="price",
            params={"window": 20},
            compute_fn=_return_kurt,
        ),
        # 波动类
        "atr": FactorDefinition(
            name="atr",
            category="volatility",
            params={"window": 14},
            compute_fn=_atr,
        ),
        "historical_volatility": FactorDefinition(
            name="historical_volatility",
            category="volatility",
            params={"window": 20},
            compute_fn=_historical_volatility,
        ),
        "hl_spread": FactorDefinition(
            name="hl_spread",
            category="volatility",
            params={"window": 10},
            compute_fn=_hl_spread,
        ),
        # 量价类
        "obv_slope": FactorDefinition(
            name="obv_slope",
            category="volume",
            params={"window": 20},
            compute_fn=_obv_slope,
        ),
        "vwap_deviation": FactorDefinition(
            name="vwap_deviation",
            category="volume",
            params={"window": 20},
            compute_fn=_vwap_deviation,
        ),
        "volume_ratio": FactorDefinition(
            name="volume_ratio",
            category="volume",
            params={"window": 20},
            compute_fn=_volume_ratio,
        ),
        "amount_ma_ratio": FactorDefinition(
            name="amount_ma_ratio",
            category="volume",
            params={"window": 20},
            compute_fn=_amount_ma_ratio,
        ),
        # 复合类
        "ma_crossover": FactorDefinition(
            name="ma_crossover",
            category="composite",
            params={"fast_window": 5, "slow_window": 20},
            compute_fn=_ma_crossover,
        ),
        "rsi_volume_combo": FactorDefinition(
            name="rsi_volume_combo",
            category="composite",
            params={"rsi_window": 14, "vol_window": 20},
            compute_fn=_rsi_volume_combo,
        ),
        "momentum_volatility_combo": FactorDefinition(
            name="momentum_volatility_combo",
            category="composite",
            params={"mom_window": 20, "vol_window": 20},
            compute_fn=_momentum_volatility_combo,
        ),
    }


class FactorEngine:
    """因子计算引擎：管理参数化因子的生成与计算。"""

    def __init__(self) -> None:
        self._templates = _build_factor_templates()

    @property
    def templates(self) -> dict[str, FactorDefinition]:
        return self._templates

    def instantiate(self, name: str, **param_overrides: Any) -> FactorDefinition:
        """用具体参数实例化一个因子。"""
        if name not in self._templates:
            raise KeyError(f"Unknown factor: {name!r}. Available: {list(self._templates)}")
        return self._templates[name].with_params(**param_overrides)

    def compute_factor(
        self,
        factor_def: FactorDefinition,
        panel: dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """计算单个因子，返回 wide DataFrame (date x symbol)。"""
        fn = factor_def.compute_fn
        params = factor_def.params

        # 根据因子名分发所需面板数据
        name = factor_def.name
        close = panel["close"]
        high = panel.get("high")
        low = panel.get("low")
        volume = panel.get("volume")
        amount = panel.get("amount")

        if name in ("momentum", "return_skew", "return_kurt"):
            return fn(close, **params)
        elif name == "ma_distance":
            return fn(close, **params)
        elif name == "bollinger_position":
            return fn(close, **params)
        elif name == "atr":
            for k in ("high", "low", "close"):
                assert panel.get(k) is not None, f"Factor {name} requires {k}"
            return fn(high, low, close, **params)
        elif name == "historical_volatility":
            return fn(close, **params)
        elif name == "hl_spread":
            return fn(high, low, **params)
        elif name == "obv_slope":
            return fn(volume, close, **params)
        elif name == "vwap_deviation":
            return fn(high, low, close, volume, **params)
        elif name == "volume_ratio":
            return fn(volume, **params)
        elif name == "amount_ma_ratio":
            return fn(amount, **params)
        elif name == "ma_crossover":
            return fn(close, **params)
        elif name == "rsi_volume_combo":
            return fn(close, volume, **params)
        elif name == "momentum_volatility_combo":
            return fn(close, high, low, **params)
        else:
            raise ValueError(f"No dispatch for factor: {name}")

    def compute_all(
        self,
        factor_defs: list[FactorDefinition],
        panel: dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """计算多个因子，返回长表 (date, symbol, factor_name -> value)。"""
        frames: list[pd.DataFrame] = []
        for fd in factor_defs:
            try:
                result = self.compute_factor(fd, panel)
                # wide -> long
                long_df = result.stack(dropna=False).reset_index()
                long_df.columns = ["date", "symbol", fd.name]
                frames.append(long_df)
            except Exception as e:
                logger.warning("Factor %s compute failed: %s", fd.name, e)

        if not frames:
            return pd.DataFrame()

        merged = frames[0]
        for f in frames[1:]:
            merged = merged.merge(f, on=["date", "symbol"], how="outer")
        return merged.sort_values(["date", "symbol"]).reset_index(drop=True)


def compute_all_factors(
    panel: dict[str, pd.DataFrame],
    config: Any = None,
) -> tuple[pd.DataFrame, list[FactorDefinition]]:
    """便捷函数：用默认参数计算全部预定义因子。

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        包含 close/high/low/volume/amount 的面板数据。
    config : OptimizerConfig, optional
        配置对象。如果为 None 使用默认值。

    Returns
    -------
    tuple[pd.DataFrame, list[FactorDefinition]]
        长表因子数据 + 使用的因子定义列表。
    """
    from aimoon.factor_model_optimizer.config import OptimizerConfig

    cfg = config or OptimizerConfig()
    engine = FactorEngine()

    all_defs: list[FactorDefinition] = []
    for name, tmpl in engine.templates.items():
        # 从 config 获取默认参数
        if name == "momentum":
            for w in cfg.momentum_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name == "ma_distance":
            for w in cfg.ma_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name == "bollinger_position":
            for w in cfg.boll_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name in ("return_skew", "return_kurt"):
            for w in cfg.momentum_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name == "atr":
            for w in cfg.atr_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name == "historical_volatility":
            for w in cfg.vol_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name == "hl_spread":
            for w in cfg.atr_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name == "obv_slope":
            for w in cfg.obv_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name == "vwap_deviation":
            for w in cfg.ma_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name == "volume_ratio":
            for w in cfg.vol_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name == "amount_ma_ratio":
            for w in cfg.ma_windows:
                all_defs.append(engine.instantiate(name, window=w))
        elif name == "ma_crossover":
            for fast in (5, 10):
                for slow in (20, 60):
                    if fast < slow:
                        all_defs.append(
                            engine.instantiate(name, fast_window=fast, slow_window=slow)
                        )
        elif name == "rsi_volume_combo":
            for rw in cfg.rsi_windows:
                for vw in cfg.vol_windows:
                    all_defs.append(engine.instantiate(name, rsi_window=rw, vol_window=vw))
        elif name == "momentum_volatility_combo":
            for mw in cfg.momentum_windows:
                for vw in cfg.vol_windows:
                    all_defs.append(engine.instantiate(name, mom_window=mw, vol_window=vw))
        else:
            all_defs.append(tmpl)

    t0 = time.time()
    df = engine.compute_all(all_defs, panel)
    elapsed = time.time() - t0
    logger.info(
        "Computed %d factors: %d rows x %d cols, %.1fs",
        len(all_defs),
        len(df),
        df.shape[1] - 2,
        elapsed,
    )
    return df, all_defs
