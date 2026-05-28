# aimoon 使用文档

A股量化筛选与交易建议系统 — 基于技术指标的多因子打分筛选、回测与分析工具。

---

## 快速开始

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd aimoon

# 安装（推荐 editable 模式）
pip install -e .
```

### 30秒体验

```bash
# 使用模拟数据体验（无需网络）
aimoon --demo

# 或直接运行模块
python -m aimoon --demo
```

运行后将看到30只模拟股票的筛选结果表格，包含评分、建议和信号。

---

## 基本用法

### 实时筛选（默认模式）

```bash
# 使用默认参数筛选
aimoon

# 显示前10名
aimoon --top 10

# 使用10个线程加速
aimoon --workers 10

# 不导出CSV
aimoon --no-csv
```

筛选流程：
1. 从东方财富获取A股实时行情
2. 按市值、换手率、价格过滤
3. 排除ST、退市、北交所、8/4开头股票
4. 多线程并行获取历史K线数据（带缓存）
5. 计算技术指标并打分
6. 输出排名表格和CSV文件

### 输出说明

| 列 | 含义 |
|---|------|
| No. | 排名 |
| Code | 股票代码 |
| Name | 股票名称 |
| Price | 最新价 |
| Chg% | 涨跌幅 |
| Turnover% | 换手率 |
| Score | 综合评分（越高越看多） |
| Suggestion | 操作建议 |
| Conf. | 置信度 |
| Signals | 触发的信号列表 |

**评分体系（总分 = 各指标之和）：**

| 指标 | 看多信号 | 看空信号 |
|------|---------|---------|
| 趋势 (MA) | 均线多头+2, 金叉+2 | 空头-2, 死叉-2 |
| RSI | 超卖+2 | 超买-2 |
| MACD | 金叉+2, 零轴上方+1 | 死叉-2, 零轴下方-1 |
| KDJ | 金叉+2, 超卖+1 | 超买-1 |
| 成交量 | 放量+2/+1 | 缩量-1 |
| 布林带 | 触及下轨+1 | 触及上轨-1 |

**建议对照：**

| 总分 | 建议 | 置信度 |
|------|------|--------|
| >= 6 | 强烈买入 | 高 |
| >= 4 | 买入 | 中高 |
| >= 2 | 建议买入 | 中 |
| >= 0 | 观望 | 低 |
| >= -2 | 谨慎 | 中 |
| >= -4 | 建议卖出 | 中高 |
| < -4 | 强烈卖出 | 高 |

---

## 回测

在历史数据上模拟策略表现，计算收益率、胜率和最大回撤。

```bash
# 回测单只股票（默认持仓5天）
aimoon backtest --stocks 000001

# 回测多只股票
aimoon backtest --stocks 000001,600519,300750

# 指定持仓天数
aimoon backtest --stocks 000001 --hold-days 10
```

输出示例：
```
=== Backtest: technical (hold 5d) ===
Backtesting 3 stocks...
  000001: +3.21% 胜率=60% 交易=5次 最大回撤=4.53%
  600519: -1.05% 胜率=40% 交易=5次 最大回撤=6.21%
  300750: +5.67% 胜率=71% 交易=7次 最大回撤=3.89%
```

回测逻辑：
- 从第60天开始逐日运行策略
- 当策略给出买入信号（总分>=2）时模拟买入
- 持有指定天数后卖出
- 计算总收益率、胜率（盈利交易占比）、最大回撤

---

## 缓存管理

K线数据缓存到 `.aimoon_cache/` 目录，默认4小时过期（同一交易日内不重复请求）。

```bash
# 清除所有缓存
aimoon cache clear
```

---

## 配置文件

支持 YAML 格式配置文件，覆盖默认参数。

```bash
# 使用指定配置文件
aimoon --config my_config.yaml
```

### 配置示例

创建 `my_config.yaml`：

```yaml
# 筛选参数
history_days: 250          # 历史数据天数
min_market_cap_yi: 50.0    # 最小市值（亿元）
max_market_cap_yi: 2000.0  # 最大市值（亿元）
min_turnover_pct: 3.0      # 最小换手率（%）
max_turnover_pct: 30.0     # 最大换手率（%）
min_price: 5.0             # 最低股价
max_price: 100.0           # 最高股价
top_n: 30                  # 输出前N名

# 缓存
cache_ttl_hours: 4         # 缓存过期时间（小时）

# 输出
output_dir: output         # CSV输出目录

# 技术指标参数
ma_short: 5                # 短期均线
ma_mid: 20                 # 中期均线
ma_long: 60                # 长期均线
rsi_period: 14             # RSI周期
macd_fast: 12              # MACD快线
macd_slow: 26              # MACD慢线
macd_signal: 9             # MACD信号线
kdj_period: 9              # KDJ周期
boll_period: 20            # 布林带周期
boll_std: 2.0              # 布林带标准差倍数
volume_ma_period: 20       # 成交量均线周期

# 排除规则
exclude_boards:
  - "ST"
  - "退"
  - "北交所"
exclude_prefixes:
  - "8"
  - "4"
```

### 参数优先级

```
命令行参数 > 配置文件 > 默认值
```

---

## 策略系统

aimoon 支持可插拔的策略系统。内置 `TechnicalStrategy`（技术指标多因子策略），也可以自定义。

### 自定义策略

```python
from aimoon.strategies.base import Strategy
from aimoon.strategies.screener import SignalScore
import pandas as pd


class MyStrategy(Strategy):
    """自定义策略示例"""

    @property
    def name(self) -> str:
        return "my_strategy"

    def score(self, code, name, kline, spot=None):
        # 你的打分逻辑
        if len(kline) < 60:
            return None
        last_close = float(kline["close"].iloc[-1])
        ma60 = float(kline["close"].rolling(60).mean().iloc[-1])

        total = 0
        signals = []
        if last_close > ma60:
            total += 2
            signals.append("价格在60日均线上方")
        else:
            total -= 2
            signals.append("价格在60日均线下方")

        return SignalScore(
            stock_code=code,
            stock_name=name,
            price=last_close,
            pct_change=0.0,
            turnover=0.0,
            total_score=total,
            signals=signals,
            suggestion="买入" if total >= 2 else "观望",
            confidence="中" if total >= 2 else "低",
        )
```

### 使用自定义策略

```python
from aimoon.strategies.screener import StockScreener

screener = StockScreener(strategies=[MyStrategy()])
# ... 正常使用 screener.screen_stock()
```

---

## Python API

除了命令行，也可以在 Python 中使用：

```python
from aimoon.data import get_spot_data, get_history_kline, filter_by_spot
from aimoon.strategies.screener import StockScreener
from aimoon.strategies.technical import TechnicalStrategy
from aimoon.strategies.backtester import BacktestEngine

# 获取数据
spot = get_spot_data().unwrap()
filtered = filter_by_spot(spot)

# 筛选
screener = StockScreener()
kline = get_history_kline("000001").unwrap()
result = screener.screen_stock("000001", "平安银行", kline)
if result:
    print(f"{result.stock_name}: {result.total_score}分, {result.suggestion}")

# 回测
strategy = TechnicalStrategy()
engine = BacktestEngine(strategy, hold_days=5)
bt = engine.run("000001", "平安银行", kline)
print(f"收益率: {bt.total_return:+.2f}%, 胜率: {bt.win_rate:.0%}")
```

---

## 项目结构

```
aimoon/
├── src/aimoon/
│   ├── cli.py               # CLI入口
│   ├── config.py             # 配置管理
│   ├── data.py               # 数据获取（AKShare + 缓存）
│   ├── result.py             # Ok/Err结果类型
│   ├── cache/
│   │   └── provider.py       # 文件缓存（pickle + TTL）
│   ├── indicators/
│   │   └── technical.py      # 技术指标计算
│   ├── strategies/
│   │   ├── base.py           # Strategy ABC
│   │   ├── technical.py      # 技术指标打分策略
│   │   ├── screener.py       # 策略编排器
│   │   └── backtester.py     # 回测引擎
│   └── output/
│       └── formatter.py      # Rich表格 + CSV导出
├── tests/                    # 测试（39个）
├── pyproject.toml
└── README.md
```

---

## 常见问题

**Q: 运行时提示网络错误？**

检查网络连接。AKShare 依赖东方财富接口，需要能访问 `eastmoney.com`。

**Q: 如何只筛选特定板块？**

编辑配置文件中的 `exclude_prefixes` 和 `exclude_boards`，移除不需要排除的项目。

**Q: 缓存在哪里？**

默认在项目根目录的 `.aimoon_cache/` 文件夹，已加入 `.gitignore`。

**Q: 如何添加新的技术指标？**

在 `src/aimoon/indicators/technical.py` 的 `TechnicalIndicators` 类中添加方法，然后在 `src/aimoon/strategies/technical.py` 的 `TechnicalStrategy` 中使用它。

**Q: CSV文件输出到哪里？**

默认输出到 `output/` 目录，文件名格式 `screen_YYYYMMDD_HHMMSS.csv`。可通过配置文件的 `output_dir` 修改。
