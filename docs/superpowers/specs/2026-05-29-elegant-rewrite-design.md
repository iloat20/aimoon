# aimoon 架构重写设计

> 日期：2026-05-29
> 风格：Pythonic 简洁——函数优先，类只在需要状态时用
> 范围：彻底重写，公共 API 可变，CLI 命令行接口保持不变

---

## 1. 设计目标

- **函数优先**：评分指标是独立函数，不是类方法
- **数据自解释**：`Signal(name="rsi_strong", score=2)` 替代字符串列表
- **配置显式传递**：去掉 `CONFIG` 全局单例
- **模块单一职责**：每个文件只做一件事，30-80 行
- **可组合**：加新指标 = 加一个文件 + 注册到列表

## 2. 模块结构

```
src/aimoon/
├── config.py          # Config frozen dataclass + load_config()
├── models.py          # Signal, ScoredStock
├── cache.py           # DataCache（保留现有实现）
│
├── data/              # 数据获取层
│   ├── __init__.py
│   ├── spot.py        # get_spot() — 东财全市场行情 + 分页
│   ├── history.py     # get_kline() — AKShare + 腾讯备用
│   └── filters.py     # filter_universe(), filter_by_sectors(), filter_by_holdings()
│
├── indicators/
│   ├── __init__.py
│   └── technical.py   # TechInd 类（内部缓存，公开简洁方法）
│
├── scoring/           # 每个信号一个函数
│   ├── __init__.py    # SCORERS 列表 + collect_signals()
│   ├── trend.py       # score_trend(ti) → list[Signal]
│   ├── rsi.py         # score_rsi(ti) → Signal | None
│   ├── macd.py        # score_macd(ti) → list[Signal]
│   ├── kdj.py         # score_kdj(ti) → list[Signal]
│   ├── volume.py      # score_volume(ti) → Signal | None
│   ├── bollinger.py   # score_bollinger(ti) → Signal | None
│   ├── momentum.py    # score_momentum(ti) → list[Signal]
│   ├── sector.py      # score_sector(code, ctx) → Signal | None
│   └── rps.py         # compute_rps(results, tails) → None (原地更新)
│
├── screener.py        # screen_stock() + screen_universe()
├── backtest.py        # BacktestEngine
├── output.py          # OutputFormatter
├── demo.py            # generate_demo()
└── cli.py             # parse_args() + main() 管道
```

## 3. 核心数据模型

### Signal

```python
@dataclass(frozen=True)
class Signal:
    name: str     # "rsi_strong" — 机器可读标识
    label: str    # "RSI强势(65)" — 人类可读描述
    score: int    # +2, -1, 0 ...
```

### ScoredStock

```python
@dataclass(frozen=True)
class ScoredStock:
    code: str
    name: str
    price: float
    pct_change: float
    turnover: float
    pe: float = 0.0
    pb: float = 0.0
    market_cap_yi: float = 0.0
    signals: tuple[Signal, ...] = ()
    rps: dict[str, float] = field(default_factory=dict)  # {"rps5": 92.3, ...}

    @property
    def total_score(self) -> int:
        return sum(s.score for s in self.signals) + self._rps_score

    @property
    def suggestion(self) -> tuple[str, str]:
        """(建议, 置信度)"""
        t = self.total_score
        if t >= 8:   return "强烈买入", "高"
        if t >= 5:   return "买入", "中高"
        if t >= 2:   return "建议买入", "中"
        if t >= 0:   return "观望", "低"
        if t >= -3:  return "谨慎", "中"
        if t >= -6:  return "建议卖出", "中高"
        return "强烈卖出", "高"
```

### 对比

| 现在 SignalScore | 之后 ScoredStock |
|---|---|
| 25 个字段（8 个分数 + 4 个 RPS + 信号列表 + ...） | 核心字段 + `signals: tuple[Signal]` + `rps: dict` |
| `total_score` 需要手动计算和赋值 | `total_score` 是 property，自动求和 |
| `suggestion`, `confidence` 是可变字段 | `suggestion` 是 property |
| `_kline_tail` 隐藏属性 | 去掉，RPS 通过 `rps` dict 显式传入 |
| `signals: list[str]` 只能展示 | `signals: tuple[Signal]` 可被程序消费 |

## 4. 评分函数模式

每个 `scoring/*.py` 遵循同一模式：

```python
# scoring/rsi.py
"""RSI 多空信号"""
import pandas as pd
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal

def score_rsi(ti: TechInd) -> Signal | None:
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

两种返回模式：
- 单信号：`→ Signal | None`（RSI、成交量、布林带、板块）
- 多信号：`→ list[Signal]`（趋势、MACD、KDJ、动量）

screener 统一处理：

```python
# scoring/__init__.py
SCORERS = [
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

所有 scorer 统一签名 `score_xxx(ti, *, code="", ctx=None)`，
不使用的参数直接忽略。这样 `collect_signals` 不需要特殊处理任何 scorer。

## 5. 数据层

### data/spot.py（~60 行）

```python
def get_spot(cfg: Config) -> Result[pd.DataFrame, str]:
    """从东财获取全市场实时行情，自动分页。"""
```

包含 `_em_get()`, `_em_fetch_all_pages()` 等东财 API 细节。

### data/history.py（~80 行）

```python
def get_kline(code: str, days: int, cache: DataCache) -> Result[pd.DataFrame, str]:
    """获取历史 K 线，AKShare 优先，腾讯备用。"""
```

包含 `_tencent_kline()` 备用逻辑。

### data/filters.py（~100 行）

```python
def filter_universe(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """基础过滤：市值、换手率、价格、上市日期、排除规则。"""
    # 合并了 filter_by_spot + filter_stock_list + _apply_inline_filters

def filter_by_sectors(df: pd.DataFrame, top_pct: float = 5.0) -> tuple[pd.DataFrame, dict]:
    """板块过滤：找强势板块 → 只保留成分股。返回 (filtered, market_context)。"""

def filter_by_holdings(df: pd.DataFrame, cfg: Config, spot: pd.DataFrame) -> pd.DataFrame:
    """机构持仓过滤：北向 + 基金。"""
```

## 6. Screener

```python
# screener.py
def screen_stock(code: str, name: str, kline: pd.DataFrame,
                 spot_row: pd.Series | None = None,
                 ctx: dict | None = None) -> ScoredStock | None:
    """对单只股票评分，返回 ScoredStock 或 None（数据不足）。"""
    ti = TechInd(kline)
    signals = collect_signals(ti, code, ctx)
    if not signals:
        return None
    return ScoredStock(
        code=code, name=name,
        price=float(kline["close"].iloc[-1]),
        # ... 其他字段
        signals=tuple(signals),
    )

def screen_universe(universe: pd.DataFrame, cfg: Config,
                    cache: DataCache, ctx: dict | None = None,
                    workers: int = 5) -> list[ScoredStock]:
    """并发评分整个候选池。"""
```

## 7. CLI 管道

```python
# cli.py — 约 60 行
def main():
    cfg = load_config(parse_args())

    if cfg.demo:
        spot_df, klines = generate_demo()
        # demo 路径...
    else:
        spot = get_spot(cfg).unwrap_or_exit()
        universe = filter_universe(spot, cfg)
        universe, ctx = filter_by_sectors(universe)
        universe = filter_by_holdings(universe, cfg, spot)

    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
    results = screen_universe(universe, cfg, cache, ctx, cfg.workers)
    apply_rps(results)

    top = sorted(results, key=lambda s: s.total_score, reverse=True)[:cfg.top_n]
    OutputFormatter().display(top)
```

## 8. Config

```python
# config.py
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
    # 排除规则
    exclude_boards: tuple[str, ...] = ("ST", "退", "北交所")
    exclude_prefixes: tuple[str, ...] = ("8", "4")

def load_config(args: argparse.Namespace | None = None, path: str | None = None) -> Config:
    """CLI 参数 > YAML > 默认值。"""
```

## 9. 测试策略

| 测试文件 | 覆盖范围 |
|---|---|
| `tests/test_score_rsi.py` | RSI 各阈值 |
| `tests/test_score_macd.py` | 金叉/死叉/零轴 |
| `tests/test_score_trend.py` | 均线排列 + 金叉死叉 |
| `tests/test_score_momentum.py` | ROC + 新高/新低 + ADX |
| `tests/test_score_kdj.py` | KDJ 金叉/死叉/超买超卖 |
| `tests/test_score_volume.py` | 放量/缩量 |
| `tests/test_score_bollinger.py` | 布林带位置 |
| `tests/test_screener.py` | screen_stock 组合 |
| `tests/test_config.py` | Config 加载 + 合并 |
| `tests/test_filters.py` | filter_universe + 板块 + 持仓 |
| `tests/test_formatter.py` | 输出格式 |
| `tests/test_cli.py` | 参数解析 + demo |
| `tests/test_rps.py` | RPS 计算 |
| `tests/test_backtest.py` | 回测引擎 |

每个评分测试用 `Signal.name` 断言，不再依赖中文字符串匹配。

## 10. 文件大小目标

| 文件 | 目标行数 |
|---|---|
| config.py | ~60 |
| models.py | ~60 |
| cache.py | ~50 |
| data/spot.py | ~60 |
| data/history.py | ~80 |
| data/filters.py | ~100 |
| indicators/technical.py | ~180 |
| scoring/*.py（每个） | ~25-40 |
| scoring/__init__.py | ~30 |
| screener.py | ~60 |
| backtest.py | ~80 |
| output.py | ~90 |
| demo.py | ~70 |
| cli.py | ~60 |
| **总计** | **~1100 行**（现在 ~1200 行，但结构清晰得多） |
