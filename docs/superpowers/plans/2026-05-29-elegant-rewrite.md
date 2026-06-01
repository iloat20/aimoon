# aimoon 架构重写实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 彻底重写 aimoon 为 Pythonic 简洁架构——函数优先、数据自解释、配置显式传递。

**Architecture:** 评分指标从类方法变为独立函数，每个返回 `Signal` 对象。数据层拆分为 spot/history/filters 三个模块。配置从全局单例变为 frozen dataclass 显式传递。CLI 精简为纯管道。

**Tech Stack:** Python 3.12+, pandas, akshare, rich, pyyaml, pytest

**设计文档:** `docs/superpowers/specs/2026-05-29-elegant-rewrite-design.md`

---

## 文件结构总览

**新建文件:**
- `src/aimoon/models.py` — Signal, ScoredStock
- `src/aimoon/data/__init__.py`
- `src/aimoon/data/spot.py` — get_spot()
- `src/aimoon/data/history.py` — get_kline()
- `src/aimoon/data/filters.py` — filter_universe(), filter_by_sectors(), filter_by_holdings()
- `src/aimoon/scoring/__init__.py` — SCORERS, collect_signals()
- `src/aimoon/scoring/trend.py`
- `src/aimoon/scoring/rsi.py`
- `src/aimoon/scoring/macd.py`
- `src/aimoon/scoring/kdj.py`
- `src/aimoon/scoring/volume.py`
- `src/aimoon/scoring/bollinger.py`
- `src/aimoon/scoring/momentum.py`
- `src/aimoon/scoring/sector.py`
- `src/aimoon/scoring/rps.py`
- `src/aimoon/screener.py` — screen_stock(), screen_universe()
- `src/aimoon/output.py` — OutputFormatter
- `src/aimoon/backtest.py` — BacktestEngine
- `src/aimoon/demo.py` — generate_demo()
- `src/aimoon/cli.py` — parse_args() + main()

**重写文件:**
- `src/aimoon/config.py` — Config frozen dataclass + load_config()
- `src/aimoon/cache.py` — DataCache（从 cache/provider.py 移出）
- `src/aimoon/indicators/technical.py` — TechInd（重命名 + 精简）
- `src/aimoon/result.py` — 保留 Ok/Err，添加 unwrap_or_exit()

**删除文件（最后一步）:**
- `src/aimoon/strategies/` 整个目录
- `src/aimoon/output/formatter.py`（移到 output.py）
- `src/aimoon/cache/provider.py`（移到 cache.py）
- `src/aimoon/data.py`（拆分为 data/ 目录）

**测试文件:**
- `tests/test_config.py`
- `tests/test_models.py`
- `tests/test_score_rsi.py`
- `tests/test_score_trend.py`
- `tests/test_score_macd.py`
- `tests/test_score_kdj.py`
- `tests/test_score_volume.py`
- `tests/test_score_bollinger.py`
- `tests/test_score_momentum.py`
- `tests/test_score_sector.py`
- `tests/test_rps.py`
- `tests/test_screener.py`
- `tests/test_filters.py`
- `tests/test_formatter.py`
- `tests/test_backtest.py`
- `tests/test_cli.py`

---

### Task 1: Config — frozen dataclass + load_config()

**Files:**
- Create: `src/aimoon/config.py`（覆盖现有）

- [ ] **Step 1: 写 config.py**

```python
"""配置模块 — frozen dataclass，显式传递，无全局单例"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    # 筛选参数
    history_days: int = 250
    min_market_cap_yi: float = 50.0
    max_market_cap_yi: float = 2000.0
    min_turnover_pct: float = 3.0
    max_turnover_pct: float = 30.0
    min_price: float = 5.0
    max_price: float = 100.0
    min_list_days: int = 250
    top_n: int = 30
    # 机构持仓
    min_northbound_cap: float = 1.0
    min_fund_pct: float = 5.0
    # 技术指标参数
    ma_short: int = 5
    ma_mid: int = 20
    ma_long: int = 60
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    kdj_period: int = 9
    boll_period: int = 20
    boll_std: float = 2.0
    volume_ma_period: int = 20
    # 缓存
    cache_dir: str = ".aimoon_cache"
    cache_ttl_hours: int = 4
    # 输出
    output_dir: str = "output"
    # CLI 参数
    no_csv: bool = False
    workers: int = 5
    demo: bool = False
    refresh: bool = False
    command: str | None = None
    stocks: str = "000001"
    hold_days: int = 5
    # 排除规则
    exclude_boards: tuple[str, ...] = ("ST", "退", "北交所")
    exclude_prefixes: tuple[str, ...] = ("8", "4")


def load_config(args: argparse.Namespace | None = None, path: str | None = None) -> Config:
    """合并配置：CLI 参数 > YAML 文件 > 默认值。"""
    overrides: dict = {}

    # YAML 文件
    if path:
        p = Path(path)
        if p.exists():
            try:
                import yaml
                with open(p, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                valid = {f.name for f in fields(Config)}
                tuple_fields = {f.name for f in fields(Config) if isinstance(f.default, tuple)}
                for k, v in data.items():
                    if k in valid:
                        overrides[k] = tuple(v) if k in tuple_fields and isinstance(v, list) else v
            except Exception as e:
                logger.warning("Failed to load config %s: %s", path, e)

    # CLI 参数覆盖
    if args:
        cli_map = {
            "top": "top_n", "workers": "workers", "no_csv": "no_csv",
            "demo": "demo", "refresh": "refresh", "config": "_config_path",
            "hold_days": "hold_days", "stocks": "stocks",
        }
        for cli_key, cfg_key in cli_map.items():
            if cli_key == "config":
                continue
            val = getattr(args, cli_key, None)
            if val is not None:
                overrides[cfg_key] = val
        if hasattr(args, "command") and args.command:
            overrides["command"] = args.command

    return Config(**overrides)
```

- [ ] **Step 2: 验证**

Run: `python -c "from aimoon.config import Config, load_config; c = Config(); print(c.top_n)"`
Expected: `30`

---

### Task 2: Models — Signal + ScoredStock

**Files:**
- Create: `src/aimoon/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: 写 test_models.py**

```python
"""Tests for core data models"""
from aimoon.models import Signal, ScoredStock


class TestSignal:
    def test_frozen(self) -> None:
        s = Signal("rsi_strong", "RSI强势", 2)
        assert s.name == "rsi_strong"
        assert s.score == 2

    def test_immutable(self) -> None:
        s = Signal("test", "test", 0)
        try:
            s.score = 1  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestScoredStock:
    def _stock(self, signals=()) -> ScoredStock:
        return ScoredStock(
            code="000001", name="Test", price=10.0,
            pct_change=1.0, turnover=5.0, signals=tuple(signals),
        )

    def test_total_score_sums_signals(self) -> None:
        s = self._stock([Signal("a", "A", 3), Signal("b", "B", -1)])
        assert s.total_score == 2

    def test_total_score_includes_rps(self) -> None:
        s = ScoredStock(
            code="000001", name="T", price=10.0,
            pct_change=0, turnover=0,
            signals=(Signal("a", "A", 2),),
            rps={"rps_score": 5},
        )
        assert s.total_score == 7

    def test_suggestion_thresholds(self) -> None:
        def stock_with_score(n: int) -> ScoredStock:
            return ScoredStock(
                code="0", name="T", price=0, pct_change=0, turnover=0,
                signals=(Signal("x", "X", n),),
            )
        assert stock_with_score(10).suggestion == ("强烈买入", "高")
        assert stock_with_score(6).suggestion == ("买入", "中高")
        assert stock_with_score(3).suggestion == ("建议买入", "中")
        assert stock_with_score(0).suggestion == ("观望", "低")
        assert stock_with_score(-2).suggestion == ("谨慎", "中")
        assert stock_with_score(-5).suggestion == ("建议卖出", "中高")
        assert stock_with_score(-8).suggestion == ("强烈卖出", "高")

    def test_empty_signals_score_zero(self) -> None:
        assert self._stock().total_score == 0
```

- [ ] **Step 2: Run tests, expect fail**

Run: `pytest tests/test_models.py -v`
Expected: ImportError

- [ ] **Step 3: 写 models.py**

```python
"""核心数据模型 — Signal 和 ScoredStock"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Signal:
    """一个评分信号。name 机器可读，label 人类可读。"""
    name: str
    label: str
    score: int


@dataclass(frozen=True)
class ScoredStock:
    """一只股票的完整评分结果。"""
    code: str
    name: str
    price: float
    pct_change: float
    turnover: float
    pe: float = 0.0
    pb: float = 0.0
    market_cap_yi: float = 0.0
    signals: tuple[Signal, ...] = ()
    rps: dict[str, float] = field(default_factory=dict)

    @property
    def total_score(self) -> int:
        signal_sum = sum(s.score for s in self.signals)
        rps_score = self.rps.get("rps_score", 0)
        return signal_sum + rps_score

    @property
    def suggestion(self) -> tuple[str, str]:
        """返回 (建议, 置信度)。"""
        t = self.total_score
        if t >= 8:   return "强烈买入", "高"
        if t >= 5:   return "买入", "中高"
        if t >= 2:   return "建议买入", "中"
        if t >= 0:   return "观望", "低"
        if t >= -3:  return "谨慎", "中"
        if t >= -6:  return "建议卖出", "中高"
        return "强烈卖出", "高"
```

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest tests/test_models.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/models.py tests/test_models.py
git commit -m "feat: add Signal and ScoredStock data models"
```

---

### Task 3: Result — 添加 unwrap_or_exit

**Files:**
- Modify: `src/aimoon/result.py`

- [ ] **Step 1: 给 Err 添加 unwrap_or_exit 方法**

在 `Err` 类中添加：

```python
    def unwrap_or_exit(self, msg: str = "") -> T:
        import sys
        print(f"[red]{msg or self.error}[/red]")
        sys.exit(1)
```

在 `Ok` 类中添加：

```python
    def unwrap_or_exit(self, msg: str = "") -> T:
        return self.value
```

- [ ] **Step 2: 验证**

Run: `python -c "from aimoon.result import Ok, Err; assert Ok(42).unwrap_or_exit() == 42; print('OK')"`
Expected: `OK`

---

### Task 4: Cache — 移到顶层

**Files:**
- Create: `src/aimoon/cache.py`（内容与 cache/provider.py 相同）

- [ ] **Step 1: 写 cache.py**

将 `src/aimoon/cache/provider.py` 的完整内容复制到 `src/aimoon/cache.py`，保持不变。

- [ ] **Step 2: 验证导入**

Run: `python -c "from aimoon.cache import DataCache; print('OK')"`
Expected: `OK`

---

### Task 5: TechInd — 重命名 + 保持缓存

**Files:**
- Modify: `src/aimoon/indicators/technical.py`

- [ ] **Step 1: 在文件末尾添加别名**

```python
# 向后兼容别名
TechInd = TechnicalIndicators
```

- [ ] **Step 2: 验证**

Run: `python -c "from aimoon.indicators.technical import TechInd; print('OK')"`
Expected: `OK`

---

### Task 6: Data Layer — spot.py, history.py, filters.py

**Files:**
- Create: `src/aimoon/data/__init__.py`
- Create: `src/aimoon/data/spot.py`
- Create: `src/aimoon/data/history.py`
- Create: `src/aimoon/data/filters.py`
- Create: `tests/test_filters.py`

- [ ] **Step 1: 写 data/__init__.py**

```python
"""数据获取层"""
from aimoon.data.spot import get_spot
from aimoon.data.history import get_kline
from aimoon.data.filters import filter_universe, filter_by_sectors, filter_by_holdings

__all__ = ["get_spot", "get_kline", "filter_universe", "filter_by_sectors", "filter_by_holdings"]
```

- [ ] **Step 2: 写 data/spot.py**

从现有 `data.py` 提取 `_em_get()`, `_em_fetch_all_pages()`, `get_spot_data()` 逻辑。关键变更：
- 接受 `Config` 参数而非读全局 `CONFIG`
- 移除错误的 `f3` 排序
- `_DEFAULT_HEADERS` 使用更新的 UA

```python
"""全市场实时行情 — 东财 API"""
from __future__ import annotations

import math
import random
import time

import pandas as pd
import requests

from aimoon.config import Config
from aimoon.result import Err, Ok, Result

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def _em_get(url: str, params: dict, timeout: int = 15, max_retries: int = 3) -> requests.Response:
    last_exc = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(1.0 * (2 ** attempt) + random.uniform(0.5, 1.5))
    raise last_exc  # type: ignore[misc]


def _em_fetch_all_pages(base_url: str, base_params: dict, timeout: int = 15) -> pd.DataFrame:
    r = _em_get(base_url, base_params, timeout=timeout)
    data = r.json()
    diff = data["data"]["diff"]
    if not diff:
        return pd.DataFrame()
    per_page = len(diff)
    total = data["data"]["total"]
    frames = [pd.DataFrame(diff)]
    for page in range(2, math.ceil(total / per_page) + 1):
        p = {**base_params, "pn": str(page)}
        time.sleep(random.uniform(0.3, 0.8))
        r = _em_get(base_url, p, timeout=timeout)
        frames.append(pd.DataFrame(r.json()["data"]["diff"]))
    return pd.concat(frames, ignore_index=True)


def get_spot(cfg: Config) -> Result[pd.DataFrame, str]:
    """从东财获取全市场实时行情。"""
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "100", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f12",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f26",
        }
        df = _em_fetch_all_pages(url, params)
        if df.empty:
            return Err("Empty spot data")
        df = df.rename(columns={
            "f12": "stock_code", "f14": "stock_name",
            "f2": "price", "f3": "pct_change",
            "f4": "change", "f5": "volume", "f6": "amount",
            "f7": "amplitude", "f8": "turnover",
            "f9": "pe", "f10": "volume_ratio",
            "f15": "high", "f16": "low",
            "f17": "open", "f18": "prev_close",
            "f20": "total_market_cap", "f21": "float_market_cap",
            "f23": "pb", "f24": "pct_60d", "f25": "pct_ytd",
            "f26": "listing_date",
        })
        return Ok(df)
    except Exception as e:
        return Err(f"Fetch spot data failed: {e}")
```

- [ ] **Step 3: 写 data/history.py**

从现有 `data.py` 提取 `get_history_kline()` 和 `_tencent_kline()`。接受 `DataCache` 参数而非读全局 `_cache`。

```python
"""历史 K 线 — AKShare + 腾讯备用"""
from __future__ import annotations

import logging
import random
import time
from datetime import date, timedelta

import akshare as ak
import pandas as pd
import requests

from aimoon.cache import DataCache
from aimoon.result import Err, Ok, Result

logger = logging.getLogger(__name__)


def _tencent_kline(stock_code: str, days: int) -> Result[pd.DataFrame, str]:
    prefix = "sh" if stock_code.startswith("6") else "sz"
    secid = prefix + stock_code
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{secid},day,,,{days},qfq"}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        data = r.json()
        if data.get("code") != 0:
            return Err(f"{stock_code}: Tencent API error")
        inner = data["data"].get(secid, {})
        key = "day" if "day" in inner else "qfqday"
        klines = inner.get(key, [])
        if not klines:
            return Err(f"{stock_code}: empty Tencent data")
        rows = [{"date": k[0], "open": float(k[1]), "close": float(k[2]),
                 "high": float(k[3]), "low": float(k[4]), "volume": float(k[5])} for k in klines]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        for col in ("amount", "amplitude", "pct_change", "change", "turnover"):
            df[col] = 0.0
        return Ok(df)
    except Exception as e:
        return Err(f"{stock_code}: Tencent fallback failed: {e}")


def get_kline(code: str, days: int, cache: DataCache) -> Result[pd.DataFrame, str]:
    """获取历史 K 线。AKShare 优先，腾讯备用，带缓存。"""
    cached = cache.get(code)
    if cached is not None:
        return Ok(cached)

    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq",
        )
        if df is None or df.empty:
            return Err(f"{code}: no data")
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude",
            "涨跌幅": "pct_change", "涨跌额": "change", "换手率": "turnover",
        })
        df["date"] = pd.to_datetime(df["date"])
        result_df = df.set_index("date").sort_index()
        cache.put(code, result_df)
        return Ok(result_df)
    except Exception as e:
        logger.warning("AKShare kline failed for %s: %s, trying Tencent", code, e)
        result = _tencent_kline(code, days)
        if result.is_ok():
            cache.put(code, result.unwrap())
        return result
```

- [ ] **Step 4: 写 data/filters.py**

合并 `filter_by_spot`, `filter_stock_list`, `_apply_inline_filters`, 以及板块/持仓过滤逻辑。

```python
"""数据过滤 — 纯函数，无副作用"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import akshare as ak
import pandas as pd
import requests

from aimoon.config import Config
from aimoon.data.spot import _DEFAULT_HEADERS

logger = logging.getLogger(__name__)

# 板块数据内存缓存
_sector_cache: dict[str, tuple[float, object]] = {}
_SECTOR_CACHE_TTL = 1800


def filter_universe(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """基础过滤：市值、换手率、价格、上市日期、排除规则。"""
    df = df.copy()
    for col in ("price", "turnover", "total_market_cap", "float_market_cap", "pe", "pb"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["price", "turnover", "total_market_cap"])
    cap_yi = df["total_market_cap"] / 1e8
    mask = (
        (cap_yi >= cfg.min_market_cap_yi) & (cap_yi <= cfg.max_market_cap_yi)
        & (df["turnover"] >= cfg.min_turnover_pct) & (df["turnover"] <= cfg.max_turnover_pct)
        & (df["price"] >= cfg.min_price) & (df["price"] <= cfg.max_price)
    )
    if "listing_date" in df.columns:
        cutoff = (date.today() - timedelta(days=cfg.min_list_days)).strftime("%Y%m%d")
        ld = pd.to_numeric(df["listing_date"], errors="coerce")
        mask = mask & ld.notna() & (ld.astype(int).astype(str) <= cutoff)
    # 排除规则
    for prefix in cfg.exclude_prefixes:
        mask &= ~df["stock_code"].str.startswith(prefix)
    for board in cfg.exclude_boards:
        mask &= ~df["stock_name"].str.contains(board, na=False)
    return df[mask].reset_index(drop=True)


def filter_by_sectors(df: pd.DataFrame, top_pct: float = 5.0) -> tuple[pd.DataFrame, dict]:
    """板块过滤：找强势板块 → 只保留成分股。返回 (filtered, market_context)。"""
    try:
        sector_df = ak.stock_board_industry_name_em()
        if sector_df is None or sector_df.empty:
            return df, {}
        name_col = "板块名称" if "板块名称" in sector_df.columns else "name"
        change_col = next((c for c in ("涨跌幅", "涨幅") if c in sector_df.columns), None)
        if change_col is None:
            return df, {}
        sector_df[change_col] = pd.to_numeric(sector_df[change_col], errors="coerce")
        sector_df = sector_df.dropna(subset=[change_col]).sort_values(change_col, ascending=False)
        n_top = max(1, int(len(sector_df) * top_pct / 100))
        top_names = sector_df[name_col].head(n_top).tolist()

        # 获取成分股
        sector_map: dict[str, str] = {}
        for name in top_names:
            try:
                cons = ak.stock_board_industry_cons_em(symbol=name)
                if cons is not None and not cons.empty:
                    code_col = "代码" if "代码" in cons.columns else "code"
                    for code in cons[code_col].tolist():
                        sector_map[str(code)] = name
            except Exception:
                continue

        if not sector_map:
            return df, {}
        filtered = df[df["stock_code"].isin(set(sector_map.keys()))].reset_index(drop=True)

        # 构建 market_context
        ctx: dict = {"sector_map": sector_map, "top_pct": top_pct}
        df_copy = df.copy()
        df_copy["pct_60d"] = pd.to_numeric(df_copy.get("pct_60d", 0), errors="coerce").fillna(0)
        df_copy["sector"] = df_copy["stock_code"].map(sector_map)
        sector_returns = df_copy.dropna(subset=["sector"]).groupby("sector")["pct_60d"].mean().to_dict()
        ctx["sector_returns"] = sector_returns
        sorted_sectors = sorted(sector_returns.items(), key=lambda x: x[1], reverse=True)
        ctx["top_sectors"] = {n for n, _ in sorted_sectors[:n_top]}
        threshold = df_copy["pct_60d"].quantile(1 - top_pct / 100)
        ctx["top_stocks"] = set(df_copy[df_copy["pct_60d"] >= threshold]["stock_code"].tolist())

        return filtered, ctx
    except Exception as e:
        logger.warning("Sector filter failed: %s", e)
        return df, {}


def filter_by_holdings(df: pd.DataFrame, cfg: Config, spot: pd.DataFrame) -> pd.DataFrame:
    """机构持仓过滤：北向 + 基金。"""
    # 北向
    nb = _get_northbound(cfg.min_northbound_cap)
    if nb:
        df = df[df["stock_code"].isin(nb)].reset_index(drop=True)
    # 基金
    ff = _get_fund_holdings(cfg.min_fund_pct, spot)
    if ff:
        df = df[df["stock_code"].isin(ff)].reset_index(drop=True)
    return df


def _get_northbound(min_cap_yi: float) -> set[str]:
    codes: set[str] = set()
    min_cap = min_cap_yi * 1e8
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        page = 1
        while True:
            params = {
                "sortColumns": "HOLD_MARKET_CAP", "sortTypes": "-1",
                "pageSize": "500", "pageNumber": str(page),
                "reportName": "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
                "columns": "SECURITY_CODE,HOLD_MARKET_CAP",
                "source": "WEB", "client": "WEB",
            }
            r = requests.get(url, params=params, timeout=15, headers=_DEFAULT_HEADERS)
            data = r.json()
            if not data.get("success") or not data.get("result") or not data["result"].get("data"):
                break
            items = data["result"]["data"]
            for item in items:
                cap = float(item.get("HOLD_MARKET_CAP", 0) or 0)
                if cap >= min_cap:
                    codes.add(str(item.get("SECURITY_CODE", "")))
            if len(items) < 500:
                break
            page += 1
    except Exception as e:
        logger.warning("Northbound holdings fetch failed: %s", e)
    return codes


def _get_fund_holdings(min_pct: float, spot: pd.DataFrame) -> set[str]:
    try:
        today = date.today()
        quarters = [(12, 31), (9, 30), (6, 30), (3, 31)]
        report_date = next(
            (date(today.year, m, d).strftime("%Y%m%d") for m, d in quarters if date(today.year, m, d) <= today),
            f"{today.year - 1}1231",
        )
        df = ak.stock_report_fund_hold(symbol="基金持仓", date=report_date)
        if df is None or df.empty:
            return set()
        cols = df.columns.tolist()
        code_col = next((c for c in cols if "代码" in str(c)), cols[1] if len(cols) > 1 else None)
        shares_col = next((c for c in cols if "数量" in str(c) or "持股" in str(c)), cols[4] if len(cols) > 4 else None)
        if code_col is None or shares_col is None:
            return set()
        df = df[[code_col, shares_col]].copy()
        df.columns = ["stock_code", "held_shares"]
        df["held_shares"] = pd.to_numeric(df["held_shares"], errors="coerce").fillna(0)
        if spot is None or spot.empty:
            return set()
        cap = spot[["stock_code", "float_market_cap", "price"]].copy()
        cap["float_market_cap"] = pd.to_numeric(cap["float_market_cap"], errors="coerce")
        cap["price"] = pd.to_numeric(cap["price"], errors="coerce")
        cap = cap.dropna(subset=["float_market_cap", "price"])
        cap = cap[cap["price"] > 0]
        cap["float_shares"] = cap["float_market_cap"] / cap["price"]
        df = df.merge(cap[["stock_code", "float_shares"]], on="stock_code", how="inner")
        df["pct"] = df["held_shares"] / df["float_shares"] * 100
        return set(df[df["pct"] >= min_pct]["stock_code"].tolist())
    except Exception as e:
        logger.warning("Fund holdings fetch failed: %s", e)
        return set()
```

- [ ] **Step 5: 写 tests/test_filters.py**

```python
"""Tests for data filters"""
import pandas as pd
import pytest
from aimoon.config import Config
from aimoon.data.filters import filter_universe


@pytest.fixture
def spot_df() -> pd.DataFrame:
    return pd.DataFrame({
        "stock_code": ["000001", "000002", "600519", "800001", "400001"],
        "stock_name": ["Test1", "ST_Test", "Test3", "Test4", "Test5"],
        "price": [10.0, 50.0, 150.0, 30.0, 20.0],
        "turnover": [5.0, 10.0, 3.0, 8.0, 6.0],
        "total_market_cap": [1e10, 5e10, 1e12, 2e10, 1e10],
        "float_market_cap": [5e9, 3e10, 5e11, 1e10, 5e9],
    })


class TestFilterUniverse:
    def test_filters_by_market_cap(self, spot_df: pd.DataFrame) -> None:
        cfg = Config()
        result = filter_universe(spot_df, cfg)
        assert len(result) == 2  # 000001 and 000002 pass (600519 too large, 800001/400001 excluded)

    def test_filters_by_price(self) -> None:
        cfg = Config()
        df = pd.DataFrame({
            "stock_code": ["000001"], "stock_name": ["Test"],
            "price": [2.0], "turnover": [5.0],
            "total_market_cap": [1e10], "float_market_cap": [5e9],
        })
        assert len(filter_universe(df, cfg)) == 0

    def test_excludes_st(self, spot_df: pd.DataFrame) -> None:
        cfg = Config()
        result = filter_universe(spot_df, cfg)
        assert not any("ST" in n for n in result["stock_name"].tolist())

    def test_excludes_prefixes(self, spot_df: pd.DataFrame) -> None:
        cfg = Config()
        result = filter_universe(spot_df, cfg)
        assert not any(c.startswith("8") or c.startswith("4") for c in result["stock_code"].tolist())
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_filters.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/aimoon/data/ tests/test_filters.py
git commit -m "feat: add data layer — spot, history, filters"
```

---

### Task 7: Scoring Functions — 8 个信号模块

**Files:**
- Create: `src/aimoon/scoring/__init__.py`
- Create: `src/aimoon/scoring/trend.py`
- Create: `src/aimoon/scoring/rsi.py`
- Create: `src/aimoon/scoring/macd.py`
- Create: `src/aimoon/scoring/kdj.py`
- Create: `src/aimoon/scoring/volume.py`
- Create: `src/aimoon/scoring/bollinger.py`
- Create: `src/aimoon/scoring/momentum.py`
- Create: `src/aimoon/scoring/sector.py`
- Create: `src/aimoon/scoring/rps.py`
- Create: `tests/test_score_rsi.py`, `tests/test_score_trend.py`, `tests/test_score_macd.py`, etc.

所有 scorer 统一签名 `score_xxx(ti, *, code="", ctx=None)`。以下给出每个文件完整代码：

- [ ] **Step 1: 写 scoring/trend.py**

```python
"""均线趋势 + 金叉/死叉"""
from __future__ import annotations
import pandas as pd
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_trend(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> list[Signal]:
    signals: list[Signal] = []
    trend = ti.ma_trend()
    if trend == "bullish":
        signals.append(Signal("trend_bullish", "均线多头排列", +2))
    elif trend == "bearish":
        signals.append(Signal("trend_bearish", "均线空头排列", -2))
    if ti.ma_golden_cross():
        signals.append(Signal("ma_golden", "MA金叉", +2))
    if ti.ma_death_cross():
        signals.append(Signal("ma_death", "MA死叉", -2))
    return signals
```

- [ ] **Step 2: 写 scoring/rsi.py**

```python
"""RSI 多空信号"""
from __future__ import annotations
import pandas as pd
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_rsi(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> Signal | None:
    val = ti.rsi().iloc[-1]
    if pd.isna(val):
        return None
    if val > 60:
        return Signal("rsi_strong", f"RSI强势({val:.0f})", +2)
    if val > 50:
        return Signal("rsi_bullish", f"RSI偏多({val:.0f})", +1)
    if val < 40:
        return Signal("rsi_weak", f"RSI弱势({val:.0f})", -2)
    if val < 50:
        return Signal("rsi_bearish", f"RSI偏空({val:.0f})", -1)
    return None
```

- [ ] **Step 3: 写 scoring/macd.py**

```python
"""MACD 金叉/死叉 + 零轴位置"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_macd(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> list[Signal]:
    signals: list[Signal] = []
    if ti.macd_golden_cross():
        signals.append(Signal("macd_golden", "MACD金叉", +2))
    if ti.macd_death_cross():
        signals.append(Signal("macd_death", "MACD死叉", -2))
    if ti.macd_above_zero():
        signals.append(Signal("macd_above_zero", "MACD零轴上方", +1))
    else:
        signals.append(Signal("macd_below_zero", "MACD零轴下方", -1))
    return signals
```

- [ ] **Step 4: 写 scoring/kdj.py**

```python
"""KDJ 金叉/死叉 + 超买超卖"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_kdj(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> list[Signal]:
    signals: list[Signal] = []
    if ti.kdj_golden_cross():
        signals.append(Signal("kdj_golden", "KDJ金叉", +1))
    if ti.kdj_death_cross():
        signals.append(Signal("kdj_death", "KDJ死叉", -1))
    if ti.kdj_oversold():
        signals.append(Signal("kdj_oversold", "KDJ超卖", +1))
    if ti.kdj_overbought():
        signals.append(Signal("kdj_overbought", "KDJ超买", -1))
    return signals
```

- [ ] **Step 5: 写 scoring/volume.py**

```python
"""成交量信号"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_volume(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> Signal | None:
    vr = ti.volume_ratio()
    if vr > 2.0:
        return Signal("volume_surge", f"放量({vr:.1f}x)", +2)
    if vr > 1.5:
        return Signal("volume_mild", f"温和放量({vr:.1f}x)", +1)
    if vr < 0.5:
        return Signal("volume_shrink", f"缩量({vr:.1f}x)", -1)
    return None
```

- [ ] **Step 6: 写 scoring/bollinger.py**

```python
"""布林带位置信号"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_bollinger(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> Signal | None:
    pos = ti.bollinger_position()
    if pos == "below":
        return Signal("boll_below", "触及布林下轨", +1)
    if pos == "above":
        return Signal("boll_above", "触及布林上轨", -1)
    return None
```

- [ ] **Step 7: 写 scoring/momentum.py**

```python
"""动量信号 — ROC + 新高/新低 + ADX"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_momentum(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> list[Signal]:
    signals: list[Signal] = []

    # 多周期 ROC
    for period, weight in [(5, 4), (10, 2), (20, 1)]:
        val = ti.roc_signal(period)
        if val > 5:
            signals.append(Signal(f"roc{period}_strong", f"ROC{period}强势({val:+.1f}%)", +weight))
        elif val > 2:
            signals.append(Signal(f"roc{period}_up", f"ROC{period}上升({val:+.1f}%)", +(weight // 2 or 1)))
        elif val < -5:
            signals.append(Signal(f"roc{period}_weak", f"ROC{period}弱势({val:+.1f}%)", -weight))
        elif val < -2:
            signals.append(Signal(f"roc{period}_down", f"ROC{period}下降({val:+.1f}%)", -(weight // 2 or 1)))

    # 动量加速度
    accel = ti.momentum_acceleration(5, 20)
    if accel > 3:
        signals.append(Signal("accel_fast", "动量加速", +3))
    elif accel > 0:
        signals.append(Signal("accel_mild", "动量偏强", +1))
    elif accel < -3:
        signals.append(Signal("decel_fast", "动量减速", -3))
    elif accel < 0:
        signals.append(Signal("decel_mild", "动量偏弱", -1))

    # 新高/新低
    for days, weight in [(5, 3), (10, 2), (20, 1)]:
        if ti.new_high(days):
            signals.append(Signal(f"high_{days}d", f"{days}日新高", +weight))
            break
    for days, weight in [(5, 3), (10, 2), (20, 1)]:
        if ti.new_low(days):
            signals.append(Signal(f"low_{days}d", f"{days}日新低", -weight))
            break

    # ADX 趋势强度
    if ti.adx(14) > 25:
        signals.append(Signal("adx_strong", "ADX强趋势", +2))

    return signals
```

- [ ] **Step 8: 写 scoring/sector.py**

```python
"""板块动量信号"""
from __future__ import annotations
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_sector(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> Signal | None:
    if not ctx:
        return None
    top_pct = ctx.get("top_pct", 5)
    sector_map = ctx.get("sector_map", {})
    top_sectors = ctx.get("top_sectors", set())
    sector = sector_map.get(code)
    if sector and sector in top_sectors:
        return Signal("sector_top", f"强势板块(Top{top_pct}%)", +3)
    return None
```

- [ ] **Step 9: 写 scoring/rps.py**

```python
"""RPS（相对价格强度）计算"""
from __future__ import annotations
import pandas as pd
from aimoon.models import Signal, ScoredStock


def compute_rps(results: list[ScoredStock], tails: dict[str, pd.DataFrame]) -> list[ScoredStock]:
    """计算 RPS 并返回新的 ScoredStock 列表（不可变更新）。"""
    if not results:
        return results

    returns: dict[int, dict[str, float]] = {5: {}, 10: {}, 15: {}, 20: {}}
    for r in results:
        tail = tails.get(r.code)
        if tail is None or len(tail) < 21:
            continue
        close = pd.to_numeric(tail["close"], errors="coerce")
        for n in returns:
            if len(close) > n:
                prev = close.iloc[-n - 1]
                if prev > 0:
                    returns[n][r.code] = (close.iloc[-1] - prev) / prev * 100

    # 计算排名百分位
    rank_maps: dict[str, dict[str, float]] = {}
    for n, ret_map in returns.items():
        if not ret_map:
            continue
        sorted_codes = sorted(ret_map, key=lambda c: ret_map[c])
        total = len(sorted_codes)
        rank_maps[f"rps{n}"] = {code: (rank + 1) / total * 100 for rank, code in enumerate(sorted_codes)}

    # 构建新结果
    updated: list[ScoredStock] = []
    for r in results:
        rps = {key: rank_maps[key][r.code] for key in rank_maps if r.code in rank_maps[key]}
        rps_red = sum(1 for v in rps.values() if v > 90)
        if rps_red >= 3:
            rps["rps_score"] = 5
        elif rps_red >= 2:
            rps["rps_score"] = 3
        else:
            rps["rps_score"] = 0

        # 添加 RPS 信号到 signals
        rps_signals = list(r.signals)
        if rps_red >= 3:
            rps_signals.append(Signal("rps_triple", f"RPS三线翻红({rps_red}/4)", +5))
        elif rps_red >= 2:
            rps_signals.append(Signal("rps_double", f"RPS双线红({rps_red}/4)", +3))

        updated.append(ScoredStock(
            code=r.code, name=r.name, price=r.price,
            pct_change=r.pct_change, turnover=r.turnover,
            pe=r.pe, pb=r.pb, market_cap_yi=r.market_cap_yi,
            signals=tuple(rps_signals), rps=rps,
        ))
    return updated
```

- [ ] **Step 10: 写 scoring/__init__.py**

```python
"""评分函数注册表"""
from __future__ import annotations
from typing import Callable, Union
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal
from aimoon.scoring.trend import score_trend
from aimoon.scoring.rsi import score_rsi
from aimoon.scoring.macd import score_macd
from aimoon.scoring.kdj import score_kdj
from aimoon.scoring.volume import score_volume
from aimoon.scoring.bollinger import score_bollinger
from aimoon.scoring.momentum import score_momentum
from aimoon.scoring.sector import score_sector

Scorer = Callable[..., Union[Signal, list[Signal], None]]

SCORERS: list[Scorer] = [
    score_trend, score_rsi, score_macd, score_kdj,
    score_volume, score_bollinger, score_momentum, score_sector,
]


def collect_signals(ti: TechInd, code: str = "", ctx: dict | None = None) -> list[Signal]:
    """运行所有评分函数，收集非空信号。"""
    signals: list[Signal] = []
    for scorer in SCORERS:
        result = scorer(ti, code=code, ctx=ctx)
        if result is None:
            continue
        signals.extend(result if isinstance(result, list) else [result])
    return signals
```

- [ ] **Step 11: 写评分测试（以 test_score_rsi.py 为例，其余类似）**

```python
"""Tests for RSI scoring"""
import numpy as np
import pandas as pd
from aimoon.indicators.technical import TechInd
from aimoon.scoring.rsi import score_rsi


def _make_ti(prices: list[float]) -> TechInd:
    n = len(prices)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = np.array(prices, dtype=float)
    return TechInd(pd.DataFrame({
        "open": close, "close": close, "high": close, "low": close,
        "volume": np.full(n, 1e6), "turnover": np.full(n, 5.0), "pct_change": np.zeros(n),
    }, index=dates))


class TestScoreRsi:
    def test_strong_uptrend(self) -> None:
        ti = _make_ti(list(np.linspace(10, 30, 100)))
        sig = score_rsi(ti)
        assert sig is not None
        assert sig.name == "rsi_strong"
        assert sig.score == 2

    def test_strong_downtrend(self) -> None:
        ti = _make_ti(list(np.linspace(30, 10, 100)))
        sig = score_rsi(ti)
        assert sig is not None
        assert sig.name == "rsi_weak"
        assert sig.score == -2

    def test_flat_returns_none(self) -> None:
        ti = _make_ti([10.0] * 100)
        sig = score_rsi(ti)
        # RSI of flat data = 50, which returns None
        assert sig is None
```

其余评分测试文件模式相同——用 `_make_ti()` 构造数据，断言 `Signal.name` 和 `Signal.score`。

- [ ] **Step 12: Run all scoring tests**

Run: `pytest tests/test_score_*.py -v`
Expected: all PASS

- [ ] **Step 13: Commit**

```bash
git add src/aimoon/scoring/ tests/test_score_*.py
git commit -m "feat: add scoring functions — 8 signal modules + RPS"
```

---

### Task 8: Screener — screen_stock + screen_universe

**Files:**
- Create: `src/aimoon/screener.py`
- Create: `tests/test_screener.py`

- [ ] **Step 1: 写 screener.py**

```python
"""股票筛选器 — 组合评分函数"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.history import get_kline
from aimoon.indicators.technical import TechInd
from aimoon.models import ScoredStock
from aimoon.scoring import collect_signals

logger = logging.getLogger(__name__)


def screen_stock(
    code: str, name: str, kline: pd.DataFrame,
    spot_row: pd.Series | None = None, ctx: dict | None = None,
) -> ScoredStock | None:
    """对单只股票评分。数据不足返回 None。"""
    if kline is None or len(kline) < 60:
        return None
    try:
        ti = TechInd(kline)
    except Exception:
        return None
    signals = collect_signals(ti, code=code, ctx=ctx)
    if not signals:
        return None

    price = float(kline["close"].iloc[-1])
    pct = float(kline["pct_change"].iloc[-1]) if "pct_change" in kline.columns else 0.0
    turnover = float(kline["turnover"].iloc[-1]) if "turnover" in kline.columns else 0.0
    pe = _safe_float(spot_row, "pe")
    pb = _safe_float(spot_row, "pb")
    cap = _safe_float(spot_row, "total_market_cap") / 1e8 if spot_row is not None else 0.0

    return ScoredStock(
        code=code, name=name, price=price,
        pct_change=pct, turnover=turnover,
        pe=pe, pb=pb, market_cap_yi=cap,
        signals=tuple(signals),
    )


def screen_universe(
    universe: pd.DataFrame, cfg: Config,
    cache: DataCache, ctx: dict | None = None,
    klines: dict[str, pd.DataFrame] | None = None,
) -> tuple[list[ScoredStock], dict[str, pd.DataFrame]]:
    """并发评分候选池。返回 (results, kline_tails)。"""
    results: list[ScoredStock] = []
    tails: dict[str, pd.DataFrame] = {}

    def _process(row: pd.Series) -> None:
        code = row["stock_code"]
        name = row["stock_name"]
        kdf = (klines or {}).get(code)
        if kdf is None:
            r = get_kline(code, cfg.history_days, cache)
            if r.is_err():
                return
            kdf = r.unwrap()
        spot_row = row if "pe" in row.index else None
        scored = screen_stock(code, name, kdf, spot_row, ctx)
        if scored:
            results.append(scored)
            tails[code] = kdf.tail(25).copy()

    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        futures = {ex.submit(_process, row): row["stock_code"] for _, row in universe.iterrows()}
        done = 0
        total = len(futures)
        for fut in as_completed(futures):
            done += 1
            try:
                fut.result()
            except Exception as e:
                logger.warning("Screen failed: %s", e)

    return results, tails


def _safe_float(row: pd.Series | None, key: str) -> float:
    if row is not None and key in row.index and pd.notna(row[key]):
        return float(row[key])
    return 0.0
```

- [ ] **Step 2: 写 tests/test_screener.py**

```python
"""Tests for screener"""
import numpy as np
import pandas as pd
from aimoon.models import ScoredStock
from aimoon.screener import screen_stock


def _uptrend_kline(n: int = 100) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = np.linspace(10, 30, n)
    return pd.DataFrame({
        "open": close * 0.99, "close": close, "high": close * 1.01, "low": close * 0.99,
        "volume": np.full(n, 1e6), "turnover": np.full(n, 5.0), "pct_change": np.zeros(n),
    }, index=dates)


class TestScreenStock:
    def test_returns_scored_stock(self) -> None:
        result = screen_stock("000001", "Test", _uptrend_kline())
        assert result is not None
        assert isinstance(result, ScoredStock)
        assert result.code == "000001"
        assert result.total_score > 0

    def test_short_data_returns_none(self) -> None:
        df = pd.DataFrame({"close": [10.0] * 10})
        assert screen_stock("000001", "Test", df) is None

    def test_signals_are_frozen(self) -> None:
        result = screen_stock("000001", "Test", _uptrend_kline())
        assert result is not None
        assert isinstance(result.signals, tuple)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_screener.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/screener.py tests/test_screener.py
git commit -m "feat: add screener — screen_stock and screen_universe"
```

---

### Task 9: Output + Demo + Backtest

**Files:**
- Create: `src/aimoon/output.py`
- Create: `src/aimoon/demo.py`
- Create: `src/aimoon/backtest.py`
- Create: `tests/test_formatter.py`
- Create: `tests/test_backtest.py`

- [ ] **Step 1: 写 output.py**

将现有 `output/formatter.py` 重写为接受 `list[ScoredStock]`。关键变更：
- `display()` 接受 `list[ScoredStock]` 而非 `list[SignalScore]`
- 信号展示用 `signal.label`
- 建议从 `scored.suggestion` property 获取

```python
"""输出格式化 — Rich 表格 + CSV + Markdown"""
from __future__ import annotations
import csv
import os
from datetime import datetime
import pandas as pd
from rich.console import Console
from rich.table import Table
from aimoon.config import Config
from aimoon.models import ScoredStock


class OutputFormatter:
    def __init__(self, cfg: Config | None = None) -> None:
        self.console = Console()
        self.cfg = cfg or Config()

    def display(self, results: list[ScoredStock]) -> None:
        if not results:
            self.console.print("[yellow]No stocks match the criteria[/yellow]")
            return
        table = Table(title=f"A-Share Quant Screen ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        table.add_column("No.", style="dim", width=4)
        table.add_column("Code", style="cyan", width=8)
        table.add_column("Name", style="bold", width=10)
        table.add_column("Price", justify="right", width=8)
        table.add_column("Chg%", justify="right", width=8)
        table.add_column("Score", justify="right", width=6)
        table.add_column("Suggestion", width=10)
        table.add_column("Conf.", width=6)
        table.add_column("Signals", width=30)
        for i, r in enumerate(results, 1):
            ps = "green" if r.pct_change >= 0 else "red"
            ts = "bold green" if r.total_score >= 4 else ("yellow" if r.total_score >= 0 else "red")
            sug, conf = r.suggestion
            ss = "bold green" if "买" in sug else ("red" if "卖" in sug else "dim")
            table.add_row(
                str(i), r.code, r.name, f"{r.price:.2f}",
                f"[{ps}]{r.pct_change:+.2f}[/{ps}]",
                f"[{ts}]{r.total_score}[/{ts}]",
                f"[{ss}]{sug}[/{ss}]", conf,
                " | ".join(s.label for s in r.signals) if r.signals else "-",
            )
        self.console.print(table)
        self.console.print(f"\n[dim]Total: {len(results)} stocks[/dim]")

    def export_csv(self, results: list[ScoredStock], filename: str | None = None) -> str:
        if not filename:
            filename = f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        filepath = os.path.join(self.cfg.output_dir, filename)
        rows = []
        for r in results:
            sug, conf = r.suggestion
            rows.append({
                "code": r.code, "name": r.name, "price": r.price,
                "pct_change": r.pct_change, "turnover": r.turnover,
                "pe": r.pe, "pb": r.pb, "market_cap_yi": r.market_cap_yi,
                "total_score": r.total_score, "suggestion": sug, "confidence": conf,
                "signals": " | ".join(s.label for s in r.signals),
                **r.rps,
            })
        pd.DataFrame(rows).to_csv(filepath, index=False, encoding="utf-8-sig")
        return filepath

    def export_markdown(self, results: list[ScoredStock], filename: str | None = None) -> str:
        if not filename:
            filename = f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        filepath = os.path.join(self.cfg.output_dir, filename)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"# A股量化筛选结果 {now}", "", f"共筛选 {len(results)} 只股票", ""]
        lines += ["| No. | Code | Name | Price | Score | Suggestion | Conf. | Signals |",
                  "|-----|------|------|-------|-------|------------|-------|---------|"]
        for i, r in enumerate(results, 1):
            sug, conf = r.suggestion
            sigs = " / ".join(s.label for s in r.signals).replace("|", "\\|") if r.signals else "-"
            lines.append(f"| {i} | {r.code} | {r.name} | {r.price:.2f} | {r.total_score} | {sug} | {conf} | {sigs} |")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return filepath
```

- [ ] **Step 2: 写 demo.py**（从现有 cli.py 中的 generate_demo 提取，保持不变）

- [ ] **Step 3: 写 backtest.py**

重写为使用 `screen_stock` 返回 `ScoredStock`：

```python
"""回测引擎"""
from __future__ import annotations
import logging
from dataclasses import dataclass
import pandas as pd
from aimoon.config import Config
from aimoon.screener import screen_stock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeRecord:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float


@dataclass(frozen=True)
class BacktestResult:
    code: str
    total_return: float
    win_rate: float
    max_drawdown: float
    trade_count: int
    trades: tuple[TradeRecord, ...]


class BacktestEngine:
    def __init__(self, cfg: Config, hold_days: int = 5) -> None:
        self.cfg = cfg
        self.hold_days = hold_days

    def run(self, code: str, name: str, kline: pd.DataFrame) -> BacktestResult:
        min_window = self.cfg.ma_long
        if len(kline) < min_window + self.hold_days:
            return BacktestResult(code, 0.0, 0.0, 0.0, 0, ())

        trades: list[TradeRecord] = []
        dates = kline.index.tolist()
        in_trade = False
        exit_idx = 0

        for i in range(min_window, len(kline) - self.hold_days):
            if in_trade and i < exit_idx:
                continue
            in_trade = False
            window = kline.iloc[:i + 1]
            scored = screen_stock(code, name, window)
            if scored is None or scored.total_score < 2:
                continue
            entry_price = float(kline["close"].iloc[i])
            exit_i = min(i + self.hold_days, len(kline) - 1)
            exit_price = float(kline["close"].iloc[exit_i])
            ret = (exit_price - entry_price) / entry_price * 100
            trades.append(TradeRecord(str(dates[i].date()), str(dates[exit_i].date()), entry_price, exit_price, ret))
            in_trade = True
            exit_idx = exit_i + 1

        return self._metrics(code, trades, kline)

    def _metrics(self, code: str, trades: list[TradeRecord], kline: pd.DataFrame) -> BacktestResult:
        if not trades:
            return BacktestResult(code, 0.0, 0.0, 0.0, 0, ())
        total_ret = sum(t.return_pct for t in trades)
        win_rate = sum(1 for t in trades if t.return_pct > 0) / len(trades)
        equity = [100.0]
        trade_idx = 0
        for i in range(1, len(kline)):
            if trade_idx < len(trades) and str(kline.index[i].date()) == trades[trade_idx].exit_date:
                equity.append(equity[-1] * (1 + trades[trade_idx].return_pct / 100))
                trade_idx += 1
            else:
                equity.append(equity[-1])
        peak = max_dd = 0.0
        for v in equity:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak if peak > 0 else 0)
        return BacktestResult(code, total_ret, win_rate, max_dd, len(trades), tuple(trades))
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_formatter.py tests/test_backtest.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/output.py src/aimoon/demo.py src/aimoon/backtest.py tests/test_formatter.py tests/test_backtest.py
git commit -m "feat: add output formatter, demo, and backtest engine"
```

---

### Task 10: CLI — 管道入口

**Files:**
- Create: `src/aimoon/cli.py`（覆盖现有）
- Create: `src/aimoon/__main__.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: 写 cli.py**

```python
"""CLI 入口 — 薄管道"""
from __future__ import annotations
import argparse
import logging
import sys
import time

from aimoon.cache import DataCache
from aimoon.config import Config, load_config
from aimoon.data import get_spot, filter_universe, filter_by_sectors, filter_by_holdings
from aimoon.models import ScoredStock
from aimoon.output import OutputFormatter
from aimoon.scoring.rps import compute_rps
from aimoon.screener import screen_universe

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A-share quant screener")
    p.add_argument("--config", type=str, default=None)
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--no-csv", action="store_true")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--refresh", action="store_true")
    sub = p.add_subparsers(dest="command")
    bt = sub.add_parser("backtest")
    bt.add_argument("--stocks", type=str, default="000001")
    bt.add_argument("--hold-days", type=int, default=5)
    cp = sub.add_parser("cache")
    cs = cp.add_subparsers(dest="cache_action")
    cs.add_parser("clear")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args, path=getattr(args, "config", None))
    fmt = OutputFormatter(cfg)

    # 缓存管理
    if cfg.command == "cache":
        cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
        print(f"Cleared {cache.clear()} cached files")
        return

    # 回测
    if cfg.command == "backtest":
        from aimoon.backtest import BacktestEngine
        from aimoon.data.history import get_kline
        engine = BacktestEngine(cfg, hold_days=cfg.hold_days)
        cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
        fmt.console.print(f"[bold blue]=== Backtest (hold {cfg.hold_days}d) ===[/bold blue]")
        for code in cfg.stocks.split(","):
            code = code.strip()
            r = get_kline(code, cfg.history_days, cache)
            if r.is_ok():
                result = engine.run(code, code, r.unwrap())
                color = "green" if result.total_return > 0 else "red"
                fmt.console.print(
                    f"  {result.code}: [{color}]{result.total_return:+.2f}%[/{color}] "
                    f"胜率={result.win_rate:.0%} 交易={result.trade_count}次 "
                    f"最大回撤={result.max_drawdown:.2%}"
                )
        return

    # Demo 模式
    if cfg.demo:
        from aimoon.demo import generate_demo
        spot_df, klines = generate_demo()
        cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
        results, tails = screen_universe(spot_df, cfg, cache, klines=klines)
    else:
        # 实时筛选管道
        sr = get_spot(cfg)
        if sr.is_err():
            fmt.console.print(f"[red]Failed: {sr.error}[/red]")
            fmt.console.print("[yellow]Try: python -m aimoon --demo[/yellow]")
            sys.exit(1)
        spot = sr.unwrap()

        fmt.console.print("[dim]Filtering universe...[/dim]")
        universe = filter_universe(spot, cfg)

        fmt.console.print("[dim]Finding top sectors...[/dim]")
        universe, ctx = filter_by_sectors(universe)

        fmt.console.print("[dim]Checking holdings...[/dim]")
        universe = filter_by_holdings(universe, cfg, spot)

        cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
        fmt.console.print(f"[dim]Analyzing {len(universe)} stocks...[/dim]")
        t0 = time.time()
        results, tails = screen_universe(universe, cfg, cache, ctx)
        fmt.console.print(f"[dim]Done in {time.time() - t0:.1f}s[/dim]")

    # RPS + 排序 + 输出
    results = compute_rps(results, tails)
    top = sorted(results, key=lambda s: s.total_score, reverse=True)[:cfg.top_n]
    fmt.display(top)
    if not cfg.no_csv and top:
        fmt.console.print(f"[dim]Exported: {fmt.export_csv(top)}[/dim]")
        fmt.console.print(f"[dim]Exported: {fmt.export_markdown(top)}[/dim]")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 更新 __main__.py**

```python
from aimoon.cli import main
if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 写 tests/test_cli.py**

```python
"""Tests for CLI"""
from unittest.mock import patch


class TestParseArgs:
    def test_default_args(self) -> None:
        with patch("sys.argv", ["aimoon"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.top == 30
            assert args.workers == 5

    def test_demo_flag(self) -> None:
        with patch("sys.argv", ["aimoon", "--demo"]):
            from aimoon.cli import parse_args
            assert parse_args().demo is True

    def test_backtest_subcommand(self) -> None:
        with patch("sys.argv", ["aimoon", "backtest", "--hold-days", "10"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.command == "backtest"
            assert args.hold_days == 10
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/cli.py src/aimoon/__main__.py tests/test_cli.py
git commit -m "feat: rewrite CLI as thin pipeline"
```

---

### Task 11: 清理旧文件 + 更新 __init__.py

**Files:**
- Delete: `src/aimoon/strategies/` 整个目录
- Delete: `src/aimoon/output/formatter.py`, `src/aimoon/output/__init__.py`
- Delete: `src/aimoon/cache/provider.py`, `src/aimoon/cache/__init__.py`
- Delete: `src/aimoon/data.py`
- Delete: `tests/test_indicators.py`, `tests/test_scoring.py`, `tests/test_data.py`, `tests/test_cache.py`, `tests/test_screener.py`（旧版）
- Modify: `src/aimoon/__init__.py`
- Modify: `src/aimoon/result.py`

- [ ] **Step 1: 删除旧目录**

```bash
rm -rf src/aimoon/strategies/
rm -rf src/aimoon/output/
rm -rf src/aimoon/cache/
rm src/aimoon/data.py
```

- [ ] **Step 2: 删除旧测试**

```bash
rm tests/test_indicators.py tests/test_scoring.py tests/test_data.py tests/test_cache.py tests/test_screener.py
```

- [ ] **Step 3: 更新 __init__.py**

```python
"""aimoon — A股量化筛选与交易建议系统"""
from __future__ import annotations

__all__ = [
    "config", "models", "cache", "data", "indicators",
    "scoring", "screener", "backtest", "output", "demo", "cli",
]
```

- [ ] **Step 4: 全量测试**

Run: `pytest tests/ -v --tb=short`
Expected: all PASS

- [ ] **Step 5: Demo 运行验证**

Run: `python -m aimoon --demo --no-csv`
Expected: 显示 30 只模拟股票的筛选结果表格

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "refactor: complete rewrite — remove old strategy/screener/output modules"
```
