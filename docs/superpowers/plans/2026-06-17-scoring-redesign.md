# 评分系统重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把评分系统重写为"ML 百分位即最终分数"——11 个手写 A 股因子 + 单 LightGBM（2 折 Purged TSCV, n_dates=120）+ ML 分数驱动回测单引擎，激进删除技术信号模块/452 Alpha Zoo 因子/集成 stacking/Optuna/增量学习。

**Architecture:** 新 `factors/ashare.py` 提供 11 个稳定 A 股因子（panel 宽表时序）；精简 `ml/feature_pipeline.py` 拼接因子 z-score + 6 基础技术统计（~18 特征）；`ml/trainer.py` 单 LightGBM 训练；`ml/predictor.py` 输出截面百分位 0-100；`screener.py` 令 `total_score = ml_score`；新 `backtest.py` 预计算全区间分数后单循环回测。

**Tech Stack:** Python 3.13, LightGBM, pandas/numpy, pytest, ruff/black/mypy。

**Spec:** `docs/superpowers/specs/2026-06-17-scoring-redesign-design.md`

---

## 文件结构

**新建**
- `src/aimoon/factors/ashare.py` — `build_panel()`（从旧 panel.py 迁入精简）+ `robust_zscore()` + `compute_ashare_factors()` + `ASHARE_FACTORS` 常量
- `src/aimoon/backtest.py` — `precompute_scores()` + `run_backtest()` + `BacktestResult`
- `src/aimoon/ml/predictor.py` — `MLPredictor`（替代 EnsemblePredictor）

**重写/精简**
- `src/aimoon/ml/feature_pipeline.py` — 仅 `extract_features()`（删 ICIR/中性化/SVD/Alpha360/Robust/PCA/KMeans）
- `src/aimoon/ml/training_loop.py` — `train_lightgbm_cv()`（2 折，删 Optuna/早停复杂逻辑）
- `src/aimoon/ml/trainer.py` — 仅 `train_model()` 单 LightGBM（删 ensemble/dual）
- `src/aimoon/ml/optimized_config.py` — 仅 LightGBM 参数 + n_dates=120
- `src/aimoon/ml/data_collection.py` — 精简日期选择
- `src/aimoon/screener.py` — `screen_stock()` ML-only
- `src/aimoon/models.py` — `ScoredStock` 简化
- `src/aimoon/output.py` — ML 分数展示
- `src/aimoon/cli.py` / `config.py` — 删开关，接新引擎
- `src/aimoon/scoring/__init__.py` — 薄封装

**删除**（Phase 7 统一处理）：`scoring/` 下 ~20 个技术信号模块、`factors/zoo/` 全部 + registry/panel/dag/genetic/incremental/quality/weighting/scorer、`ml/` 中 alpha360/alpha360_robust/stacking/meta_ensemble/hyperopt/incremental_trainer/icir_weighter/factor_decay/factor_quality/factor_importance/covariance_estimator/feature_selector/slippage_model/walk_forward/ensemble/ensemble_signals/lgbm_trainer、顶层 regime_enhanced/rumi_*/adaptive_strategy/grid_search/optimizer/self_learning/factor_eval/factor_model_optimizer/、qf_backtest/、enhanced_backtest/（保留 metrics.py）

---

## 接口契约（全局一致，后续任务不得改名）

```python
# factors/ashare.py
ASHARE_FACTORS: list[str]  # 11 个 factor_id
def build_panel(klines: dict[str, pd.DataFrame], min_rows: int = 60) -> dict[str, pd.DataFrame] | None
def robust_zscore(df: pd.DataFrame, clip: float = 3.0) -> pd.DataFrame
def compute_ashare_factors(
    panel: dict[str, pd.DataFrame],
    fundamentals: pd.DataFrame | None = None,   # index=code, cols: pe, pb, dividend_yield
    sector_map: dict[str, str] | None = None,    # code -> 行业
) -> dict[str, pd.DataFrame]                     # factor_id -> wide DataFrame(日期×股票)

# ml/feature_pipeline.py
def extract_features(
    panel: dict[str, pd.DataFrame],
    target_date: pd.Timestamp | None = None,
    fundamentals: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    factor_series: dict[str, pd.DataFrame] | None = None,
    feature_medians: pd.Series | None = None,
) -> pd.DataFrame                                 # index=code, columns=features

# ml/predictor.py
@dataclass(frozen=True)
class MLPredictor:
    model: object
    feature_names: tuple[str, ...]
    feature_medians: pd.Series
    @classmethod
    def from_cache(cls, cache_dir: str | None) -> "MLPredictor | None"
    @property
    def has_model(self) -> bool
    def predict_percentile(self, panel, target_date, fundamentals=None, sector_map=None) -> dict[str, int]

# ml/trainer.py
@dataclass(frozen=True)
class TrainingResult:
    model: object
    feature_names: tuple[str, ...]
    feature_medians: pd.Series
    ic: float
    n_stocks: int
    n_dates: int
    train_duration: float
def train_model(panel, klines, *, n_dates=120, forward_days=5, save_dir, fundamentals=None, sector_map=None, force=False) -> TrainingResult

# backtest.py
@dataclass(frozen=True)
class BacktestResult:
    total_return: float; annual_return: float; sharpe: float; max_drawdown: float
    win_rate: float; profit_factor: float; trade_count: int; avg_hold_days: float
    trades: tuple[dict, ...]; equity_curve: pd.Series
def precompute_scores(panel, predictor, fundamentals, sector_map, dates) -> dict[pd.Timestamp, dict[str, int]]
def run_backtest(panel, klines, scores, cfg) -> BacktestResult
```

---

## Phase 1 — 因子模块 `factors/ashare.py`

### Task 1: `build_panel` 迁入 ashare.py

**Files:**
- Create: `src/aimoon/factors/ashare.py`
- Test: `tests/test_ashare_panel.py`

- [ ] **Step 1: 写失败测试**

```python
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
    assert build_panel({"000001": _kline()}, min_rows=60) is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_ashare_panel.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError`

- [ ] **Step 3: 实现 build_panel**

```python
# src/aimoon/factors/ashare.py
"""A 股因子模块 — 11 个手写稳定因子 + panel 构建。

替代旧的 factors/zoo（452 因子）+ registry/panel/dag 等。设计目标：
计算稳定（除零保护、无复杂链式算子）、向量化快、A 股文献验证。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_PANEL_COLUMNS = ("open", "high", "low", "close", "volume")
_OPTIONAL_COLUMNS = ("turnover", "amount", "northbound")


def build_panel(
    klines: dict[str, pd.DataFrame],
    min_rows: int = 60,
) -> dict[str, pd.DataFrame] | None:
    """将 {code: kline_df} 转为宽表 {field: DataFrame(日期×股票)}。"""
    if not klines:
        return None

    from aimoon.data.validator import fix_kline_dates

    klines = {code: fix_kline_dates(df) for code, df in klines.items()}

    valid_codes: list[str] = []
    for code, df in klines.items():
        if df is None or len(df) < min_rows:
            continue
        if any(c not in df.columns for c in _PANEL_COLUMNS):
            continue
        valid_codes.append(code)

    if len(valid_codes) < 2:
        logger.warning("Panel 需至少 2 只有效股票，仅 %d 只", len(valid_codes))
        return None

    dt_indices: dict[str, pd.DatetimeIndex] = {}
    for code in valid_codes:
        idx = klines[code].index
        dt_indices[code] = idx if isinstance(idx, pd.DatetimeIndex) else pd.to_datetime(idx)

    panel: dict[str, pd.DataFrame] = {}
    for col in _PANEL_COLUMNS + _OPTIONAL_COLUMNS:
        col_data: dict[str, pd.Series] = {}
        for code in valid_codes:
            df = klines[code]
            if col in df.columns:
                s = df[col].copy()
                s.index = dt_indices[code]
                col_data[code] = s
        if col_data:
            wide = pd.DataFrame(col_data).sort_index().ffill(limit=5)
            panel[col] = wide
    return panel
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_ashare_panel.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/factors/ashare.py tests/test_ashare_panel.py
git commit -m "feat(factors): add ashare.build_panel migrated from panel.py"
```

---

### Task 2: `robust_zscore` 截面稳健标准化

**Files:**
- Modify: `src/aimoon/factors/ashare.py`
- Test: `tests/test_ashare_zscore.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ashare_zscore.py
import numpy as np
import pandas as pd
from aimoon.factors.ashare import robust_zscore


def test_robust_zscore_centered():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 100.0]})  # 100 是离群
    z = robust_zscore(df.T, clip=3.0)  # 转宽：1 行 5 列
    arr = z.iloc[0].values
    assert abs(np.nanmedian(arr)) < 1e-9
    assert np.nanmax(arr) <= 3.0 + 1e-9
    assert np.nanmin(arr) >= -3.0 - 1e-9


def test_robust_zscore_constant_returns_zero():
    df = pd.DataFrame({"a": [5.0, 5.0, 5.0], "b": [5.0, 5.0, 5.0]})
    z = robust_zscore(df)
    assert (z == 0.0).all().all()


def test_robust_zscore_preserves_nan():
    df = pd.DataFrame({"a": [1.0, np.nan, 3.0], "b": [2.0, 4.0, 6.0]})
    z = robust_zscore(df)
    assert np.isnan(z.iloc[1, 0])
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ashare_zscore.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现**

在 `src/aimoon/factors/ashare.py` 追加：

```python
def robust_zscore(df: pd.DataFrame, clip: float = 3.0) -> pd.DataFrame:
    """截面稳健 z-score：每行 (val - median) / (1.4826 * MAD)，clip 到 ±clip。NaN 保留。"""
    median = df.median(axis=1, skipna=True)
    mad = (df.sub(median, axis=0)).abs().median(axis=1, skipna=True)
    scale = (1.4826 * mad).where(mad > 1e-10, np.nan)
    z = df.sub(median, axis=0).div(scale, axis=0)
    return z.clip(-clip, clip)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ashare_zscore.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/factors/ashare.py tests/test_ashare_zscore.py
git commit -m "feat(factors): add robust_zscore cross-sectional standardizer"
```

---

### Task 3: 价格类因子（反转/波动/动量）

**Files:**
- Modify: `src/aimoon/factors/ashare.py`
- Test: `tests/test_ashare_price_factors.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ashare_price_factors.py
import numpy as np
import pandas as pd
from aimoon.factors.ashare import compute_ashare_factors


def _panel(prices):
    # prices: dict[code -> list]
    idx = pd.date_range("2024-01-01", periods=len(next(iter(prices.values()))), freq="D")
    close = pd.DataFrame({c: v for c, v in prices.items()}, index=idx)
    return {"close": close, "open": close, "high": close, "low": close,
            "volume": close * 1000, "turnover": close * 0 + 0.5, "amount": close * 1e6}


def test_rev_5d_negative_of_return():
    panel = _panel({"a": list(range(1, 71)), "b": list(range(70, 0, -1))})
    factors = compute_ashare_factors(panel)
    assert "rev_5d" in factors
    # a 上涨 → rev_5d 为负；b 下跌 → rev_5d 为正
    last = factors["rev_5d"].iloc[-1]
    assert last["a"] < 0
    assert last["b"] > 0


def test_vol_20d_is_negative_of_realized_vol():
    panel = _panel({"a": [10.0] * 70, "b": [10, 11] * 35})
    factors = compute_ashare_factors(panel)
    assert "vol_20d" in factors
    last = factors["vol_20d"].iloc[-1]
    # a 无波动 → vol_20d = 0（-0）；b 有波动 → vol_20d < 0
    assert last["a"] == 0.0
    assert last["b"] < 0


def test_mom_60d_positive_for_rising():
    panel = _panel({"a": list(range(1, 71)), "b": list(range(70, 0, -1))})
    factors = compute_ashare_factors(panel)
    last = factors["mom_60d"].iloc[-1]
    assert last["a"] > 0
    assert last["b"] < 0


def test_rev_20d_present():
    panel = _panel({"a": list(range(1, 71)), "b": list(range(70, 0, -1))})
    factors = compute_ashare_factors(panel)
    assert "rev_20d" in factors
    assert factors["rev_20d"].shape[0] == 70
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ashare_price_factors.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现价格因子 + 编排骨架**

在 `src/aimoon/factors/ashare.py` 追加：

```python
ASHARE_FACTORS: list[str] = [
    "rev_5d", "rev_20d", "turnover_20d", "vol_20d", "mom_60d",
    "amihud_20d", "ep", "bp", "div_yield", "northbound_chg_20d", "sector_mom_20d",
]


def _pct_change(panel: dict[str, pd.DataFrame], col: str, n: int) -> pd.DataFrame:
    return panel[col].pct_change(n)


def _rev(panel: dict[str, pd.DataFrame], n: int, fid: str) -> pd.DataFrame:
    """反转因子 = -1 * n 日收益率。"""
    return -_pct_change(panel, "close", n).rename(columns=lambda c: c)  # 保持列名


def _vol(panel: dict[str, pd.DataFrame], n: int) -> pd.DataFrame:
    """低波动因子 = -1 * n 日实现波动率（日收益 std）。"""
    rets = panel["close"].pct_change()
    return -rets.rolling(n, min_periods=n).std()


def _mom(panel: dict[str, pd.DataFrame], n: int) -> pd.DataFrame:
    """动量因子 = n 日收益率。"""
    return _pct_change(panel, "close", n)


def compute_ashare_factors(
    panel: dict[str, pd.DataFrame],
    fundamentals: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
) -> dict[str, pd.DataFrame]:
    """计算 11 个 A 股因子，返回 {factor_id: wide DataFrame(日期×股票)}。

    缺失数据因子返回全 NaN DataFrame（由下游截面中位数填充）。
    """
    close = panel.get("close")
    if close is None:
        raise ValueError("panel 缺少 close")

    factors: dict[str, pd.DataFrame] = {}
    factors["rev_5d"] = -_pct_change(panel, "close", 5)
    factors["rev_20d"] = -_pct_change(panel, "close", 20)
    factors["vol_20d"] = _vol(panel, 20)
    factors["mom_60d"] = _mom(panel, 60)
    # turnover_20d / amihud_20d / ep / bp / div_yield / northbound_chg_20d / sector_mom_20d
    # 在后续 Task 4/5/6 填充
    for fid in ("turnover_20d", "amihud_20d", "ep", "bp",
                "div_yield", "northbound_chg_20d", "sector_mom_20d"):
        factors[fid] = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    return factors
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ashare_price_factors.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/factors/ashare.py tests/test_ashare_price_factors.py
git commit -m "feat(factors): add price factors rev_5d/rev_20d/vol_20d/mom_60d + orchestrator skeleton"
```

---

### Task 4: 换手率与 Amihud 非流动性因子

**Files:**
- Modify: `src/aimoon/factors/ashare.py`
- Test: `tests/test_ashare_liq_factors.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ashare_liq_factors.py
import numpy as np
import pandas as pd
from aimoon.factors.ashare import compute_ashare_factors


def _panel(turnover_a, turnover_b):
    idx = pd.date_range("2024-01-01", periods=70, freq="D")
    close = pd.DataFrame({"a": np.linspace(10, 11, 70), "b": np.linspace(10, 11, 70)}, index=idx)
    turnover = pd.DataFrame({"a": [turnover_a] * 70, "b": [turnover_b] * 70}, index=idx)
    amount = pd.DataFrame({"a": [1e6] * 70, "b": [1e6] * 70}, index=idx)
    return {"close": close, "open": close, "high": close, "low": close,
            "volume": close * 1000, "turnover": turnover, "amount": amount}


def test_turnover_20d_negative_high_turnover_lower():
    panel = _panel(5.0, 1.0)  # a 高换手
    factors = compute_ashare_factors(panel)
    last = factors["turnover_20d"].iloc[-1]
    assert last["a"] < last["b"]  # 高换手 → 低分


def test_amihud_20d_higher_for_less_liquid():
    # a 成交额小（流动性差）→ amihud 高
    idx = pd.date_range("2024-01-01", periods=70, freq="D")
    close = pd.DataFrame({"a": [10, 11] * 35, "b": [10, 11] * 35}, index=idx)
    amount = pd.DataFrame({"a": [1e4] * 70, "b": [1e8] * 70}, index=idx)
    panel = {"close": close, "open": close, "high": close, "low": close,
             "volume": close * 1000, "turnover": close * 0 + 1.0, "amount": amount}
    factors = compute_ashare_factors(panel)
    last = factors["amihud_20d"].iloc[-1]
    assert last["a"] > last["b"]


def test_amihud_zero_amount_returns_nan():
    idx = pd.date_range("2024-01-01", periods=70, freq="D")
    close = pd.DataFrame({"a": np.linspace(10, 11, 70), "b": np.linspace(10, 11, 70)}, index=idx)
    amount = pd.DataFrame(0.0, index=idx, columns=["a", "b"])
    panel = {"close": close, "open": close, "high": close, "low": close,
             "volume": close * 1000, "turnover": close * 0 + 1.0, "amount": amount}
    factors = compute_ashare_factors(panel)
    assert factors["amihud_20d"].iloc[-1].isna().all()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ashare_liq_factors.py -v`
Expected: FAIL — turnover_20d/amihud_20d 全 NaN（占位）

- [ ] **Step 3: 实现两个因子**

在 `compute_ashare_factors` 中替换 turnover_20d / amihud_20d 的占位：

```python
    # turnover_20d: 高换手 → 低未来收益，取负
    if "turnover" in panel:
        factors["turnover_20d"] = -panel["turnover"].rolling(20, min_periods=20).mean()

    # amihud_20d: 非流动性 = mean(|日收益| / 成交额)，成交额 0 → NaN
    if "amount" in panel:
        rets = panel["close"].pct_change()
        amount = panel["amount"]
        denom = amount.where(amount > 1e-10, np.nan)
        illiq_daily = rets.abs() / denom
        factors["amihud_20d"] = illiq_daily.rolling(20, min_periods=20).mean()
```

并从占位循环里移除 `"turnover_20d"` 和 `"amihud_20d"`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ashare_liq_factors.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/factors/ashare.py tests/test_ashare_liq_factors.py
git commit -m "feat(factors): add turnover_20d and amihud_20d liquidity factors"
```

---

### Task 5: 基本面因子（EP / BP / 股息率）

**Files:**
- Modify: `src/aimoon/factors/ashare.py`
- Test: `tests/test_ashare_fundamental_factors.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ashare_fundamental_factors.py
import numpy as np
import pandas as pd
from aimoon.factors.ashare import compute_ashare_factors


def _panel():
    idx = pd.date_range("2024-01-01", periods=70, freq="D")
    close = pd.DataFrame({"a": np.linspace(10, 11, 70), "b": np.linspace(10, 11, 70)}, index=idx)
    return {"close": close, "open": close, "high": close, "low": close,
            "volume": close * 1000, "turnover": close * 0 + 1.0, "amount": close * 1e6}


def test_ep_bp_div_yield_from_fundamentals():
    panel = _panel()
    fund = pd.DataFrame(
        {"pe": [10.0, 50.0], "pb": [1.0, 5.0], "dividend_yield": [5.0, 1.0]},
        index=["a", "b"],
    )
    factors = compute_ashare_factors(panel, fundamentals=fund)
    # a 便宜（PE=10 → EP=0.1 高）→ ep 高
    last_ep = factors["ep"].iloc[-1]
    assert last_ep["a"] > last_ep["b"]
    last_bp = factors["bp"].iloc[-1]
    assert last_bp["a"] > last_bp["b"]
    last_div = factors["div_yield"].iloc[-1]
    assert last_div["a"] > last_div["b"]


def test_fundamentals_none_yields_nan():
    factors = compute_ashare_factors(_panel())
    assert factors["ep"].isna().all().all()
    assert factors["bp"].isna().all().all()
    assert factors["div_yield"].isna().all().all()


def test_ep_zero_pe_returns_nan():
    panel = _panel()
    fund = pd.DataFrame({"pe": [0.0, 10.0], "pb": [1.0, 1.0], "dividend_yield": [1.0, 1.0]},
                        index=["a", "b"])
    factors = compute_ashare_factors(panel, fundamentals=fund)
    assert np.isnan(factors["ep"].iloc[-1]["a"])
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ashare_fundamental_factors.py -v`
Expected: FAIL — ep/bp/div_yield 全 NaN（占位）

- [ ] **Step 3: 实现**

在 `compute_ashare_factors` 中替换 ep / bp / div_yield 占位：

```python
    # 基本面因子：静态值广播到所有日期，再由下游截面 z-score
    if fundamentals is not None:
        codes = close.columns
        for fid, col, invert_field in [
            ("ep", "pe", "pe"), ("bp", "pb", "pb"), ("div_yield", "dividend_yield", "dividend_yield"),
        ]:
            if col not in fundamentals.columns:
                continue
            val = fundamentals[col].reindex(codes)
            # EP = 1/PE, BP = 1/PB, div_yield 直接用；分母 0/缺失 → NaN
            if fid in ("ep", "bp"):
                val = val.where(val.abs() > 1e-10, np.nan)
                val = 1.0 / val
            factors[fid] = pd.DataFrame(
                np.tile(val.values, (len(close.index), 1)),
                index=close.index, columns=codes,
            )
```

并从占位循环里移除 `"ep"`、`"bp"`、`"div_yield"`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ashare_fundamental_factors.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/factors/ashare.py tests/test_ashare_fundamental_factors.py
git commit -m "feat(factors): add ep/bp/div_yield fundamental factors with zero-division guard"
```

---

### Task 6: 北向资金变化 + 板块动量因子

**Files:**
- Modify: `src/aimoon/factors/ashare.py`
- Test: `tests/test_ashare_exotic_factors.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ashare_exotic_factors.py
import numpy as np
import pandas as pd
from aimoon.factors.ashare import compute_ashare_factors


def _base_panel(extra=None):
    idx = pd.date_range("2024-01-01", periods=70, freq="D")
    close = pd.DataFrame({"a": np.linspace(10, 12, 70), "b": np.linspace(10, 11, 70)}, index=idx)
    panel = {"close": close, "open": close, "high": close, "low": close,
             "volume": close * 1000, "turnover": close * 0 + 1.0, "amount": close * 1e6}
    if extra:
        panel.update(extra)
    return panel


def test_northbound_chg_20d_positive_when_inflow():
    idx = pd.date_range("2024-01-01", periods=70, freq="D")
    nb = pd.DataFrame({"a": np.linspace(1e8, 2e8, 70), "b": np.linspace(1e8, 1e8, 70)}, index=idx)
    panel = _base_panel({"northbound": nb})
    factors = compute_ashare_factors(panel)
    last = factors["northbound_chg_20d"].iloc[-1]
    assert last["a"] > 0   # 流入
    assert last["b"] == 0.0  # 不变


def test_northbound_absent_returns_nan():
    factors = compute_ashare_factors(_base_panel())
    assert factors["northbound_chg_20d"].isna().all().all()


def test_sector_mom_20d_uses_sector_average():
    panel = _base_panel()
    sector_map = {"a": "tech", "b": "tech"}
    factors = compute_ashare_factors(panel, sector_map=sector_map)
    # a/b 同板块，sector_mom 相同
    last = factors["sector_mom_20d"].iloc[-1]
    assert abs(last["a"] - last["b"]) < 1e-9


def test_sector_mom_absent_returns_nan():
    factors = compute_ashare_factors(_base_panel())
    assert factors["sector_mom_20d"].isna().all().all()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ashare_exotic_factors.py -v`
Expected: FAIL — 两个因子全 NaN（占位）

- [ ] **Step 3: 实现**

在 `compute_ashare_factors` 中替换 northbound_chg_20d / sector_mom_20d 占位：

```python
    # northbound_chg_20d: 20 日北向持仓变化率；缺数据 → NaN
    if "northbound" in panel:
        nb = panel["northbound"]
        nb_prev = nb.shift(20)
        factors["northbound_chg_20d"] = (nb - nb_prev).where(
            nb_prev.abs() > 1e-10, np.nan
        ) / nb_prev.where(nb_prev.abs() > 1e-10, np.nan)

    # sector_mom_20d: 同板块 20 日平均动量（不含自身）
    if sector_map:
        mom20 = _mom(panel, 20)
        sec = pd.Series(sector_map)
        sector_mom = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
        for sector in sec.unique():
            members = sec[sec == sector].index.tolist()
            others = [m for m in members if m in mom20.columns]
            if len(others) < 1:
                continue
            # 板块均值（含自身也可，样本小）；用 expanding 截面均值
            sec_avg = mom20[others].mean(axis=1)
            for m in others:
                sector_mom[m] = sec_avg
        factors["sector_mom_20d"] = sector_mom
```

并从占位循环里移除 `"northbound_chg_20d"`、`"sector_mom_20d"`。占位循环此时应已无剩余项，删除该循环。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ashare_exotic_factors.py -v`
Expected: PASS

- [ ] **Step 5: 全因子模块回归**

Run: `pytest tests/test_ashare_*.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add src/aimoon/factors/ashare.py tests/test_ashare_exotic_factors.py
git commit -m "feat(factors): add northbound_chg_20d and sector_mom_20d factors; complete 11-factor module"
```

---

### Task 7: `factors/__init__.py` 导出

**Files:**
- Modify: `src/aimoon/factors/__init__.py`

- [ ] **Step 1: 重写导出**

```python
# src/aimoon/factors/__init__.py
"""因子模块 — 仅 A 股手写因子（替代旧 Alpha Zoo 452 因子）。"""
from aimoon.factors.ashare import (
    ASHARE_FACTORS,
    build_panel,
    compute_ashare_factors,
    robust_zscore,
)

__all__ = ["ASHARE_FACTORS", "build_panel", "compute_ashare_factors", "robust_zscore"]
```

- [ ] **Step 2: 验证导入**

Run: `python -c "from aimoon.factors import build_panel, compute_ashare_factors, ASHARE_FACTORS; print(len(ASHARE_FACTORS))"`
Expected: 输出 `11`

- [ ] **Step 3: 提交**

```bash
git add src/aimoon/factors/__init__.py
git commit -m "feat(factors): export ashare factor API"
```

---

## Phase 2 — 特征提取 + 预测器

### Task 8: 重写 `extract_features`

**Files:**
- Rewrite: `src/aimoon/ml/feature_pipeline.py`
- Test: `tests/test_ml_features_new.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ml_features_new.py
import numpy as np
import pandas as pd
from aimoon.ml.feature_pipeline import extract_features


def _panel():
    idx = pd.date_range("2024-01-01", periods=70, freq="D")
    close = pd.DataFrame({"a": np.linspace(10, 12, 70), "b": np.linspace(10, 9, 70)}, index=idx)
    return {"close": close, "open": close, "high": close, "low": close,
            "volume": close * 1000, "turnover": close * 0 + 1.0, "amount": close * 1e6}


def test_extract_features_shape_and_index():
    panel = _panel()
    feats = extract_features(panel, target_date=panel["close"].index[-1])
    assert set(feats.index) == {"a", "b"}
    # 11 因子 + 6 技术统计 = 17
    assert feats.shape[1] == 17
    assert not feats.isna().any().any()  # 中位数填充后无 NaN


def test_extract_features_train_inference_consistency():
    panel = _panel()
    target = panel["close"].index[-1]
    train_feats = extract_features(panel, target_date=target)
    medians = train_feats.median()
    inf_feats = extract_features(panel, target_date=target, feature_medians=medians)
    pd.testing.assert_frame_equal(train_feats, inf_feats)


def test_extract_features_fundamentals_merged():
    panel = _panel()
    fund = pd.DataFrame({"pe": [10.0, 50.0], "pb": [1.0, 5.0], "dividend_yield": [5.0, 1.0]},
                        index=["a", "b"])
    feats = extract_features(panel, target_date=panel["close"].index[-1], fundamentals=fund)
    assert "ep" in feats.columns
    assert "bp" in feats.columns
    assert "div_yield" in feats.columns
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ml_features_new.py -v`
Expected: FAIL — 旧 extract_features 签名/行为不符

- [ ] **Step 3: 重写 feature_pipeline.py**

```python
# src/aimoon/ml/feature_pipeline.py
"""特征提取 — 11 个 A 股因子 z-score + 6 基础技术统计。

替代旧版（Alpha360 + Robust + Alpha Zoo + ICIR + 中性化 + SVD）。仅 ~18 特征。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from aimoon.factors.ashare import ASHARE_FACTORS, compute_ashare_factors, robust_zscore

logger = logging.getLogger(__name__)

_TECH_WINDOWS = (5, 10, 20)


def _technical_stats(panel: dict[str, pd.DataFrame], target_date: pd.Timestamp) -> pd.DataFrame:
    """6 维技术统计：5/10/20 日波动率与收益率。index=code。"""
    close = panel["close"]
    if target_date not in close.index:
        target_date = close.index[-1]
    rets = close.pct_change()
    idx = close.index.get_loc(target_date)
    records: dict[str, dict[str, float]] = {}
    for code in close.columns:
        r = rets[code].iloc[: idx + 1].dropna()
        rec: dict[str, float] = {}
        for w in _TECH_WINDOWS:
            window = r.iloc[-w:] if len(r) >= w else r
            rec[f"tech_vol_{w}d"] = float(window.std()) if len(window) > 1 else 0.0
            rec[f"tech_ret_{w}d"] = float(window.mean()) if len(window) > 0 else 0.0
        records[code] = rec
    return pd.DataFrame.from_dict(records, orient="index")


def extract_features(
    panel: dict[str, pd.DataFrame],
    target_date: pd.Timestamp | None = None,
    fundamentals: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    factor_series: dict[str, pd.DataFrame] | None = None,
    feature_medians: pd.Series | None = None,
) -> pd.DataFrame:
    """提取特征矩阵（index=code, columns=~18 特征）。训练/推理同函数。"""
    if panel is None or "close" not in panel:
        return pd.DataFrame()
    close = panel["close"]
    if target_date is None:
        target_date = close.index[-1]
    if target_date not in close.index:
        target_date = close.index[-1]

    factor_series = factor_series or compute_ashare_factors(panel, fundamentals, sector_map)

    # 截面切片 + 稳健 z-score
    factor_rows = {}
    for fid in ASHARE_FACTORS:
        fdf = factor_series.get(fid)
        if fdf is None or target_date not in fdf.index:
            factor_rows[fid] = pd.Series(np.nan, index=close.columns)
        else:
            row = fdf.loc[target_date]
            factor_rows[fid] = row
    factor_df = pd.DataFrame(factor_rows, index=close.columns)
    factor_df = robust_zscore(factor_df.T).T  # 对每列（因子）截面 z-score

    # 技术统计
    tech = _technical_stats(panel, target_date)

    result = pd.concat([factor_df, tech], axis=1)
    result = result.reindex(close.columns)

    if feature_medians is not None:
        medians = feature_medians.reindex(result.columns, fill_value=0.0)
        result = result.fillna(medians)
    else:
        result = result.fillna(result.median()).fillna(0.0)
    return result
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ml_features_new.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/ml/feature_pipeline.py tests/test_ml_features_new.py
git commit -m "feat(ml): rewrite extract_features — 11 factors z-score + 6 tech stats"
```

---

### Task 9: `MLPredictor`

**Files:**
- Create: `src/aimoon/ml/predictor.py`
- Test: `tests/test_ml_predictor_new.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ml_predictor_new.py
import numpy as np
import pandas as pd
import pytest
from aimoon.ml.predictor import MLPredictor


class _FakeModel:
    def __init__(self, scores):
        self._scores = scores

    def predict(self, X):
        return np.array([self._scores[c] for c in X.index])


def test_predict_percentile_ranks_cross_section():
    panel = {"close": pd.DataFrame({"a": [1.0] * 70, "b": [1.0] * 70,
                                    "c": [1.0] * 70, "d": [1.0] * 70},
                                   index=pd.date_range("2024-01-01", periods=70))}
    # 构造最小 panel 让 extract_features 可运行
    for k in ["open", "high", "low", "volume", "turnover", "amount"]:
        panel[k] = panel["close"]
    feats = pd.DataFrame({"f1": [0.1, 0.2, 0.3, 0.4]}, index=["a", "b", "c", "d"])
    model = _FakeModel({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})
    predictor = MLPredictor(
        model=model, feature_names=("f1",),
        feature_medians=pd.Series({"f1": 0.0}),
    )
    # 用 monkeypatch 跳过 extract_features：直接传 panel 但覆盖 predict
    scores = predictor.predict_percentile(panel, panel["close"].index[-1])
    assert scores["d"] == 100  # 最高
    assert scores["a"] == 0     # 最低


def test_from_cache_missing_returns_none(tmp_path):
    assert MLPredictor.from_cache(str(tmp_path)) is None


def test_has_model_flag():
    p = MLPredictor(model=None, feature_names=(), feature_medians=pd.Series(dtype=float))
    assert p.has_model is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ml_predictor_new.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 predictor**

```python
# src/aimoon/ml/predictor.py
"""单 LightGBM 预测器 — 输出截面百分位 0-100。替代 EnsemblePredictor。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_MODEL_TTL_DAYS = 7


@dataclass(frozen=True)
class MLPredictor:
    model: object
    feature_names: tuple[str, ...]
    feature_medians: pd.Series

    @property
    def has_model(self) -> bool:
        return self.model is not None

    @classmethod
    def from_cache(cls, cache_dir: str | None) -> "MLPredictor | None":
        if not cache_dir:
            return None
        model_dir = Path(cache_dir) / "ml"
        model_path = model_dir / "lgbm_model.txt"
        meta_path = model_dir / "meta.json"
        names_path = model_dir / "feature_names.json"
        medians_path = model_dir / "feature_medians.json"
        if not model_path.exists() or not meta_path.exists():
            return None
        import time

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            age_days = (time.time() - meta.get("timestamp", 0)) / 86400
            if age_days > _MODEL_TTL_DAYS:
                logger.info("ML 模型过期（%.1f 天），需重训", age_days)
                return None
            import lightgbm as lgb

            model = lgb.Booster(model_file=str(model_path))
            names = tuple(json.loads(names_path.read_text(encoding="utf-8")))
            medians = pd.read_json(medians_path, typ="series")
            return cls(model=model, feature_names=names, feature_medians=medians)
        except Exception as e:
            logger.warning("加载 ML 模型失败: %s", e)
            return None

    def predict_percentile(
        self,
        panel: dict[str, pd.DataFrame],
        target_date: pd.Timestamp,
        fundamentals: pd.DataFrame | None = None,
        sector_map: dict[str, str] | None = None,
    ) -> dict[str, int]:
        """返回 {code: 0-100 百分位}。无模型返回空 dict。"""
        if not self.has_model:
            return {}
        from aimoon.ml.feature_pipeline import extract_features

        feats = extract_features(
            panel, target_date=target_date, fundamentals=fundamentals,
            sector_map=sector_map, feature_medians=self.feature_medians,
        )
        if feats.empty:
            return {}
        feats = feats.reindex(columns=list(self.feature_names)).fillna(0.0)
        raw = self.model.predict(feats)
        pct = pd.Series(raw, index=feats.index).rank(pct=True) * 100.0
        return {code: int(round(v)) for code, v in pct.items()}
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ml_predictor_new.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/ml/predictor.py tests/test_ml_predictor_new.py
git commit -m "feat(ml): add MLPredictor — single LightGBM + cross-sectional percentile"
```

---

## Phase 3 — 单 LightGBM 训练

### Task 10: 精简 `optimized_config.py`

**Files:**
- Rewrite: `src/aimoon/ml/optimized_config.py`
- Test: `tests/test_optimized_config_new.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_optimized_config_new.py
from aimoon.ml.optimized_config import LGBM_PARAMS, TRAINING_CONFIG


def test_lgbm_params_present():
    assert LGBM_PARAMS["objective"] == "regression"
    assert LGBM_PARAMS["n_estimators"] == 300
    assert LGBM_PARAMS["max_depth"] == 4


def test_training_config_n_dates_120():
    assert TRAINING_CONFIG["n_dates"] == 120
    assert TRAINING_CONFIG["cv_folds"] == 2
    assert TRAINING_CONFIG["forward_days"] == 5
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_optimized_config_new.py -v`
Expected: FAIL — 旧常量名/值不符

- [ ] **Step 3: 重写 optimized_config.py**

```python
# src/aimoon/ml/optimized_config.py
"""ML 训练配置 — 单 LightGBM（精简版）。"""
from __future__ import annotations

from typing import Any

LGBM_PARAMS: dict[str, Any] = {
    "max_depth": 4,
    "num_leaves": 31,
    "n_estimators": 300,
    "learning_rate": 0.03,
    "min_child_samples": 50,
    "subsample": 0.7,
    "colsample_bytree": 0.6,
    "reg_lambda": 5.0,
    "reg_alpha": 2.0,
    "early_stopping_rounds": 30,
    "objective": "regression",
    "metric": "rmse",
    "random_state": 42,
    "verbose": -1,
    "n_jobs": -1,
}

TRAINING_CONFIG: dict[str, Any] = {
    "n_dates": 120,
    "forward_days": 5,
    "cv_folds": 2,
    "purge_days": 5,
    "embargo_days": 5,
}

OUTPUT_CONFIG: dict[str, Any] = {
    "save_dir": ".aimoon_cache/ml",
    "model_ttl_days": 7,
}
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_optimized_config_new.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/ml/optimized_config.py tests/test_optimized_config_new.py
git commit -m "refactor(ml): slim optimized_config — single LightGBM params, n_dates=120, 2 folds"
```

---

### Task 11: 精简 `training_loop.py` — `train_lightgbm_cv`

**Files:**
- Rewrite: `src/aimoon/ml/training_loop.py`
- Test: `tests/test_training_loop_new.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_training_loop_new.py
import numpy as np
import pandas as pd
import pytest
from aimoon.ml.training_loop import train_lightgbm_cv


def _synth(n_dates=60, n_stocks=20):
    idx = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    rng = np.random.default_rng(42)
    X = pd.DataFrame(rng.normal(size=(n_dates * n_stocks, 5)),
                     columns=[f"f{i}" for i in range(5)])
    X["_date"] = np.repeat(idx.values, n_stocks)
    X["_code"] = (["s" + str(i) for i in range(n_stocks)]) * n_dates
    y = pd.Series(X["f0"] * 0.5 + rng.normal(scale=0.1, size=len(X)))
    return X, y


def test_train_lightgbm_cv_returns_model_and_ics():
    X, y = _synth()
    from aimoon.ml.optimized_config import LGBM_PARAMS
    model, ics, best_round = train_lightgbm_cv(X, y, LGBM_PARAMS, forward_days=5)
    assert model is not None
    assert isinstance(ics, list)
    assert len(ics) >= 1
    assert isinstance(best_round, int)


def test_train_lightgbm_cv_no_optuna_dependency():
    # 确保不导入 optuna
    import sys
    X, y = _synth()
    from aimoon.ml.optimized_config import LGBM_PARAMS
    train_lightgbm_cv(X, y, LGBM_PARAMS, forward_days=5)
    assert "optuna" not in sys.modules
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_training_loop_new.py -v`
Expected: FAIL — `ImportError`/签名不符

- [ ] **Step 3: 重写 training_loop.py**

```python
# src/aimoon/ml/training_loop.py
"""单 LightGBM 训练循环 — 2 折 Purged TSCV。无 Optuna、无早停复杂逻辑。"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _spearmanr_safe(a: np.ndarray, b: np.ndarray) -> float:
    from scipy.stats import spearmanr

    if np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return 0.0
    corr, _ = spearmanr(a, b)
    return 0.0 if np.isnan(corr) else float(corr)


def train_lightgbm_cv(
    X: pd.DataFrame,
    y: pd.Series,
    params: dict[str, Any],
    forward_days: int,
    n_folds: int = 2,
) -> tuple[object, list[float], int]:
    """2 折 Purged TSCV 训练单 LightGBM，返回 (final_model, fold_ics, best_round)。"""
    import lightgbm as lgb
    from aimoon.ml.purged_tscv import PurgedTimeSeriesSplit

    dates_column = None
    if "_date" in X.columns:
        dates_column = X["_date"].copy()
        X_feat = X.drop(columns=["_date", "_code"], errors="ignore")
    else:
        X_feat = X.drop(columns=["_code"], errors="ignore")

    tscv = PurgedTimeSeriesSplit(
        n_splits=n_folds, purge_days=forward_days, embargo_days=forward_days,
    )
    X_with_dates = X_feat.copy()
    if dates_column is not None:
        X_with_dates["_date"] = dates_column

    ics: list[float] = []
    best_round = 0
    best_model: object | None = None

    fit_params = {k: v for k, v in params.items()
                  if k not in ("early_stopping_rounds", "n_estimators")}

    for train_idx, val_idx in tscv.split(X_with_dates, date_column="_date"):
        if len(train_idx) < 10 or len(val_idx) < 5:
            continue
        X_tr, X_va = X_feat.iloc[train_idx], X_feat.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
        dtr = lgb.Dataset(X_tr, label=y_tr)
        dva = lgb.Dataset(X_va, label=y_va, reference=dtr)
        model = lgb.train(
            fit_params, dtr, num_boost_round=params["n_estimators"],
            valid_sets=[dva], callbacks=[
                lgb.early_stopping(params.get("early_stopping_rounds", 30), verbose=False),
            ],
        )
        preds = model.predict(X_va)
        ics.append(_spearmanr_safe(preds, y_va.values))
        best_model = model  # 最后一折模型作为最终（时间序列：最靠后）
        best_round = int(model.best_iteration)

    # 全量训练最终模型（用最佳轮数）
    final_rounds = best_round if best_round > 0 else params["n_estimators"]
    dtrain_full = lgb.Dataset(X_feat, label=y)
    final_model = lgb.train(fit_params, dtrain_full, num_boost_round=final_rounds)
    logger.info("LightGBM CV: %d 折, fold ICs=%s", len(ics), [round(i, 4) for i in ics])
    return final_model, ics, best_round
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_training_loop_new.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/ml/training_loop.py tests/test_training_loop_new.py
git commit -m "refactor(ml): rewrite training_loop — single LightGBM 2-fold Purged TSCV, no Optuna"
```

---

### Task 12: 精简 `trainer.py` — `train_model`

**Files:**
- Rewrite: `src/aimoon/ml/trainer.py`
- Test: `tests/test_ml_trainer_new.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ml_trainer_new.py
import numpy as np
import pandas as pd
from aimoon.ml.trainer import train_model


def _panel(n_dates=80, n_stocks=15):
    idx = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    rng = np.random.default_rng(0)
    close = pd.DataFrame(
        rng.uniform(8, 12, size=(n_dates, n_stocks)),
        index=idx, columns=[f"s{i}" for i in range(n_stocks)],
    )
    return {"close": close, "open": close, "high": close + 0.5, "low": close - 0.5,
            "volume": close * 1000, "turnover": close * 0 + 1.0, "amount": close * 1e6}


def _klines(panel):
    close = panel["close"]
    return {c: close[c].to_frame("close").assign(
        open=close[c], high=close[c], low=close[c],
        volume=close[c] * 1000, turnover=1.0, amount=1e6,
    ) for c in close.columns}


def test_train_model_returns_result_with_ic(tmp_path):
    panel = _panel()
    klines = _klines(panel)
    result = train_model(
        panel, klines, n_dates=40, forward_days=5, save_dir=str(tmp_path),
    )
    assert result.model is not None
    assert len(result.feature_names) > 0
    assert isinstance(result.ic, float)
    assert result.n_stocks > 0
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ml_trainer_new.py -v`
Expected: FAIL — 旧 train_model 签名不符

- [ ] **Step 3: 重写 trainer.py**

```python
# src/aimoon/ml/trainer.py
"""单 LightGBM 训练 — train_model()。替代旧 ensemble/dual 训练。"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aimoon.factors.ashare import ASHARE_FACTORS, compute_ashare_factors
from aimoon.ml.feature_pipeline import extract_features
from aimoon.ml.label_engine import generate_labels
from aimoon.ml.optimized_config import LGBM_PARAMS, TRAINING_CONFIG
from aimoon.ml.training_loop import train_lightgbm_cv

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrainingResult:
    model: Any = field(repr=False)
    feature_names: tuple[str, ...]
    feature_medians: pd.Series
    ic: float
    n_stocks: int
    n_dates: int
    train_duration: float


def _select_dates_evenly(dates: list, n_dates: int, min_interval: int = 5) -> list:
    if not dates:
        return []
    if len(dates) < (n_dates - 1) * min_interval + 1:
        n_dates = max(1, (len(dates) - 1) // min_interval + 1)
    if n_dates <= 1:
        return [dates[len(dates) // 2]]
    step = (len(dates) - 1) / (n_dates - 1)
    return sorted({dates[int(i * step)] for i in range(n_dates)})


def train_model(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    *,
    n_dates: int = TRAINING_CONFIG["n_dates"],
    forward_days: int = TRAINING_CONFIG["forward_days"],
    save_dir: str,
    fundamentals: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    force: bool = False,
) -> TrainingResult:
    """训练单 LightGBM 模型并持久化。"""
    t0 = time.time()
    close = panel["close"]
    all_dates = list(close.index)
    target_dates = _select_dates_evenly(all_dates, n_dates)

    factor_series = compute_ashare_factors(panel, fundamentals, sector_map)

    # 收集 (date, code) 样本
    feat_blocks: list[pd.DataFrame] = []
    label_blocks: list[pd.Series] = []
    for d in target_dates:
        feats = extract_features(
            panel, target_date=d, fundamentals=fundamentals, sector_map=sector_map,
            factor_series=factor_series,
        )
        labels = generate_labels(klines, d, forward_days=forward_days)
        common = feats.index.intersection(labels.index)
        if len(common) < 5:
            continue
        f = feats.loc[common].copy()
        f["_date"] = d
        f["_code"] = common
        feat_blocks.append(f)
        label_blocks.append(labels.loc[common])

    if not feat_blocks:
        raise ValueError("训练样本不足：无法收集特征/标签")

    X = pd.concat(feat_blocks)
    y = pd.concat(label_blocks).reindex(X.index)
    valid = y.notna()
    X, y = X[valid], y[valid]

    feature_names = tuple(c for c in X.columns if c not in ("_date", "_code"))
    feature_medians = X[feature_names].median()

    model, ics, _ = train_lightgbm_cv(X, y, LGBM_PARAMS, forward_days=forward_days)
    ic = float(np.mean(ics)) if ics else 0.0

    _save(model, feature_names, feature_medians, ic, save_dir)
    duration = time.time() - t0
    logger.info("训练完成: IC=%.4f, 样本=%d, 用时=%.1fs", ic, len(X), duration)
    return TrainingResult(
        model=model, feature_names=feature_names, feature_medians=feature_medians,
        ic=ic, n_stocks=close.shape[1], n_dates=len(target_dates), train_duration=duration,
    )


def _save(
    model: Any, feature_names: tuple[str, ...], feature_medians: pd.Series,
    ic: float, save_dir: str,
) -> None:
    d = Path(save_dir)
    d.mkdir(parents=True, exist_ok=True)
    model.save_model(str(d / "lgbm_model.txt"))
    (d / "feature_names.json").write_text(json.dumps(list(feature_names)), encoding="utf-8")
    feature_medians.to_json(d / "feature_medians.json")
    meta = {"ic": ic, "timestamp": time.time()}
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ml_trainer_new.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/ml/trainer.py tests/test_ml_trainer_new.py
git commit -m "refactor(ml): rewrite trainer — single LightGBM train_model + persistence"
```

---

## Phase 4 — Screener / models / output

### Task 13: 简化 `models.py`

**Files:**
- Modify: `src/aimoon/models.py`

- [ ] **Step 1: 更新 ScoredStock**

```python
# src/aimoon/models.py — 保留 Signal（输出展示可能用），ScoredStock signals 默认空元组
```

编辑 `ScoredStock`：保持字段，但更新 docstring 说明 `total_score = ml_score or 0`，`signals` 默认空元组（评分不再使用技术信号）。`Signal` 类保留（最小化，输出层可选）。

实际改动：仅更新 `models.py` 第 26-48 行的 docstring，移除对 hybrid 评分的引用；字段不变（保持向后兼容最小改动）。

- [ ] **Step 2: 验证导入**

Run: `python -c "from aimoon.models import ScoredStock, Signal; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 3: 提交**

```bash
git add src/aimoon/models.py
git commit -m "docs(models): note total_score=ml_score, signals default empty"
```

---

### Task 14: `screener.screen_stock` ML-only

**Files:**
- Rewrite: `src/aimoon/screener.py`
- Test: `tests/test_screener_new.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_screener_new.py
import numpy as np
import pandas as pd
from aimoon.screener import screen_stock


def _kline():
    idx = pd.date_range("2024-01-01", periods=70, freq="D")
    return pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": np.linspace(10, 11, 70),
         "volume": 1000.0, "turnover": 1.0, "amount": 1e6, "pct_change": 0.0},
        index=idx,
    )


def test_screen_stock_returns_scored_with_ml_score_none_when_no_predictor():
    df = _kline()
    scored = screen_stock("000001", "测试", df, predictor=None)
    assert scored is not None
    assert scored.code == "000001"
    assert scored.ml_score is None
    assert scored.total_score == 0


def test_screen_stock_too_short_returns_none():
    df = _kline().iloc[:40]
    assert screen_stock("000001", "测试", df, predictor=None) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_screener_new.py -v`
Expected: FAIL — 旧 screen_stock 签名不符

- [ ] **Step 3: 重写 screener.py（仅保留 ML 分数路径）**

```python
# src/aimoon/screener.py
"""筛选器 — ML 百分位即最终分数（删技术信号/hybrid 评分）。"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

from aimoon.config import Config
from aimoon.models import ScoredStock

if TYPE_CHECKING:
    from aimoon.cache import DataCache
    from aimoon.ml.predictor import MLPredictor

logger = logging.getLogger(__name__)


def screen_stock(
    code: str,
    name: str,
    kline: pd.DataFrame,
    *,
    predictor: "MLPredictor | None" = None,
    panel: dict[str, pd.DataFrame] | None = None,
    target_date: pd.Timestamp | None = None,
    fundamentals: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
) -> ScoredStock | None:
    """对单股评分：ml_score 来自预测器（缺失则 None），total_score = ml_score or 0。"""
    if kline is None or len(kline) < 60:
        return None
    price = float(kline["close"].iloc[-1])
    pct = float(kline["pct_change"].iloc[-1]) if "pct_change" in kline.columns else 0.0
    turnover = float(kline["turnover"].iloc[-1]) if "turnover" in kline.columns else None

    ml_score: int | None = None
    if predictor is not None and predictor.has_model and panel is not None:
        d = target_date or panel["close"].index[-1]
        scores = predictor.predict_percentile(
            panel, d, fundamentals=fundamentals, sector_map=sector_map,
        )
        ml_score = scores.get(code)

    return ScoredStock(
        code=code, name=name, price=price, pct_change=pct, turnover=turnover,
        ml_score=ml_score, total_score=ml_score or 0,
    )


def screen_universe(
    universe: pd.DataFrame,
    cfg: Config,
    cache: "DataCache",
    predictor: "MLPredictor | None" = None,
    klines: dict[str, pd.DataFrame] | None = None,
    panel: dict[str, pd.DataFrame] | None = None,
    fundamentals: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
) -> tuple[list[ScoredStock], dict[str, pd.DataFrame]]:
    """并发评分候选池。返回 (results, kline_tails)。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from aimoon.data.history import get_kline

    results: list[ScoredStock] = []
    tails: dict[str, pd.DataFrame] = {}
    target_date = panel["close"].index[-1] if panel else None

    def _process(row: pd.Series) -> tuple[ScoredStock | None, str, pd.DataFrame | None]:
        code, name = row["stock_code"], row["stock_name"]
        kdf = (klines or {}).get(code)
        if kdf is None:
            r = get_kline(code, cfg.history_days, cache)
            if r.is_err():
                return None, code, None
            kdf = r.unwrap()
        scored = screen_stock(
            code, name, kdf, predictor=predictor, panel=panel,
            target_date=target_date, fundamentals=fundamentals, sector_map=sector_map,
        )
        return scored, code, kdf.tail(25).copy() if scored else None

    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        futures = {ex.submit(_process, row): row["stock_code"] for _, row in universe.iterrows()}
        for fut in as_completed(futures):
            try:
                scored, code, tail = fut.result()
                if scored:
                    results.append(scored)
                    if tail is not None:
                        tails[code] = tail
            except Exception as e:
                logger.warning("Screen failed: %s", e)
    return results, tails
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_screener_new.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/screener.py tests/test_screener_new.py
git commit -m "refactor(screener): ML-only screen_stock — total_score = ml_score"
```

---

### Task 15: `output.py` ML 分数展示

**Files:**
- Modify: `src/aimoon/output.py`

- [ ] **Step 1: 调整展示列**

`output.py` 中生成 CSV/Markdown 表格的部分，移除对技术信号 breakdown 的引用，改为以 `ml_score`/`total_score` 为主列。具体：用 `Grep` 找到引用 `signals`/`hybrid_score`/`analyze_score_breakdown` 的位置，改为展示 `ml_score`、`total_score`、建议（基于 total_score 阈值）。

Run: `grep -n "hybrid_score\|analyze_score_breakdown\|signals" src/aimoon/output.py`

- [ ] **Step 2: 改造**（按 Grep 结果逐处替换）

把 `get_suggestion(score)` 逻辑内联或保留 `scoring/__init__.py` 薄封装（见 Task 19）。表格列：代码、名称、现价、涨跌、ML分数、建议。

- [ ] **Step 3: 冒烟验证**

Run: `python -c "from aimoon.output import OutputFormatter; print('ok')"`
Expected: 输出 `ok`（无导入错误）

- [ ] **Step 4: 提交**

```bash
git add src/aimoon/output.py
git commit -m "refactor(output): ML-score-driven display, drop technical signal breakdown"
```

---

## Phase 5 — 回测单引擎

### Task 16: `backtest.precompute_scores`

**Files:**
- Create: `src/aimoon/backtest.py`
- Test: `tests/test_backtest_precompute.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_backtest_precompute.py
import numpy as np
import pandas as pd
from aimoon.backtest import precompute_scores


class _FakePredictor:
    has_model = True

    def predict_percentile(self, panel, target_date, fundamentals=None, sector_map=None):
        # 让 a 永远高分
        return {"a": 90, "b": 10}


def _panel(n=80):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = pd.DataFrame({"a": np.linspace(10, 12, n), "b": np.linspace(10, 9, n)}, index=idx)
    return {"close": close, "open": close, "high": close, "low": close,
            "volume": close * 1000, "turnover": close * 0 + 1.0, "amount": close * 1e6}


def test_precompute_scores_returns_dict_per_date():
    panel = _panel()
    dates = list(panel["close"].index[-30:])
    scores = precompute_scores(panel, _FakePredictor(), None, None, dates)
    assert len(scores) == 30
    first = scores[dates[0]]
    assert first["a"] == 90
    assert first["b"] == 10
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_backtest_precompute.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 backtest.py 骨架 + precompute**

```python
# src/aimoon/backtest.py
"""ML 分数驱动回测单引擎 — 预计算分数 + 单循环。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacktestResult:
    total_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    trade_count: int
    avg_hold_days: float
    trades: tuple[dict, ...] = field(default_factory=tuple)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
```


def precompute_scores(
    panel: dict[str, pd.DataFrame],
    predictor: Any,
    fundamentals: pd.DataFrame | None,
    sector_map: dict[str, str] | None,
    dates: list[pd.Timestamp],
) -> dict[pd.Timestamp, dict[str, int]]:
    """对每个日期调用预测器，返回 {date: {code: 0-100}}。"""
    scores: dict[pd.Timestamp, dict[str, int]] = {}
    for d in dates:
        scores[d] = predictor.predict_percentile(
            panel, d, fundamentals=fundamentals, sector_map=sector_map,
        )
    return scores
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_backtest_precompute.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/backtest.py tests/test_backtest_precompute.py
git commit -m "feat(backtest): add precompute_scores + BacktestResult dataclass"
```

---

### Task 17: `run_backtest` 单引擎

**Files:**
- Modify: `src/aimoon/backtest.py`
- Test: `tests/test_backtest_engine.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_backtest_engine.py
import numpy as np
import pandas as pd
from aimoon.backtest import run_backtest
from aimoon.config import Config


def _panel_klines(n=120):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(0)
    close = pd.DataFrame({"a": np.linspace(10, 13, n), "b": np.linspace(10, 9, n)}, index=idx)
    panel = {"close": close, "open": close, "high": close + 0.1, "low": close - 0.1,
             "volume": close * 1000, "turnover": close * 0 + 1.0, "amount": close * 1e6}
    klines = {c: close[c].to_frame("close").assign(
        open=close[c], high=close[c], low=close[c],
        volume=close[c] * 1000, turnover=1.0, amount=1e6) for c in close.columns}
    return panel, klines, idx


def test_run_backtest_produces_metrics():
    panel, klines, idx = _panel_klines()
    dates = list(idx)
    scores = {d: {"a": 90, "b": 10} for d in dates}
    cfg = Config(entry_threshold=60, max_positions=4, hold_days=12,
                 stop_loss_pct=0.04, take_profit_pct=0.14)
    result = run_backtest(panel, klines, scores, cfg)
    assert result.trade_count >= 0
    assert isinstance(result.total_return, float)
    assert isinstance(result.sharpe, float)
    assert 0.0 <= result.win_rate <= 1.0
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_backtest_engine.py -v`
Expected: FAIL — `run_backtest` 未定义

- [ ] **Step 3: 实现 run_backtest**

在 `backtest.py` 追加：

```python
def _compute_metrics(equity: pd.Series, trades: list[dict]) -> BacktestResult:
    """从净值曲线与交易列表计算指标（自包含，不依赖 enhanced_backtest）。"""
    if len(equity) < 2 or not trades:
        return BacktestResult(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, (), equity)

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1) * 100
    n_days = len(equity) - 1
    annual_return = float((equity.iloc[-1] / equity.iloc[0]) ** (252 / max(n_days, 1)) - 1) * 100
    rets = equity.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 1e-12 else 0.0
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax
    max_drawdown = float(drawdown.min() * 100)
    wins = [t for t in trades if t["return"] > 0]
    losses = [t for t in trades if t["return"] <= 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    gross_win = sum(t["return"] for t in wins)
    gross_loss = -sum(t["return"] for t in losses)
    profit_factor = gross_win / gross_loss if gross_loss > 1e-12 else float("inf")
    avg_hold = float(np.mean([t["hold_days"] for t in trades])) if trades else 0.0
    return BacktestResult(
        total_return=total_return, annual_return=annual_return, sharpe=sharpe,
        max_drawdown=max_drawdown, win_rate=win_rate, profit_factor=profit_factor,
        trade_count=len(trades), avg_hold_days=avg_hold,
        trades=tuple(trades), equity_curve=equity,
    )


def run_backtest(
    panel: dict[str, pd.DataFrame],
    klines: dict[str, pd.DataFrame],
    scores: dict[pd.Timestamp, dict[str, int]],
    cfg: Any,
) -> BacktestResult:
    """ML 分数驱动单引擎回测。入场用 T 日收盘价，退出止损/止盈/跟踪止损/最大持有。"""
    close = panel["close"]
    dates = list(close.index)
    cash = 1.0
    positions: dict[str, dict] = {}  # code -> {entry_price, entry_date, peak}
    equity_curve: list[float] = []
    trades: list[dict] = []
    max_pos = cfg.max_positions
    per_pos_value = 1.0 / max_pos

    for i, d in enumerate(dates):
        # 1. 退出检查
        for code in list(positions.keys()):
            if code not in close.columns:
                continue
            price = float(close.loc[d, code])
            pos = positions[code]
            pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
            pos["peak"] = max(pos["peak"], price)
            peak_pnl = (pos["peak"] - pos["entry_price"]) / pos["entry_price"]
            exit_price = None
            reason = None
            high = float(panel["high"].loc[d, code]) if "high" in panel else price
            low = float(panel["low"].loc[d, code]) if "low" in panel else price
            if low <= pos["entry_price"] * (1 - cfg.stop_loss_pct):
                exit_price, reason = pos["entry_price"] * (1 - cfg.stop_loss_pct), "stop_loss"
            elif high >= pos["entry_price"] * (1 + cfg.take_profit_pct):
                exit_price, reason = pos["entry_price"] * (1 + cfg.take_profit_pct), "take_profit"
            elif peak_pnl >= 0.06 and pnl_pct <= peak_pnl - 0.06:
                exit_price, reason = price, "trailing_lock"
            elif peak_pnl >= 0.03 and pnl_pct <= 0.0:
                exit_price, reason = price, "trailing_breakeven"
            elif (d - pos["entry_date"]).days >= cfg.hold_days:
                exit_price, reason = price, "max_hold"
            if exit_price is not None:
                ret = (exit_price - pos["entry_price"]) / pos["entry_price"]
                cash += per_pos_value * (1 + ret)
                trades.append({
                    "code": code, "entry_date": pos["entry_date"], "exit_date": d,
                    "entry_price": pos["entry_price"], "exit_price": exit_price,
                    "return": ret, "reason": reason,
                    "hold_days": (d - pos["entry_date"]).days,
                })
                del positions[code]

        # 2. 入场检查（T 日收盘价）
        day_scores = scores.get(d, {})
        candidates = sorted(
            [(c, s) for c, s in day_scores.items() if s >= cfg.entry_threshold and c not in positions],
            key=lambda x: -x[1],
        )
        for code, _ in candidates:
            if len(positions) >= max_pos:
                break
            if code not in close.columns:
                continue
            entry_price = float(close.loc[d, code])
            if entry_price <= 0:
                continue
            cash -= per_pos_value
            positions[code] = {
                "entry_price": entry_price, "entry_date": d, "peak": entry_price,
            }

        # 3. 净值
        mv = sum(
            per_pos_value * (1 + (float(close.loc[d, c]) - p["entry_price"]) / p["entry_price"])
            for c, p in positions.items() if c in close.columns
        )
        equity_curve.append(cash + mv)

    equity = pd.Series(equity_curve, index=dates)
    return _compute_metrics(equity, trades)
```

> 自包含实现，**不依赖** `enhanced_backtest/metrics.py`（其旧签名 `compute_metrics(trades, equity, dd_curve, ...)` 返回 `EnhancedPortfolioResult`，与本设计不兼容）。整个 `enhanced_backtest/` 包将在 Task 24 删除。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_backtest_engine.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/backtest.py tests/test_backtest_engine.py
git commit -m "feat(backtest): ML-score-driven single engine with stop/take/trailing exits"
```

---

### Task 18: 删除整个 `enhanced_backtest/` 包

> 自审发现：旧 `enhanced_backtest/metrics.py` 的 `compute_metrics(trades, equity, dd_curve, ...)` 返回 `EnhancedPortfolioResult`，与本设计不兼容。新 `backtest.py` 的 `_compute_metrics` 已自包含，故整个 `enhanced_backtest/` 包可删除。

**Files:** Delete `src/aimoon/enhanced_backtest/`（全部，含 `__init__.py`/`metrics.py`/`models.py`/`engine.py` 等）

- [ ] **Step 1: 查引用**

Run: `grep -rln "aimoon.enhanced_backtest" src/ tests/`

- [ ] **Step 2: 修复引用**（全部指向 `aimoon.backtest` 的 `BacktestResult`/`run_backtest`）

- [ ] **Step 3: 删除包**

```bash
git rm -r src/aimoon/enhanced_backtest
```

- [ ] **Step 4: 删除相关测试**

删除 `tests/test_metrics.py`、`tests/test_enhanced_backtest.py`、`tests/test_backtest_position.py`（若仅测被删包）。

- [ ] **Step 5: 验证**

Run: `ruff check src/aimoon/ && pytest tests/test_backtest_engine.py tests/test_backtest_precompute.py -v`
Expected: 无错误

- [ ] **Step 6: 提交**

```bash
git add -A && git commit -m "refactor(backtest): delete entire enhanced_backtest package (self-contained backtest.py)"
```

---

## Phase 6 — CLI / config

### Task 19: `config.py` 精简 + `scoring/__init__.py` 薄封装

**Files:**
- Modify: `src/aimoon/config.py`, `src/aimoon/scoring/__init__.py`

- [ ] **Step 1: 删除 use_alpha/use_reversal 字段**

在 `config.py` 的 `Config` dataclass 中删除 `use_alpha: bool = True` 与 `use_reversal: bool = False`，并删除 `load_config` 中对 `no_alpha`/`reversal` 的处理。

- [ ] **Step 2: 重写 scoring/__init__.py 薄封装**

```python
# src/aimoon/scoring/__init__.py
"""评分薄封装 — ML 百分位即最终分数。仅保留建议阈值映射。"""
from __future__ import annotations


def get_suggestion(score: int) -> tuple[str, str]:
    """根据 ML 百分位分数返回 (建议, 置信度)。"""
    if score >= 75:
        return "强烈买入", "高"
    if score >= 60:
        return "买入", "中高"
    if score >= 50:
        return "建议买入", "中"
    if score >= 40:
        return "观望", "低"
    if score >= 30:
        return "谨慎", "中"
    if score >= 20:
        return "建议卖出", "中高"
    return "强烈卖出", "高"


__all__ = ["get_suggestion"]
```

- [ ] **Step 3: 验证导入**

Run: `python -c "from aimoon.config import Config; from aimoon.scoring import get_suggestion; print(get_suggestion(80))"`
Expected: 输出 `('强烈买入', '高')`

- [ ] **Step 4: 提交**

```bash
git add src/aimoon/config.py src/aimoon/scoring/__init__.py
git commit -m "refactor(config,scoring): drop use_alpha/use_reversal, slim scoring to get_suggestion"
```

---

### Task 20: `cli.py` 接新训练/回测

**Files:**
- Modify: `src/aimoon/cli.py`

- [ ] **Step 1: 改 `_run_train_model`**

将 `_run_train_model`（约 cli.py:181）改为调用 `aimoon.ml.trainer.train_model`（单 LightGBM），删除 `train_ensemble`/`--optuna`/`--smart-incremental`/`--early-stop` 等选项分支；保留 `--n-days`、`--forward-days`、`--force`。删除 `--no-alpha`/`--reversal` 参数定义（约 cli.py:133 的 train-model subparser 及 screening parser）。

- [ ] **Step 2: 改 `_run_backtest`**

将 `_run_backtest`（约 cli.py:304）改为：构建 panel → 加载 `MLPredictor.from_cache` → `precompute_scores` → `run_backtest` → 输出 `BacktestResult`。删除 qf_backtest/RPS/walk-forward 分支。

- [ ] **Step 3: 改默认 screening 命令**

默认 `aimoon`（无子命令）调用 `screen_universe` 时传入 `MLPredictor.from_cache(cfg.cache_dir)` + `build_panel`。无模型时提示训练。

- [ ] **Step 4: 冒烟测试**

Run: `python -c "from aimoon.cli import main; print('ok')"` 且 `aimoon --help`
Expected: 无导入错误，`--help` 正常显示（无 `--no-alpha`/`--reversal`）

- [ ] **Step 5: 提交**

```bash
git add src/aimoon/cli.py
git commit -m "refactor(cli): wire train-model to single LightGBM, backtest to ML single engine"
```

---

## Phase 7 — 激进删除

> 每个删除任务先 `Grep` 验证引用，再删文件，再修被删模块的测试文件（删除或改写）。每个任务后运行 `ruff check` + 受影响测试。

### Task 21: 删除 `scoring/` 技术信号模块

**Files:** Delete `scoring/{hybrid_scorer,combiner,signal_map,momentum,momentum_ext,reversal,mean_reversion,turtle,rsi,macd,kdj,bollinger,trend,trend_ext,volume,sector,rps,fundamentals,adaptive_weight,dedup,_ml_signal}.py`

- [ ] **Step 1: 查引用**

Run: `grep -rln "from aimoon.scoring" src/ tests/ | head` 及对每个模块名 grep。保留 `scoring/portfolio.py` 与 `scoring/__init__.py`。

- [ ] **Step 2: 删除文件**

```bash
git rm src/aimoon/scoring/hybrid_scorer.py src/aimoon/scoring/combiner.py src/aimoon/scoring/signal_map.py src/aimoon/scoring/momentum.py src/aimoon/scoring/momentum_ext.py src/aimoon/scoring/reversal.py src/aimoon/scoring/mean_reversion.py src/aimoon/scoring/turtle.py src/aimoon/scoring/rsi.py src/aimoon/scoring/macd.py src/aimoon/scoring/kdj.py src/aimoon/scoring/bollinger.py src/aimoon/scoring/trend.py src/aimoon/scoring/trend_ext.py src/aimoon/scoring/volume.py src/aimoon/scoring/sector.py src/aimoon/scoring/rps.py src/aimoon/scoring/fundamentals.py src/aimoon/scoring/adaptive_weight.py src/aimoon/scoring/dedup.py src/aimoon/scoring/_ml_signal.py
```

- [ ] **Step 3: 删除/改写相关测试**

删除 `tests/test_score_rsi.py`、`tests/test_momentum_ext.py`、`tests/test_trend_ext.py`、`tests/test_rps.py`、`tests/test_fundamentals.py`（若仅测被删模块）。

- [ ] **Step 4: 验证**

Run: `ruff check src/aimoon/scoring/ && python -c "from aimoon.scoring import get_suggestion, portfolio"`
Expected: 无错误

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "refactor(scoring): delete technical signal modules (ML-only scoring)"
```

---

### Task 22: 删除 `factors/zoo/` + registry/panel/dag 等

**Files:** Delete `src/aimoon/factors/zoo/`（全部 452 因子）、`factors/{registry,panel,dag,genetic,incremental,quality,weighting,scorer}.py`

- [ ] **Step 1: 查引用**

Run: `grep -rln "aimoon.factors.registry\|aimoon.factors.panel\|aimoon.factors.scorer\|factors.zoo\|get_default_registry" src/ tests/`

- [ ] **Step 2: 修复引用**（全部改指向 `aimoon.factors.ashare`：`build_panel`/`compute_ashare_factors`）

- [ ] **Step 3: 删除文件**

```bash
git rm -r src/aimoon/factors/zoo
git rm src/aimoon/factors/registry.py src/aimoon/factors/panel.py src/aimoon/factors/dag.py src/aimoon/factors/genetic.py src/aimoon/factors/incremental.py src/aimoon/factors/quality.py src/aimoon/factors/weighting.py src/aimoon/factors/scorer.py
```

- [ ] **Step 4: 删除相关测试**

删除 `tests/test_golden_factors.py` 及任何 `factors/zoo` 相关测试。

- [ ] **Step 5: 验证**

Run: `ruff check src/aimoon/factors/ && pytest tests/test_ashare_*.py -v`
Expected: 无错误，因子测试通过

- [ ] **Step 6: 提交**

```bash
git add -A && git commit -m "refactor(factors): delete Alpha Zoo 452 factors + registry/panel/dag (11 ashare factors replace)"
```

---

### Task 23: 删除 `ml/` 废弃模块

**Files:** Delete `ml/{alpha360,alpha360_robust,stacking,meta_ensemble,hyperopt,incremental_trainer,icir_weighter,factor_decay,factor_quality,factor_importance,covariance_estimator,feature_selector,slippage_model,walk_forward,ensemble,ensemble_signals,lgbm_trainer}.py`

- [ ] **Step 1: 查引用**

Run: `grep -rln "aimoon.ml.alpha360\|aimoon.ml.stacking\|aimoon.ml.ensemble\|aimoon.ml.hyperopt\|aimoon.ml.incremental_trainer\|aimoon.ml.icir_weighter\|aimoon.ml.factor_decay\|aimoon.ml.walk_forward\|aimoon.ml.lgbm_trainer" src/ tests/`

- [ ] **Step 2: 修复引用**（改指向新 `trainer`/`predictor`/`training_loop`）

- [ ] **Step 3: 删除文件**

```bash
git rm src/aimoon/ml/alpha360.py src/aimoon/ml/alpha360_robust.py src/aimoon/ml/stacking.py src/aimoon/ml/meta_ensemble.py src/aimoon/ml/hyperopt.py src/aimoon/ml/incremental_trainer.py src/aimoon/ml/icir_weighter.py src/aimoon/ml/factor_decay.py src/aimoon/ml/factor_quality.py src/aimoon/ml/factor_importance.py src/aimoon/ml/covariance_estimator.py src/aimoon/ml/feature_selector.py src/aimoon/ml/slippage_model.py src/aimoon/ml/walk_forward.py src/aimoon/ml/ensemble.py src/aimoon/ml/ensemble_signals.py src/aimoon/ml/lgbm_trainer.py
```

- [ ] **Step 4: 删除/改写测试**

删除 `tests/test_alpha360.py`、`tests/test_ensemble.py`、`tests/test_lgbm_trainer.py`、`tests/test_covariance_estimator.py`、`tests/test_ml_features.py`（旧的）、`tests/test_ml_predictor.py`（旧的，已被 `test_ml_predictor_new.py` 替代）、`tests/test_ml_trainer.py`（旧的）。

- [ ] **Step 5: 验证**

Run: `ruff check src/aimoon/ml/ && pytest tests/test_ml_trainer_new.py tests/test_ml_predictor_new.py tests/test_training_loop_new.py -v`
Expected: 无错误

- [ ] **Step 6: 提交**

```bash
git add -A && git commit -m "refactor(ml): delete ensemble/stacking/alpha360/optuna/incremental modules (single LightGBM)"
```

---

### Task 24: 删除顶层废弃模块 + qf_backtest

**Files:** Delete `regime_enhanced.py`、`rumi_strategy.py`、`rumi_optimizer.py`、`adaptive_strategy.py`、`grid_search.py`、`optimizer.py`、`self_learning.py`、`factor_eval.py`、`factor_model_optimizer/`、`qf_backtest/`；精简 `demo.py`（`enhanced_backtest/` 已在 Task 18 删除）

- [ ] **Step 1: 查引用**

Run: `grep -rln "aimoon.regime_enhanced\|aimoon.rumi_\|aimoon.adaptive_strategy\|aimoon.grid_search\|aimoon.optimizer\|aimoon.self_learning\|aimoon.factor_eval\|aimoon.factor_model_optimizer\|aimoon.qf_backtest" src/ tests/`

- [ ] **Step 2: 修复引用**（回测改指向 `aimoon.backtest`，其余删除引用）

- [ ] **Step 3: 删除文件**

```bash
git rm src/aimoon/regime_enhanced.py src/aimoon/rumi_strategy.py src/aimoon/rumi_optimizer.py src/aimoon/adaptive_strategy.py src/aimoon/grid_search.py src/aimoon/optimizer.py src/aimoon/self_learning.py src/aimoon/factor_eval.py
git rm -r src/aimoon/factor_model_optimizer src/aimoon/qf_backtest
```

- [ ] **Step 4: 精简 demo.py**

`demo.py` 改为仅生成合成 klines（供 `--demo` 训练），删除技术信号/demo 策略引用。

- [ ] **Step 5: 删除/改写测试**

删除 `tests/test_regime.py`、`tests/test_optimizer.py`、`tests/test_cli_subparsers.py` 中涉及被删命令的部分（改写）。

- [ ] **Step 6: 验证**

Run: `ruff check src/aimoon/ && python -c "import aimoon.backtest; import aimoon.screener; import aimoon.cli"`
Expected: 无错误

- [ ] **Step 7: 提交**

```bash
git add -A && git commit -m "refactor: delete regime/rumi/adaptive/qf_backtest (single backtest engine)"
```

---

## Phase 8 — 全量验证

### Task 25: ruff + mypy + 全量测试

- [ ] **Step 1: ruff**

Run: `ruff check src/aimoon`
Expected: 0 errors（修复残余 import/未用变量）

- [ ] **Step 2: black**

Run: `black --check src/aimoon`（若有差异 `black src/aimoon`）

- [ ] **Step 3: mypy**

Run: `mypy src/aimoon --ignore-missing-imports`
Expected: 0 errors（修复类型）

- [ ] **Step 4: 全量测试**

Run: `pytest tests/ -v`
Expected: 全部 PASS（目标覆盖率 80%+，运行 `pytest --cov=src/aimoon --cov-report=term-missing`）

- [ ] **Step 5: 端到端冒烟**

Run: `aimoon --demo`（训练单 LightGBM + 筛选）与 `aimoon backtest`
Expected: 正常完成，无崩溃；训练 ~20-40s，回测 ~5-15s

- [ ] **Step 6: 提交**

```bash
git add -A && git commit -m "test: green ruff/mypy/pytest after scoring redesign; smoke test passes"
```

---

## Self-Review 记录

- **Spec 覆盖**：11 因子（Task 3-6）、单 LightGBM（Task 10-12）、ML-only 评分（Task 13-14）、回测单引擎（Task 16-18）、激进删除（Task 21-24）—— 全部覆盖。北向资金因子缺失优雅降级（Task 6 测试 `test_northbound_absent_returns_nan`）。
- **占位符扫描**：每步含完整代码/命令/期望。无 TBD。
- **类型一致性**：`MLPredictor.predict_percentile`、`extract_features`、`train_model`、`run_backtest`、`BacktestResult` 签名在定义任务与调用任务中一致；`ASHARE_FACTORS` 11 项与 Task 3-6 实现的因子 id 一致。
- **已知风险**：Task 18 的 `compute_metrics` 签名需对照现有 `enhanced_backtest/metrics.py` 适配；Task 15/20 的 output/cli 改造依赖 Grep 结果，实施时按实际引用逐处处理。
