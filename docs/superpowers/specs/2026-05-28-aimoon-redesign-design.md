# aimoon 大规模重构设计文档

**日期**: 2026-05-28
**状态**: 待审批
**范围**: 全面重构 — 架构改进 + 新功能 + 测试覆盖 + Bug修复

---

## 1. 项目现状

aimoon 是一个A股量化筛选与交易建议系统，当前功能：
- 通过 AKShare 获取实时行情和历史K线数据
- 计算技术指标（MA、RSI、MACD、KDJ、布林带、成交量）
- 基于信号打分并排名股票
- 输出 Rich 表格和 CSV 文件
- CLI 支持 `--demo`、`--top`、`--workers`、`--no-csv`

### 已知问题

1. **`formatter.py:38`** — 样式判断用英文 `"Buy"/"Sell"`，但建议文本是中文（`"买入"`, `"卖出"`），颜色不生效
2. **`cli.py:109`** — 多线程调用 `screener.results.append()` 无锁保护
3. **测试覆盖不足** — 仅有 `test_indicators.py`，screener/formatter/data/cli 均无测试
4. **无数据缓存** — 每次运行重复请求 API
5. **策略不可扩展** — 打分逻辑硬编码在 `StockScreener` 中

---

## 2. 目标架构

```
src/aimoon/
├── __init__.py
├── __main__.py
├── cli.py                  # CLI 入口，支持子命令
├── config.py               # AppConfig + YAML配置加载
├── data.py                 # AKShare 封装 + 缓存集成
├── result.py               # Ok/Err 类型（保持不变）
├── cache/
│   ├── __init__.py
│   └── provider.py         # 文件缓存（pickle + TTL）
├── indicators/
│   ├── __init__.py
│   └── technical.py        # 技术指标（保持不变）
├── strategies/
│   ├── __init__.py
│   ├── base.py             # Strategy ABC
│   ├── technical.py        # TechnicalStrategy（现有打分逻辑）
│   ├── screener.py         # StockScreener 编排器
│   └── backtester.py       # 回测引擎
└── output/
    ├── __init__.py
    └── formatter.py         # Rich 表格 + CSV + 回测报告
```

---

## 3. 缓存层

### 设计

```python
# cache/provider.py
class DataCache:
    """文件缓存，pickle 序列化，TTL 过期。"""

    def __init__(self, cache_dir: str = ".aimoon_cache", ttl_hours: int = 4):
        ...

    def get(self, stock_code: str) -> pd.DataFrame | None:
        """返回缓存的 DataFrame，过期返回 None。"""

    def put(self, stock_code: str, df: pd.DataFrame) -> None:
        """写入 DataFrame 到缓存。"""

    def clear(self) -> None:
        """清除所有缓存文件。"""
```

### 规格

- 缓存目录：`.aimoon_cache/`（加入 `.gitignore`）
- 文件命名：`{stock_code}.pkl`
- TTL：可配置，默认 4 小时（覆盖一个交易日）
- 集成点：`data.py` 的 `get_history_kline()` 先查缓存，未命中再请求 API
- `cli.py` 新增 `cache clear` 子命令

---

## 4. 策略系统

### Strategy ABC

```python
# strategies/base.py
from abc import ABC, abstractmethod

class Strategy(ABC):
    """策略基类，所有打分策略实现此接口。"""

    @abstractmethod
    def score(
        self, code: str, name: str,
        kline: pd.DataFrame, spot: pd.Series | None,
    ) -> SignalScore | None:
        """对单只股票打分，返回 None 表示跳过。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略显示名称。"""
```

### TechnicalStrategy

现有 `StockScreener.screen_stock()` 的打分逻辑迁移到 `TechnicalStrategy.score()`：
- 趋势评分（MA 多头/空头、金叉/死叉）
- RSI 评分（超买/超卖）
- MACD 评分（金叉/死叉、零轴位置）
- KDJ 评分（金叉、超买/超卖）
- 成交量评分（放量/缩量）
- 布林带评分（上轨/下轨位置）

### StockScreener（编排器）

```python
class StockScreener:
    def __init__(self, strategies: list[Strategy] | None = None):
        self.strategies = strategies or [TechnicalStrategy()]
        self.results: list[SignalScore] = []
        self._lock = threading.Lock()  # 线程安全

    def screen_stock(self, code, name, kline, spot) -> SignalScore | None:
        for strategy in self.strategies:
            result = strategy.score(code, name, kline, spot)
            if result:
                with self._lock:
                    self.results.append(result)
                return result
        return None
```

---

## 5. 回测框架

### 数据结构

```python
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
    total_return: float     # 总收益率
    win_rate: float         # 胜率 (0-1)
    max_drawdown: float     # 最大回撤 (0-1)
    trade_count: int        # 交易次数
    trades: list[TradeRecord]
```

### BacktestEngine

```python
class BacktestEngine:
    """在历史K线上逐日回测策略。"""

    def __init__(self, strategy: Strategy, hold_days: int = 5):
        self.strategy = strategy
        self.hold_days = hold_days

    def run(self, code: str, name: str, kline: pd.DataFrame) -> BacktestResult:
        """逐日滚动窗口运行策略，模拟买入持有 hold_days 天后卖出。"""

    def run_batch(self, stocks: dict[str, tuple[str, pd.DataFrame]]) -> list[BacktestResult]:
        """批量回测多只股票。"""
```

### 回测逻辑

1. 从第 `history_days` 天开始，逐日截取历史数据
2. 对每天的数据运行 `strategy.score()`
3. 当策略给出买入信号（`total_score >= 2`）时，模拟买入
4. 持有 `hold_days` 个交易日后卖出
5. 记录每笔交易，计算总收益率、胜率、最大回撤

### CLI 集成

```bash
aimoon backtest --strategy technical --hold-days 5 --stocks 000001,600519
aimoon backtest --strategy technical --top 20  # 对筛选结果回测
```

---

## 6. Bug修复

### 6.1 formatter.py 中文样式修复

```python
# 修复前 (line 38)
ss = "bold green" if "Buy" in r.suggestion or "buy" in r.suggestion.lower() else (...)

# 修复后
ss = "bold green" if "买" in r.suggestion else ("red" if "卖" in r.suggestion else "dim")
```

### 6.2 cli.py 线程安全修复

```python
# 在 StockScreener 中加锁（见第4节）
self._lock = threading.Lock()

def screen_stock(self, ...):
    ...
    with self._lock:
        self.results.append(result)
```

### 6.3 screener.py 方法拆分

将 `screen_stock()` 中 spot_row 字段提取逻辑拆分为 `_extract_spot_fields()` 方法，减少重复代码。

---

## 7. 配置文件支持

### YAML 配置格式

```yaml
# aimoon.yaml
history_days: 250
recent_days: 20
min_market_cap_yi: 50.0
max_market_cap_yi: 2000.0
min_turnover_pct: 3.0
max_turnover_pct: 30.0
min_price: 5.0
max_price: 100.0
top_n: 30
cache_ttl_hours: 4
output_dir: output
```

### 加载逻辑

```python
# config.py
def load_config(path: str | None = None) -> AppConfig:
    """加载配置：默认值 < YAML文件 < 命令行参数。"""
```

- 新增 `pyyaml` 依赖
- `AppConfig` 保持 `frozen=True`
- CLI 新增 `--config` 参数
- 命令行参数优先级最高
- 配置文件不存在时使用默认值并记录警告

---

## 8. 测试计划

| 模块 | 测试文件 | 关键测试点 |
|------|---------|-----------|
| `indicators/technical.py` | `test_indicators.py` | ✅ 已有，补充边界case（空数据、NaN） |
| `strategies/technical.py` | `test_technical_strategy.py` | 各信号触发条件、分数计算、建议生成 |
| `strategies/screener.py` | `test_screener.py` | 多策略编排、线程安全、结果排序 |
| `strategies/backtester.py` | `test_backtester.py` | 收益计算、胜率、最大回撤、空数据处理 |
| `cache/provider.py` | `test_cache.py` | 写入/读取/过期/清理/并发访问 |
| `output/formatter.py` | `test_formatter.py` | 表格生成、CSV导出、中文样式正确性 |
| `data.py` | `test_data.py` | Mock AKShare、过滤逻辑、缓存集成 |
| `cli.py` | `test_cli.py` | 参数解析、demo模式、子命令路由 |

### 测试策略

- 使用 `pytest` + `pytest-cov`
- Mock 外部依赖（AKShare API）
- 目标覆盖率：80%+
- 测试数据使用 `generate_demo()` 生成的模拟数据

---

## 9. 依赖变更

```toml
# pyproject.toml 新增
dependencies = [
    # ... 现有依赖 ...
    "pyyaml>=6.0",    # YAML 配置文件
]
```

---

## 10. 实施顺序

1. **Bug修复** — formatter 中文样式、线程安全、方法拆分
2. **缓存层** — 实现 `DataCache`，集成到 `data.py`
3. **策略系统** — `Strategy` ABC + `TechnicalStrategy` 重构
4. **回测框架** — `BacktestEngine` + CLI 子命令
5. **配置文件** — YAML 支持 + `load_config()`
6. **测试补充** — 按模块逐步补充测试
7. **CLI增强** — 子命令路由、新参数

---

## 11. 不做的事

- 不引入数据库（文件缓存足够）
- 不添加 Web UI（保持 CLI 工具定位）
- 不实现实时推送/告警
- 不添加基本面分析（PE/PB 以外的指标）
- 不引入机器学习模型
