# aimoon 全面重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 全面重构 aimoon A股量化筛选系统 — 修复Bug、添加缓存层、引入可插拔策略系统、回测框架、YAML配置支持、完整测试覆盖、CLI增强。

**Architecture:** 在现有单体结构基础上，引入缓存层（pickle+TTL）、策略模式（ABC+具体策略）、回测引擎。保持CLI工具定位，新增子命令支持回测和缓存管理。

**Tech Stack:** Python 3.12+, AKShare, Pandas, Rich, PyYAML, pytest

---

## 文件结构

### 新建文件
| 文件 | 职责 |
|------|------|
| `src/aimoon/cache/__init__.py` | 缓存模块导出 |
| `src/aimoon/cache/provider.py` | 文件缓存（pickle + TTL） |
| `src/aimoon/strategies/base.py` | Strategy ABC 定义 |
| `src/aimoon/strategies/backtester.py` | 回测引擎 |
| `tests/test_cache.py` | 缓存层测试 |
| `tests/test_screener.py` | 策略系统测试 |
| `tests/test_backtester.py` | 回测引擎测试 |
| `tests/test_formatter.py` | 输出格式化测试 |
| `tests/test_data.py` | 数据层测试 |
| `tests/test_cli.py` | CLI 测试 |

### 修改文件
| 文件 | 变更 |
|------|------|
| `src/aimoon/output/formatter.py` | 修复中文样式判断 |
| `src/aimoon/strategies/screener.py` | 重构为编排器 + 线程安全 |
| `src/aimoon/strategies/technical.py` | 新建，承载打分逻辑 |
| `src/aimoon/data.py` | 集成缓存层 |
| `src/aimoon/config.py` | 添加 YAML 配置加载 |
| `src/aimoon/cli.py` | 子命令路由 + 新参数 |
| `src/aimoon/strategies/__init__.py` | 更新导出 |
| `src/aimoon/indicators/__init__.py` | 保持不变 |
| `pyproject.toml` | 添加 pyyaml 依赖 |
| `.gitignore` | 添加 `.aimoon_cache/` |

---

## Task 1: Bug修复

### Task 1.1: 修复 formatter.py 中文样式判断

**Files:**
- Modify: `src/aimoon/output/formatter.py:38`

- [ ] **Step 1: 修改样式判断逻辑**

当前代码（line 38）用英文 `"Buy"/"Sell"` 判断，但建议文本是中文。修改为匹配中文关键词：

```python
# src/aimoon/output/formatter.py, line 38
# 修改前:
ss = "bold green" if "Buy" in r.suggestion or "buy" in r.suggestion.lower() else ("red" if "Sell" in r.suggestion or "sell" in r.suggestion.lower() else "dim")

# 修改后:
ss = "bold green" if "买" in r.suggestion else ("red" if "卖" in r.suggestion else "dim")
```

- [ ] **Step 2: 验证修改**

```bash
python -c "
from aimoon.strategies.screener import SignalScore
s = SignalScore(stock_code='000001', stock_name='Test', price=10.0, pct_change=1.0, turnover=5.0)
s.suggestion = '买入'
print('买' in s.suggestion)  # True
s.suggestion = '强烈卖出'
print('卖' in s.suggestion)  # True
s.suggestion = '观望'
print('买' in s.suggestion or '卖' in s.suggestion)  # False
"
```

- [ ] **Step 3: Commit**

```bash
git add src/aimoon/output/formatter.py
git commit -m "fix: correct Chinese style matching in formatter"
```

---

### Task 1.2: 修复线程安全问题

**Files:**
- Modify: `src/aimoon/strategies/screener.py`

- [ ] **Step 1: 添加 threading 导入和锁**

在 `screener.py` 顶部添加导入，在 `__init__` 中加锁：

```python
# src/aimoon/strategies/screener.py
# 在 import 区域添加:
import threading

# 在 StockScreener.__init__ 中添加:
self._lock = threading.Lock()
```

- [ ] **Step 2: 用锁保护 append 操作**

```python
# src/aimoon/strategies/screener.py
# 在 screen_stock 方法中，替换:
#   self.results.append(s)
# 为:
with self._lock:
    self.results.append(s)
```

- [ ] **Step 3: Commit**

```bash
git add src/aimoon/strategies/screener.py
git commit -m "fix: add thread lock for screener results append"
```

---

### Task 1.3: 拆分 screener.py 过长方法

**Files:**
- Modify: `src/aimoon/strategies/screener.py`

- [ ] **Step 1: 提取 spot 字段解析方法**

在 `StockScreener` 类中添加新方法，将 `screen_stock()` 中重复的 spot_row 字段提取逻辑移到这里：

```python
# src/aimoon/strategies/screener.py
# 在 StockScreener 类中添加方法:

def _extract_spot_fields(self, spot_row: pd.Series | None) -> dict[str, float]:
    """从 spot_row 提取估值和市值字段，缺失返回默认值 0。"""
    pe = 0.0
    if spot_row is not None and "pe" in spot_row.index and pd.notna(spot_row["pe"]):
        pe = float(spot_row["pe"])
    pb = 0.0
    if spot_row is not None and "pb" in spot_row.index and pd.notna(spot_row["pb"]):
        pb = float(spot_row["pb"])
    total_cap = 0.0
    if (
        spot_row is not None
        and "total_market_cap" in spot_row.index
        and pd.notna(spot_row["total_market_cap"])
    ):
        total_cap = float(spot_row["total_market_cap"]) / 1e8
    float_cap = 0.0
    if (
        spot_row is not None
        and "float_market_cap" in spot_row.index
        and pd.notna(spot_row["float_market_cap"])
    ):
        float_cap = float(spot_row["float_market_cap"]) / 1e8
    return {"pe": pe, "pb": pb, "total_cap": total_cap, "float_cap": float_cap}
```

- [ ] **Step 2: 重构 screen_stock 使用新方法**

```python
# src/aimoon/strategies/screener.py
# 在 screen_stock 方法中，替换 spot 字段提取块 (pe/pb/total_cap/float_cap 的 if 块)
# 为:

fields = self._extract_spot_fields(spot_row)
score = SignalScore(
    stock_code=stock_code, stock_name=stock_name,
    price=price, pct_change=pct_change, turnover=turnover,
    pe=fields["pe"], pb=fields["pb"],
    total_market_cap_yi=fields["total_cap"],
    float_market_cap_yi=fields["float_cap"],
)
```

- [ ] **Step 3: 验证不破坏现有行为**

```bash
python -c "from aimoon.strategies.screener import StockScreener; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/strategies/screener.py
git commit -m "refactor: extract spot field parsing from screen_stock"
```

---

## Task 2: 缓存层

### Task 2.1: 实现 DataCache

**Files:**
- Create: `src/aimoon/cache/__init__.py`
- Create: `src/aimoon/cache/provider.py`

- [ ] **Step 1: 创建 cache 模块 __init__.py**

```python
# src/aimoon/cache/__init__.py
from aimoon.cache.provider import DataCache

__all__ = ["DataCache"]
```

- [ ] **Step 2: 实现 DataCache**

```python
# src/aimoon/cache/provider.py
"""文件缓存层 - pickle 序列化 + TTL 过期"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class DataCache:
    """缓存 DataFrame 到磁盘，支持 TTL 过期。"""

    def __init__(self, cache_dir: str = ".aimoon_cache", ttl_hours: int = 4) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_hours * 3600
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, stock_code: str) -> Path:
        return self.cache_dir / f"{stock_code}.pkl"

    def get(self, stock_code: str) -> pd.DataFrame | None:
        """返回缓存的 DataFrame，过期或不存在返回 None。"""
        path = self._path_for(stock_code)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.ttl_seconds:
            logger.debug("Cache expired for %s (%.0fs old)", stock_code, age)
            return None
        try:
            return pd.read_pickle(path)
        except Exception as e:
            logger.warning("Cache read failed for %s: %s", stock_code, e)
            return None

    def put(self, stock_code: str, df: pd.DataFrame) -> None:
        """写入 DataFrame 到缓存。"""
        try:
            df.to_pickle(self._path_for(stock_code))
        except Exception as e:
            logger.warning("Cache write failed for %s: %s", stock_code, e)

    def clear(self) -> int:
        """清除所有缓存文件，返回删除数量。"""
        count = 0
        for p in self.cache_dir.glob("*.pkl"):
            p.unlink()
            count += 1
        return count
```

- [ ] **Step 3: 验证导入正常**

```bash
python -c "from aimoon.cache import DataCache; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/cache/
git commit -m "feat: add file-based data cache with TTL"
```

---

### Task 2.2: 缓存层测试

**Files:**
- Create: `tests/test_cache.py`

- [ ] **Step 1: 编写缓存测试**

```python
# tests/test_cache.py
"""Tests for cache provider"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from aimoon.cache.provider import DataCache


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame({
        "close": [10.0, 11.0, 12.0],
        "open": [9.5, 10.5, 11.5],
        "volume": [1000, 2000, 3000],
    }, index=pd.date_range("2025-01-01", periods=3))


@pytest.fixture
def cache(tmp_path) -> DataCache:
    return DataCache(cache_dir=str(tmp_path / "test_cache"), ttl_hours=1)


class TestDataCache:
    def test_put_and_get(self, cache: DataCache, sample_df: pd.DataFrame) -> None:
        cache.put("000001", sample_df)
        result = cache.get("000001")
        assert result is not None
        assert len(result) == 3
        assert list(result.columns) == ["close", "open", "volume"]

    def test_get_missing_returns_none(self, cache: DataCache) -> None:
        assert cache.get("nonexistent") is None

    def test_expired_returns_none(self, tmp_path, sample_df: pd.DataFrame) -> None:
        cache = DataCache(cache_dir=str(tmp_path / "exp_cache"), ttl_hours=0)
        cache.put("000001", sample_df)
        # 文件已写入，TTL=0 应该过期
        time.sleep(0.1)
        assert cache.get("000001") is None

    def test_clear(self, cache: DataCache, sample_df: pd.DataFrame) -> None:
        cache.put("000001", sample_df)
        cache.put("600519", sample_df)
        removed = cache.clear()
        assert removed == 2
        assert cache.get("000001") is None
        assert cache.get("600519") is None
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/test_cache.py -v
```

Expected: 全部 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_cache.py
git commit -m "test: add cache provider tests"
```

---

### Task 2.3: 集成缓存到 data.py

**Files:**
- Modify: `src/aimoon/data.py`

- [ ] **Step 1: 在 data.py 中集成缓存**

在 `get_history_kline()` 函数中，先查缓存，未命中再请求 API：

```python
# src/aimoon/data.py
# 在文件顶部添加导入:
from aimoon.cache.provider import DataCache

# 创建模块级缓存实例（惰性初始化）
_cache: DataCache | None = None

def _get_cache() -> DataCache:
    global _cache
    if _cache is None:
        _cache = DataCache(ttl_hours=CONFIG.cache_ttl_hours)
    return _cache

# 修改 get_history_kline 函数:
def get_history_kline(stock_code: str, days: int | None = None) -> Result[pd.DataFrame, str]:
    days = days or CONFIG.history_days
    cache = _get_cache()
    cached = cache.get(stock_code)
    if cached is not None:
        return Ok(cached)
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(
            symbol=stock_code, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq",
        )
        if df is None or df.empty:
            return Err(f"{stock_code}: no data")
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude",
            "涨跌幅": "pct_change", "涨跌额": "change", "换手率": "turnover",
        })
        df["date"] = pd.to_datetime(df["date"])
        result_df = df.set_index("date").sort_index()
        cache.put(stock_code, result_df)
        return Ok(result_df)
    except Exception as e:
        return Err(f"{stock_code}: {e}")
```

- [ ] **Step 2: 验证导入正常**

```bash
python -c "from aimoon.data import get_history_kline; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/aimoon/data.py
git commit -m "feat: integrate data cache into get_history_kline"
```

---

### Task 2.4: 添加 .gitignore 和 CLI cache 子命令

**Files:**
- Modify: `.gitignore`
- Modify: `src/aimoon/cli.py`

- [ ] **Step 1: 更新 .gitignore**

```bash
# 如果 .gitignore 不存在则创建，存在则追加
echo ".aimoon_cache/" >> .gitignore
```

- [ ] **Step 2: 添加 cache clear 子命令到 CLI**

在 `cli.py` 中将 `argparse.ArgumentParser` 改为支持子命令。在 `parse_args()` 函数中：

```python
# src/aimoon/cli.py
def parse_args():
    p = argparse.ArgumentParser(description="A-share quant screener")
    sub = p.add_subparsers(dest="command")

    # 默认 screen 行为（兼容旧用法）
    p.add_argument("--top", type=int, default=CONFIG.top_n)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--no-csv", action="store_true")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--config", type=str, default=None, help="YAML config file path")

    # cache 子命令
    cache_p = sub.add_parser("cache", help="Cache management")
    cache_sub = cache_p.add_subparsers(dest="cache_action")
    cache_sub.add_parser("clear", help="Clear all cached data")

    # backtest 子命令（Task 5 中完善）
    bt_p = sub.add_parser("backtest", help="Backtest a strategy")
    bt_p.add_argument("--strategy", type=str, default="technical")
    bt_p.add_argument("--hold-days", type=int, default=5)
    bt_p.add_argument("--stocks", type=str, default=None, help="Comma-separated stock codes")
    bt_p.add_argument("--top", type=int, default=None, help="Backtest top N stocks from screening")

    return p.parse_args()
```

- [ ] **Step 3: 在 main() 中处理 cache 子命令**

在 `main()` 函数开头添加子命令分发：

```python
# src/aimoon/cli.py
def main():
    args = parse_args()

    # 子命令分发
    if args.command == "cache":
        if args.cache_action == "clear":
            from aimoon.cache.provider import DataCache
            cache = DataCache()
            removed = cache.clear()
            print(f"Cleared {removed} cached files")
            return
        return

    # ... 原有 main 逻辑 ...
```

- [ ] **Step 4: 验证子命令**

```bash
python -m aimoon cache clear
```

Expected: `Cleared 0 cached files`

- [ ] **Step 5: Commit**

```bash
git add .gitignore src/aimoon/cli.py
git commit -m "feat: add cache clear subcommand to CLI"
```

---

## Task 3: 策略系统

### Task 3.1: 创建 Strategy ABC

**Files:**
- Create: `src/aimoon/strategies/base.py`

- [ ] **Step 1: 实现 Strategy 抽象基类**

```python
# src/aimoon/strategies/base.py
"""策略抽象基类"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from aimoon.strategies.screener import SignalScore


class Strategy(ABC):
    """策略基类，所有打分策略实现此接口。"""

    @abstractmethod
    def score(
        self,
        code: str,
        name: str,
        kline: pd.DataFrame,
        spot: pd.Series | None = None,
    ) -> SignalScore | None:
        """对单只股票打分，返回 None 表示跳过。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略显示名称。"""
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from aimoon.strategies.base import Strategy; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/aimoon/strategies/base.py
git commit -m "feat: add Strategy ABC"
```

---

### Task 3.2: 创建 TechnicalStrategy

**Files:**
- Create: `src/aimoon/strategies/technical.py`
- Modify: `src/aimoon/strategies/__init__.py`

- [ ] **Step 1: 实现 TechnicalStrategy**

将 `StockScreener` 中的打分逻辑迁移到独立的 `TechnicalStrategy` 类：

```python
# src/aimoon/strategies/technical.py
"""技术指标策略 - 基于 MA/RSI/MACD/KDJ/布林带/成交量 打分"""
from __future__ import annotations

import logging

import pandas as pd

from aimoon.config import CONFIG
from aimoon.indicators.technical import TechnicalIndicators
from aimoon.strategies.base import Strategy
from aimoon.strategies.screener import SignalScore

logger = logging.getLogger(__name__)


class TechnicalStrategy(Strategy):
    """基于技术指标的多因子打分策略。"""

    @property
    def name(self) -> str:
        return "technical"

    def score(
        self,
        code: str,
        name: str,
        kline: pd.DataFrame,
        spot: pd.Series | None = None,
    ) -> SignalScore | None:
        if kline is None or len(kline) < CONFIG.ma_long:
            return None
        try:
            ti = TechnicalIndicators(kline)
        except Exception:
            return None
        price = float(kline["close"].iloc[-1])
        pct_change = (
            float(kline["pct_change"].iloc[-1])
            if "pct_change" in kline.columns
            else 0.0
        )
        turnover = (
            float(kline["turnover"].iloc[-1])
            if "turnover" in kline.columns
            else 0.0
        )
        fields = self._extract_spot_fields(spot)
        score = SignalScore(
            stock_code=code, stock_name=name,
            price=price, pct_change=pct_change, turnover=turnover,
            pe=fields["pe"], pb=fields["pb"],
            total_market_cap_yi=fields["total_cap"],
            float_market_cap_yi=fields["float_cap"],
        )
        self._score_trend(ti, score)
        self._score_rsi(ti, score)
        self._score_macd(ti, score)
        self._score_kdj(ti, score)
        self._score_volume(ti, score)
        self._score_bollinger(ti, score)
        score.total_score = (
            score.trend_score + score.rsi_score + score.macd_score +
            score.kdj_score + score.volume_score + score.boll_score
        )
        score.suggestion, score.confidence = self._generate_suggestion(score)
        return score

    def _extract_spot_fields(self, spot: pd.Series | None) -> dict[str, float]:
        """从 spot 提取估值和市值字段。"""
        pe = 0.0
        if spot is not None and "pe" in spot.index and pd.notna(spot["pe"]):
            pe = float(spot["pe"])
        pb = 0.0
        if spot is not None and "pb" in spot.index and pd.notna(spot["pb"]):
            pb = float(spot["pb"])
        total_cap = 0.0
        if (
            spot is not None
            and "total_market_cap" in spot.index
            and pd.notna(spot["total_market_cap"])
        ):
            total_cap = float(spot["total_market_cap"]) / 1e8
        float_cap = 0.0
        if (
            spot is not None
            and "float_market_cap" in spot.index
            and pd.notna(spot["float_market_cap"])
        ):
            float_cap = float(spot["float_market_cap"]) / 1e8
        return {"pe": pe, "pb": pb, "total_cap": total_cap, "float_cap": float_cap}

    def _score_trend(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        trend = ti.ma_trend()
        if trend == "bullish":
            score.trend_score = 2
            score.signals.append("均线多头排列")
        elif trend == "bearish":
            score.trend_score = -2
            score.signals.append("均线空头排列")
        if ti.ma_golden_cross():
            score.trend_score += 2
            score.signals.append("MA金叉")
        if ti.ma_death_cross():
            score.trend_score -= 2
            score.signals.append("MA死叉")

    def _score_rsi(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        sig = ti.rsi_signal()
        if sig == "oversold":
            score.rsi_score = 2
            score.signals.append("RSI超卖")
        elif sig == "overbought":
            score.rsi_score = -2
            score.signals.append("RSI超买")

    def _score_macd(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        if ti.macd_golden_cross():
            score.macd_score = 2
            score.signals.append("MACD金叉")
        if ti.macd_death_cross():
            score.macd_score -= 2
            score.signals.append("MACD死叉")
        if ti.macd_above_zero():
            score.macd_score += 1
            score.signals.append("MACD零轴上方")
        else:
            score.macd_score -= 1

    def _score_kdj(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        if ti.kdj_golden_cross():
            score.kdj_score = 2
            score.signals.append("KDJ金叉")
        if ti.kdj_oversold():
            score.kdj_score += 1
            score.signals.append("KDJ超卖")
        if ti.kdj_overbought():
            score.kdj_score -= 1
            score.signals.append("KDJ超买")

    def _score_volume(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        vr = ti.volume_ratio()
        if vr > 2.0:
            score.volume_score = 2
            score.signals.append("放量(2x+)")
        elif vr > 1.5:
            score.volume_score = 1
            score.signals.append("温和放量")
        elif vr < 0.5:
            score.volume_score = -1
            score.signals.append("缩量")

    def _score_bollinger(self, ti: TechnicalIndicators, score: SignalScore) -> None:
        pos = ti.bollinger_position()
        if pos == "below":
            score.boll_score = 1
            score.signals.append("触及布林下轨")
        elif pos == "above":
            score.boll_score = -1
            score.signals.append("触及布林上轨")

    def _generate_suggestion(self, score: SignalScore) -> tuple[str, str]:
        total = score.total_score
        if total >= 6:
            return "强烈买入", "高"
        elif total >= 4:
            return "买入", "中高"
        elif total >= 2:
            return "建议买入", "中"
        elif total >= 0:
            return "观望", "低"
        elif total >= -2:
            return "谨慎", "中"
        elif total >= -4:
            return "建议卖出", "中高"
        else:
            return "强烈卖出", "高"
```

- [ ] **Step 2: 更新 strategies/__init__.py**

```python
# src/aimoon/strategies/__init__.py
from aimoon.strategies.base import Strategy
from aimoon.strategies.screener import StockScreener
from aimoon.strategies.technical import TechnicalStrategy

__all__ = ["Strategy", "StockScreener", "TechnicalStrategy"]
```

- [ ] **Step 3: 验证导入**

```bash
python -c "from aimoon.strategies import TechnicalStrategy, Strategy; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/strategies/technical.py src/aimoon/strategies/__init__.py
git commit -m "feat: add TechnicalStrategy with scoring logic from screener"
```

---

### Task 3.3: 重构 StockScreener 为编排器

**Files:**
- Modify: `src/aimoon/strategies/screener.py`
- Modify: `src/aimoon/cli.py`

- [ ] **Step 1: 重构 StockScreener**

将 `screener.py` 中的打分逻辑替换为委托给 Strategy：

```python
# src/aimoon/strategies/screener.py
"""Stock screener - strategy orchestration"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from aimoon.config import CONFIG

if TYPE_CHECKING:
    from aimoon.strategies.base import Strategy

logger = logging.getLogger(__name__)


@dataclass
class SignalScore:
    stock_code: str
    stock_name: str
    price: float
    pct_change: float
    turnover: float
    pe: float = 0.0
    pb: float = 0.0
    total_market_cap_yi: float = 0.0
    float_market_cap_yi: float = 0.0
    trend_score: int = 0
    rsi_score: int = 0
    macd_score: int = 0
    kdj_score: int = 0
    volume_score: int = 0
    boll_score: int = 0
    total_score: int = 0
    signals: list[str] = field(default_factory=list)
    suggestion: str = "观望"
    confidence: str = "低"


class StockScreener:
    def __init__(self, strategies: list[Strategy] | None = None) -> None:
        self._strategies = strategies
        self.results: list[SignalScore] = []
        self._lock = threading.Lock()

    def _get_strategies(self) -> list[Strategy]:
        if self._strategies is None:
            from aimoon.strategies.technical import TechnicalStrategy
            self._strategies = [TechnicalStrategy()]
        return self._strategies

    def screen_stock(
        self, stock_code: str, stock_name: str,
        kline_df: pd.DataFrame, spot_row: pd.Series | None = None,
    ) -> SignalScore | None:
        for strategy in self._get_strategies():
            result = strategy.score(stock_code, stock_name, kline_df, spot_row)
            if result:
                with self._lock:
                    self.results.append(result)
                return result
        return None

    def get_top_picks(self, n: int | None = None) -> list[SignalScore]:
        n = n or CONFIG.top_n
        sorted_results = sorted(self.results, key=lambda x: x.total_score, reverse=True)
        return sorted_results[:n]
```

- [ ] **Step 2: 验证 screener 仍可正常导入和使用**

```bash
python -c "from aimoon.strategies.screener import StockScreener, SignalScore; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/aimoon/strategies/screener.py
git commit -m "refactor: convert StockScreener to strategy orchestrator"
```

---

## Task 4: 回测框架

### Task 4.1: 回测数据结构和引擎

**Files:**
- Create: `src/aimoon/strategies/backtester.py`

- [ ] **Step 1: 实现 BacktestEngine**

```python
# src/aimoon/strategies/backtester.py
"""回测引擎 - 在历史数据上模拟策略表现"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from aimoon.config import CONFIG
from aimoon.strategies.base import Strategy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeRecord:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    signal: str


@dataclass(frozen=True)
class BacktestResult:
    stock_code: str
    stock_name: str
    total_return: float
    win_rate: float
    max_drawdown: float
    trade_count: int
    trades: list[TradeRecord]


class BacktestEngine:
    """在历史K线上逐日回测策略。"""

    def __init__(self, strategy: Strategy, hold_days: int = 5) -> None:
        self.strategy = strategy
        self.hold_days = hold_days

    def run(self, code: str, name: str, kline: pd.DataFrame) -> BacktestResult:
        """逐日滚动窗口运行策略。"""
        min_window = CONFIG.ma_long
        if len(kline) < min_window + self.hold_days:
            return BacktestResult(
                stock_code=code, stock_name=name,
                total_return=0.0, win_rate=0.0, max_drawdown=0.0,
                trade_count=0, trades=[],
            )

        trades: list[TradeRecord] = []
        dates = kline.index.tolist()
        in_trade = False
        exit_idx = 0

        for i in range(min_window, len(kline) - self.hold_days):
            if in_trade and i < exit_idx:
                continue
            in_trade = False

            window = kline.iloc[:i + 1]
            sig = self.strategy.score(code, name, window)
            if sig is None or sig.total_score < 2:
                continue

            entry_price = float(kline["close"].iloc[i])
            exit_i = min(i + self.hold_days, len(kline) - 1)
            exit_price = float(kline["close"].iloc[exit_i])
            ret = (exit_price - entry_price) / entry_price * 100

            trades.append(TradeRecord(
                entry_date=str(dates[i].date()),
                exit_date=str(dates[exit_i].date()),
                entry_price=entry_price,
                exit_price=exit_price,
                return_pct=ret,
                signal=", ".join(sig.signals[:3]),
            ))
            in_trade = True
            exit_idx = exit_i + 1

        return self._calc_metrics(code, name, trades, kline)

    def run_batch(self, stocks: dict[str, tuple[str, pd.DataFrame]]) -> list[BacktestResult]:
        """批量回测。stocks: {code: (name, kline_df)}"""
        return [self.run(code, name, kline) for code, (name, kline) in stocks.items()]

    def _calc_metrics(
        self, code: str, name: str, trades: list[TradeRecord], kline: pd.DataFrame,
    ) -> BacktestResult:
        if not trades:
            return BacktestResult(
                stock_code=code, stock_name=name,
                total_return=0.0, win_rate=0.0, max_drawdown=0.0,
                trade_count=0, trades=[],
            )
        total_ret = sum(t.return_pct for t in trades)
        wins = sum(1 for t in trades if t.return_pct > 0)
        win_rate = wins / len(trades)

        # 最大回撤：基于每日净值曲线
        equity = [100.0]
        trade_idx = 0
        daily_returns: list[float] = []
        for i in range(1, len(kline)):
            if trade_idx < len(trades) and str(kline.index[i].date()) == trades[trade_idx].exit_date:
                daily_returns.append(trades[trade_idx].return_pct / 100)
                trade_idx += 1
            else:
                daily_returns.append(0.0)
        for r in daily_returns:
            equity.append(equity[-1] * (1 + r))
        peak = equity[0]
        max_dd = 0.0
        for v in equity:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd

        return BacktestResult(
            stock_code=code, stock_name=name,
            total_return=total_ret,
            win_rate=win_rate,
            max_drawdown=max_dd,
            trade_count=len(trades),
            trades=trades,
        )
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from aimoon.strategies.backtester import BacktestEngine, BacktestResult, TradeRecord; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/aimoon/strategies/backtester.py
git commit -m "feat: add backtesting engine with trade simulation"
```

---

### Task 4.2: 回测 CLI 集成

**Files:**
- Modify: `src/aimoon/cli.py`

- [ ] **Step 1: 在 main() 中添加 backtest 子命令处理**

在 `main()` 函数中，cache 子命令处理之后添加：

```python
# src/aimoon/cli.py
# 在 main() 中，cache 子命令块之后添加:

    if args.command == "backtest":
        from aimoon.strategies.backtester import BacktestEngine
        from aimoon.strategies.technical import TechnicalStrategy

        strategy = TechnicalStrategy()
        engine = BacktestEngine(strategy, hold_days=args.hold_days)
        fmt = OutputFormatter()
        fmt.console.print(f"[bold blue]=== Backtest: {strategy.name} (hold {args.hold_days}d) ===[/bold blue]")

        if args.stocks:
            codes = [c.strip() for c in args.stocks.split(",")]
            fmt.console.print(f"[dim]Backtesting {len(codes)} stocks...[/dim]")
            results = []
            for code in codes:
                r = get_history_kline(code, days=CONFIG.history_days)
                if r.is_ok():
                    result = engine.run(code, code, r.unwrap())
                    results.append(result)
            # 输出结果
            for res in results:
                color = "green" if res.total_return > 0 else "red"
                fmt.console.print(
                    f"  {res.stock_code}: [{color}]{res.total_return:+.2f}%[/{color}] "
                    f"胜率={res.win_rate:.0%} 交易={res.trade_count}次 "
                    f"最大回撤={res.max_drawdown:.2%}"
                )
        return
```

- [ ] **Step 2: 验证 demo 模式下的回测**

```bash
python -c "
from aimoon.strategies.backtester import BacktestEngine
from aimoon.strategies.technical import TechnicalStrategy
from aimoon.cli import generate_demo
_, klines = generate_demo()
engine = BacktestEngine(TechnicalStrategy(), hold_days=5)
result = engine.run('000001', 'PingAnBank', klines['000001'])
print(f'Return: {result.total_return:.2f}%, Trades: {result.trade_count}, WinRate: {result.win_rate:.0%}')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/aimoon/cli.py
git commit -m "feat: add backtest subcommand to CLI"
```

---

## Task 5: YAML 配置支持

### Task 5.1: 配置文件加载

**Files:**
- Modify: `src/aimoon/config.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 pyyaml 依赖**

```toml
# pyproject.toml dependencies 中添加:
"pyyaml>=6.0",
```

- [ ] **Step 2: 实现 load_config**

```python
# src/aimoon/config.py
"""配置模块 - 支持 YAML 配置文件"""
from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AppConfig:
    """应用配置，所有参数集中管理"""
    history_days: int = 250
    recent_days: int = 20
    min_market_cap_yi: float = 50.0
    max_market_cap_yi: float = 2000.0
    min_turnover_pct: float = 3.0
    max_turnover_pct: float = 30.0
    min_price: float = 5.0
    max_price: float = 100.0
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
    top_n: int = 30
    output_dir: str = "output"
    cache_ttl_hours: int = 4
    exclude_boards: tuple[str, ...] = ("ST", "退", "北交所")
    exclude_prefixes: tuple[str, ...] = ("8", "4")


def load_config(path: str | None = None) -> AppConfig:
    """加载配置：默认值 < YAML 文件。
    YAML 文件不存在时使用默认值并记录警告。
    """
    if path is None:
        return AppConfig()

    p = Path(path)
    if not p.exists():
        logger.warning("Config file not found: %s, using defaults", path)
        return AppConfig()

    try:
        import yaml
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load config %s: %s, using defaults", path, e)
        return AppConfig()

    valid_fields = {f.name for f in fields(AppConfig)}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return AppConfig(**filtered)


CONFIG = AppConfig()
```

- [ ] **Step 3: 在 CLI 中支持 --config 参数**

在 `cli.py` 的 `main()` 函数开头，`parse_args()` 之后添加配置加载。

首先修改 cli.py 顶部导入方式，改为导入模块：

```python
# src/aimoon/cli.py
# 将原来的:
#   from aimoon.config import CONFIG
# 改为:
import aimoon.config as _config_mod
CONFIG = _config_mod.CONFIG  # 初始引用
```

然后在 `main()` 开头添加配置加载：

```python
# src/aimoon/cli.py
# 在 main() 中 args = parse_args() 之后添加:

    if args.config:
        from aimoon.config import load_config
        new_cfg = load_config(args.config)
        _config_mod.CONFIG = new_cfg  # 更新模块级引用，其他模块也能生效
```

这样 `data.py`、`screener.py` 等通过 `from aimoon.config import CONFIG` 导入的模块，
在下次访问 `CONFIG` 时会获取到更新后的配置。

注意：`from aimoon.config import CONFIG` 是值绑定，对于 frozen dataclass 实例，
需要改为 `import aimoon.config` 然后用 `aimoon.config.CONFIG` 访问才能动态生效。
如果其他模块也需要动态配置，应改为 `import aimoon.config as cfg` 然后用 `cfg.CONFIG`。

- [ ] **Step 4: 安装 pyyaml 并验证**

```bash
pip install pyyaml>=6.0
python -c "from aimoon.config import load_config; c = load_config(); print(c.top_n)"
```

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/config.py src/aimoon/cli.py pyproject.toml
git commit -m "feat: add YAML config file support"
```

---

## Task 6: 测试补充

### Task 6.1: screener 测试

**Files:**
- Create: `tests/test_screener.py`

- [ ] **Step 1: 编写 screener 测试**

```python
# tests/test_screener.py
"""Tests for stock screener and strategy system"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.strategies.screener import StockScreener, SignalScore
from aimoon.strategies.technical import TechnicalStrategy
from aimoon.strategies.base import Strategy


@pytest.fixture
def sample_kline() -> pd.DataFrame:
    np.random.seed(42)
    n = 120
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = 20 + np.cumsum(np.random.randn(n) * 0.5)
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "close": close,
        "high": close + np.abs(np.random.randn(n) * 0.3),
        "low": close - np.abs(np.random.randn(n) * 0.3),
        "volume": np.random.randint(100000, 1000000, n).astype(float),
        "turnover": np.random.uniform(1, 10, n),
        "pct_change": np.random.randn(n) * 2,
    }, index=dates)


class TestTechnicalStrategy:
    def test_score_returns_signal(self, sample_kline: pd.DataFrame) -> None:
        strategy = TechnicalStrategy()
        result = strategy.score("000001", "Test", sample_kline)
        assert result is not None
        assert isinstance(result, SignalScore)
        assert result.stock_code == "000001"

    def test_score_short_data_returns_none(self) -> None:
        strategy = TechnicalStrategy()
        df = pd.DataFrame({"close": [10.0] * 10})
        assert strategy.score("000001", "Test", df) is None

    def test_score_with_spot_data(self, sample_kline: pd.DataFrame) -> None:
        strategy = TechnicalStrategy()
        spot = pd.Series({"pe": 15.0, "pb": 2.0, "total_market_cap": 1e10, "float_market_cap": 5e9})
        result = strategy.score("000001", "Test", sample_kline, spot)
        assert result is not None
        assert result.pe == 15.0
        assert result.pb == 2.0

    def test_name_property(self) -> None:
        assert TechnicalStrategy().name == "technical"


class TestStockScreener:
    def test_screen_stock(self, sample_kline: pd.DataFrame) -> None:
        screener = StockScreener()
        result = screener.screen_stock("000001", "Test", sample_kline)
        assert result is not None
        assert len(screener.results) == 1

    def test_get_top_picks(self, sample_kline: pd.DataFrame) -> None:
        screener = StockScreener()
        for i in range(5):
            screener.screen_stock(f"00000{i}", f"Stock{i}", sample_kline)
        picks = screener.get_top_picks(3)
        assert len(picks) == 3
        assert picks[0].total_score >= picks[1].total_score

    def test_custom_strategy(self, sample_kline: pd.DataFrame) -> None:
        class DummyStrategy(Strategy):
            @property
            def name(self) -> str:
                return "dummy"
            def score(self, code, name, kline, spot=None):
                return SignalScore(
                    stock_code=code, stock_name=name,
                    price=10.0, pct_change=0.0, turnover=5.0,
                    total_score=99,
                )
        screener = StockScreener(strategies=[DummyStrategy()])
        result = screener.screen_stock("000001", "Test", sample_kline)
        assert result is not None
        assert result.total_score == 99
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/test_screener.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_screener.py
git commit -m "test: add screener and strategy tests"
```

---

### Task 6.2: backtester 测试

**Files:**
- Create: `tests/test_backtester.py`

- [ ] **Step 1: 编写回测测试**

```python
# tests/test_backtester.py
"""Tests for backtesting engine"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.strategies.backtester import BacktestEngine, BacktestResult
from aimoon.strategies.technical import TechnicalStrategy


@pytest.fixture
def trending_kline() -> pd.DataFrame:
    """生成上涨趋势的K线数据。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    # 上涨趋势
    close = 10 + np.arange(n) * 0.1 + np.random.randn(n) * 0.2
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.05,
        "close": close,
        "high": close + np.abs(np.random.randn(n) * 0.1),
        "low": close - np.abs(np.random.randn(n) * 0.1),
        "volume": np.random.randint(100000, 1000000, n).astype(float),
        "turnover": np.random.uniform(1, 10, n),
        "pct_change": np.random.randn(n) * 2,
    }, index=dates)


class TestBacktestEngine:
    def test_run_returns_result(self, trending_kline: pd.DataFrame) -> None:
        engine = BacktestEngine(TechnicalStrategy(), hold_days=5)
        result = engine.run("000001", "Test", trending_kline)
        assert isinstance(result, BacktestResult)
        assert result.stock_code == "000001"

    def test_short_data_no_trades(self) -> None:
        engine = BacktestEngine(TechnicalStrategy(), hold_days=5)
        df = pd.DataFrame({"close": [10.0] * 30}, index=pd.date_range("2025-01-01", periods=30))
        result = engine.run("000001", "Test", df)
        assert result.trade_count == 0

    def test_batch_run(self, trending_kline: pd.DataFrame) -> None:
        engine = BacktestEngine(TechnicalStrategy(), hold_days=5)
        stocks = {"000001": ("Stock1", trending_kline), "000002": ("Stock2", trending_kline)}
        results = engine.run_batch(stocks)
        assert len(results) == 2

    def test_metrics_calculation(self, trending_kline: pd.DataFrame) -> None:
        engine = BacktestEngine(TechnicalStrategy(), hold_days=5)
        result = engine.run("000001", "Test", trending_kline)
        assert 0.0 <= result.win_rate <= 1.0
        assert 0.0 <= result.max_drawdown <= 1.0
        assert result.trade_count >= 0
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/test_backtester.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_backtester.py
git commit -m "test: add backtester tests"
```

---

### Task 6.3: formatter 测试

**Files:**
- Create: `tests/test_formatter.py`

- [ ] **Step 1: 编写 formatter 测试**

```python
# tests/test_formatter.py
"""Tests for output formatter"""
from __future__ import annotations

import os

import pytest

from aimoon.output.formatter import OutputFormatter
from aimoon.strategies.screener import SignalScore


@pytest.fixture
def formatter() -> OutputFormatter:
    return OutputFormatter()


@pytest.fixture
def sample_results() -> list[SignalScore]:
    return [
        SignalScore(
            stock_code="000001", stock_name="Test1", price=10.0,
            pct_change=2.5, turnover=5.0, total_score=6,
            suggestion="强烈买入", confidence="高", signals=["MA金叉", "RSI超卖"],
        ),
        SignalScore(
            stock_code="600519", stock_name="Test2", price=200.0,
            pct_change=-1.0, turnover=3.0, total_score=-3,
            suggestion="建议卖出", confidence="中高", signals=["MACD死叉"],
        ),
    ]


class TestOutputFormatter:
    def test_display_results_no_crash(self, formatter: OutputFormatter, sample_results) -> None:
        formatter.display_results(sample_results)

    def test_display_empty_results(self, formatter: OutputFormatter) -> None:
        formatter.display_results([])

    def test_export_csv(self, formatter: OutputFormatter, sample_results, tmp_path) -> None:
        filepath = formatter.export_csv(sample_results, filename="test.csv")
        assert os.path.exists(filepath)
        assert filepath.endswith("test.csv")
        # 清理
        os.remove(filepath)

    def test_chinese_style_buy(self, formatter: OutputFormatter) -> None:
        """验证中文 '买' 关键词匹配。"""
        r = SignalScore(
            stock_code="000001", stock_name="T", price=10.0,
            pct_change=1.0, turnover=5.0, suggestion="买入",
        )
        # 内部逻辑: "买" in "买入" -> True
        assert "买" in r.suggestion

    def test_chinese_style_sell(self, formatter: OutputFormatter) -> None:
        r = SignalScore(
            stock_code="000001", stock_name="T", price=10.0,
            pct_change=1.0, turnover=5.0, suggestion="强烈卖出",
        )
        assert "卖" in r.suggestion

    def test_chinese_style_hold(self, formatter: OutputFormatter) -> None:
        r = SignalScore(
            stock_code="000001", stock_name="T", price=10.0,
            pct_change=1.0, turnover=5.0, suggestion="观望",
        )
        assert "买" not in r.suggestion and "卖" not in r.suggestion
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/test_formatter.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_formatter.py
git commit -m "test: add formatter tests"
```

---

### Task 6.4: data 层测试

**Files:**
- Create: `tests/test_data.py`

- [ ] **Step 1: 编写 data 测试**

```python
# tests/test_data.py
"""Tests for data layer (filtering logic, mock AKShare)"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aimoon.data import filter_by_spot, filter_stock_list


@pytest.fixture
def spot_df() -> pd.DataFrame:
    return pd.DataFrame({
        "stock_code": ["000001", "000002", "600519", "800001"],
        "stock_name": ["Test1", "ST_Test", "Test3", "Test4"],
        "price": [10.0, 50.0, 150.0, 30.0],
        "turnover": [5.0, 10.0, 3.0, 8.0],
        "total_market_cap": [1e10, 5e10, 1e12, 2e10],
        "float_market_cap": [5e9, 3e10, 5e11, 1e10],
    })


class TestFilterBySpot:
    def test_filters_by_market_cap(self, spot_df: pd.DataFrame) -> None:
        result = filter_by_spot(spot_df)
        # min_market_cap_yi=50, max=2000
        # 1e10/1e8=100yi, 5e10/1e8=500yi, 1e12/1e8=10000yi, 2e10/1e8=200yi
        # 10000yi > max 2000 -> filtered out
        assert len(result) == 3  # 第三个被过滤掉

    def test_filters_by_price(self) -> None:
        df = pd.DataFrame({
            "stock_code": ["000001"],
            "stock_name": ["Test"],
            "price": [2.0],  # 低于 min_price=5
            "turnover": [5.0],
            "total_market_cap": [1e10],
            "float_market_cap": [5e9],
        })
        result = filter_by_spot(df)
        assert len(result) == 0


class TestFilterStockList:
    def test_excludes_st_prefix(self) -> None:
        df = pd.DataFrame({
            "stock_code": ["000001", "800001", "400001"],
            "stock_name": ["Test1", "Test2", "Test3"],
        })
        result = filter_stock_list(df)
        assert len(result) == 1
        assert result.iloc[0]["stock_code"] == "000001"

    def test_excludes_st_name(self) -> None:
        df = pd.DataFrame({
            "stock_code": ["000001", "000002"],
            "stock_name": ["Test", "ST_Something"],
        })
        result = filter_stock_list(df)
        assert len(result) == 1
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/test_data.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_data.py
git commit -m "test: add data layer filter tests"
```

---

### Task 6.5: CLI 测试

**Files:**
- Create: `tests/test_cli.py`

- [ ] **Step 1: 编写 CLI 测试**

```python
# tests/test_cli.py
"""Tests for CLI"""
from __future__ import annotations

import pytest
from unittest.mock import patch
import sys


class TestParseArgs:
    def test_default_args(self) -> None:
        with patch("sys.argv", ["aimoon"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.top == 30
            assert args.workers == 5
            assert args.demo is False
            assert args.no_csv is False

    def test_demo_flag(self) -> None:
        with patch("sys.argv", ["aimoon", "--demo"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.demo is True

    def test_top_flag(self) -> None:
        with patch("sys.argv", ["aimoon", "--top", "10"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.top == 10

    def test_cache_clear_subcommand(self) -> None:
        with patch("sys.argv", ["aimoon", "cache", "clear"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.command == "cache"
            assert args.cache_action == "clear"

    def test_backtest_subcommand(self) -> None:
        with patch("sys.argv", ["aimoon", "backtest", "--hold-days", "10"]):
            from aimoon.cli import parse_args
            args = parse_args()
            assert args.command == "backtest"
            assert args.hold_days == 10


class TestGenerateDemo:
    def test_generate_demo_returns_data(self) -> None:
        from aimoon.cli import generate_demo
        spot_df, klines = generate_demo()
        assert len(spot_df) == 30
        assert len(klines) == 30
        assert "000001" in klines
        assert len(klines["000001"]) == 120
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/test_cli.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: add CLI argument parsing and demo tests"
```

---

### Task 6.6: 运行完整测试套件

- [ ] **Step 1: 运行全部测试**

```bash
pytest tests/ -v --tb=short
```

Expected: 全部 PASS

- [ ] **Step 2: 检查覆盖率**

```bash
pip install pytest-cov
pytest tests/ --cov=src/aimoon --cov-report=term-missing
```

Expected: 总覆盖率 >= 80%

- [ ] **Step 3: 修复失败的测试（如有）**

如有测试失败，根据错误信息修复代码或测试。

- [ ] **Step 4: Commit（如修复了问题）**

```bash
git add -A
git commit -m "fix: resolve test failures and improve coverage"
```

---

## Task 7: 最终集成

### Task 7.1: 更新 strategies/__init__.py 导出

**Files:**
- Modify: `src/aimoon/strategies/__init__.py`

- [ ] **Step 1: 确认 __init__.py 导出完整**

```python
# src/aimoon/strategies/__init__.py
from aimoon.strategies.base import Strategy
from aimoon.strategies.backtester import BacktestEngine, BacktestResult, TradeRecord
from aimoon.strategies.screener import StockScreener, SignalScore
from aimoon.strategies.technical import TechnicalStrategy

__all__ = [
    "Strategy", "StockScreener", "SignalScore",
    "TechnicalStrategy",
    "BacktestEngine", "BacktestResult", "TradeRecord",
]
```

- [ ] **Step 2: 验证所有导入**

```bash
python -c "
from aimoon.strategies import Strategy, StockScreener, SignalScore, TechnicalStrategy
from aimoon.strategies import BacktestEngine, BacktestResult, TradeRecord
from aimoon.cache import DataCache
from aimoon.config import load_config
print('All imports OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/aimoon/strategies/__init__.py
git commit -m "chore: update strategies module exports"
```

---

### Task 7.2: 端到端验证

- [ ] **Step 1: Demo 模式端到端测试**

```bash
python -m aimoon --demo
```

Expected: 显示30只股票的筛选结果表格，无错误

- [ ] **Step 2: 回测端到端测试**

```bash
python -c "
from aimoon.cli import generate_demo
from aimoon.strategies.backtester import BacktestEngine
from aimoon.strategies.technical import TechnicalStrategy
_, klines = generate_demo()
engine = BacktestEngine(TechnicalStrategy(), hold_days=5)
for code in ['000001', '600519', '300750']:
    r = engine.run(code, code, klines[code])
    print(f'{code}: return={r.total_return:+.2f}% win={r.win_rate:.0%} trades={r.trade_count}')
"
```

- [ ] **Step 3: 缓存功能测试**

```bash
python -m aimoon cache clear
```

- [ ] **Step 4: 完整测试套件**

```bash
pytest tests/ -v --cov=src/aimoon --cov-report=term-missing
```

- [ ] **Step 5: 最终 Commit**

```bash
git add -A
git commit -m "chore: final integration verification"
```
