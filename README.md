# aimoon 使用文档

A股量化筛选与交易建议系统 — 基于技术指标的多因子打分筛选、回测与分析工具。

---

## 快速开始

### 安装

```bash
git clone <repo-url>
cd aimoon
pip install -e .
```

### 30秒体验

```bash
aimoon --demo
# 或
python -m aimoon --demo
```

运行后将看到30只模拟股票的筛选结果表格，包含评分、建议和信号。

---

## 基本用法

### 实时筛选（默认模式）

```bash
aimoon                  # 默认参数筛选
aimoon --top 10         # 显示前10名
aimoon --workers 10     # 10个线程加速
aimoon --no-csv         # 不导出文件
```

### 筛选流程

1. 从东方财富获取全市场实时行情（含上市日期字段 f26）
2. **上市时间过滤** — 排除上市不满1年的股票
3. **基本面过滤** — 按市值、换手率、价格区间筛选
4. **排除规则** — ST、退市、北交所、8/4开头股票
5. **北向资金过滤** — 北向持股市值 >= 1亿元
6. **基金持仓过滤** — 基金持股占流通股 >= 5%
7. 多线程并行获取历史K线数据（带缓存，AKShare + 腾讯备用）
8. 计算技术指标并打分
9. **RPS 计算** — 从K线数据计算 5/10/15/20 日涨幅排名
10. 输出排名表格、CSV 和 Markdown 文件

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
| RPS5/10/15/20 | 相对价格强度（0-100，>90为强势） |
| Suggestion | 操作建议 |
| Conf. | 置信度 |
| Signals | 触发的信号列表 |

---

## 评分体系

总分 = 各指标之和（动量为主，RPS 权重5，技术指标为辅）

### 技术指标（动量为主）

| 指标 | 看多信号 | 看空信号 |
|------|---------|---------|
| **动量 ROC5** | 强势(+5%+)+4, 上升(+2%+)+2 | 弱势(-5%-)-4, 下降(-2%-)-2 |
| **动量 ROC10** | 强势(+5%+)+2, 上升(+2%+)+1 | 弱势(-5%-)-2, 下降(-2%-)-1 |
| **动量 ROC20** | 强势(+5%+)+1, 上升+1 | 弱势(-5%-)-1, 下降-1 |
| **动量加速** | 加速+3, 偏强+1 | 减速-3, 偏弱-1 |
| **新高/新低** | 5日新高+3, 10日+2, 20日+1 | 5日新低-3, 10日-2, 20日-1 |
| **ADX** | 强趋势(>25)+2 | — |
| 趋势 (MA) | 均线多头+2, 金叉+2 | 空头-2, 死叉-2 |
| MACD | 金叉+2, 零轴上方+1 | 死叉-2, 零轴下方-1 |
| RSI | 强势(>60)+2, 偏多+1 | 弱势(<40)-2, 偏空-1 |
| KDJ | 金叉+1, 超卖+1 | 死叉-1, 超买-1 |
| 成交量 | 放量(2x+)+2, 温和放量+1 | 缩量-1 |
| 布林带 | 触及下轨+1 | 触及上轨-1 |
| 板块动量 | 强势板块+3, 全市场Top%+3 | — |

### RPS（相对价格强度）— 权重5

RPS 衡量股票近 N 日涨幅在全部股票中的排名百分位（0-100）。

| RPS 值 | 含义 |
|--------|------|
| > 90 | 涨幅超过 90% 的股票，极度强势 |
| > 80 | 涨幅超过 80% 的股票，强势 |
| < 20 | 涨幅低于 80% 的股票，弱势 |

**RPS 加分：**

| 条件 | 加分 | 信号 |
|------|------|------|
| 3-4个周期 RPS > 90 | +5 | RPS三线翻红(3/4 或 4/4) |
| 2个周期 RPS > 90 | +3 | RPS双线红(2/4) |

### 建议对照

| 总分 | 建议 | 置信度 |
|------|------|--------|
| >= 8 | 强烈买入 | 高 |
| >= 5 | 买入 | 中高 |
| >= 2 | 建议买入 | 中 |
| >= 0 | 观望 | 低 |
| >= -3 | 谨慎 | 中 |
| >= -6 | 建议卖出 | 中高 |
| < -6 | 强烈卖出 | 高 |

---

## 输出文件

每次运行自动生成两个文件到 `output/` 目录：

- `screen_YYYYMMDD_HHMMSS.csv` — CSV 格式，可用 Excel 打开
- `screen_YYYYMMDD_HHMMSS.md` — Markdown 格式，包含完整 RPS 数据和信号

---

## 回测

```bash
aimoon backtest --stocks 000001                   # 回测单只股票
aimoon backtest --stocks 000001,600519,300750     # 回测多只
aimoon backtest --stocks 000001 --hold-days 10    # 指定持仓天数
```

回测逻辑：
- 从第60天开始逐日运行策略
- 买入信号：总分 >= 2
- 持有指定天数后卖出
- 输出：总收益率、胜率、最大回撤

---

## 缓存管理

K线数据缓存到 `.aimoon_cache/` 目录，默认4小时过期。

```bash
aimoon cache clear    # 清除所有缓存
```

---

## 配置文件

```bash
aimoon --config my_config.yaml
```

### 配置示例

```yaml
# 筛选参数
history_days: 250
min_market_cap_yi: 50.0
max_market_cap_yi: 2000.0
min_turnover_pct: 3.0
max_turnover_pct: 30.0
min_price: 5.0
max_price: 100.0
top_n: 30
min_list_days: 250            # 上市天数（约1年）

# 机构持仓
min_northbound_cap: 1.0       # 北向持股市值（亿元）
min_fund_pct: 5.0             # 基金持股占比（%）

# 缓存
cache_ttl_hours: 4

# 输出
output_dir: output

# 技术指标参数
ma_short: 5
ma_mid: 20
ma_long: 60
rsi_period: 14
macd_fast: 12
macd_slow: 26
macd_signal: 9
kdj_period: 9
boll_period: 20
boll_std: 2.0
volume_ma_period: 20

# 排除规则
exclude_boards:
  - "ST"
  - "退"
  - "北交所"
exclude_prefixes:
  - "8"
  - "4"
```

参数优先级：`命令行参数 > 配置文件 > 默认值`

---

## 评分系统

评分采用函数注册表模式，每个评分器是一个独立函数，返回 `Signal` 或 `None`。

```python
from aimoon.indicators.technical import TechInd
from aimoon.models import Signal
from aimoon.scoring import SCORERS, collect_signals

# 内置评分器列表
# score_momentum, score_rps, score_trend, score_macd,
# score_rsi, score_kdj, score_bollinger, score_volume, score_sector

# 自定义评分器
def my_scorer(ti: TechInd, code: str = "", ctx: dict | None = None) -> Signal | None:
    close = ti.kline["close"]
    if close.iloc[-1] > close.rolling(60).mean().iloc[-1]:
        return Signal(label="MA60上方", weight=2, direction=1)
    return None

# 注册并运行
SCORERS.append(my_scorer)
signals = collect_signals(ti, code="000001")
```

---

## Python API

```python
from aimoon.data.spot import get_spot
from aimoon.data.history import get_kline
from aimoon.data.filters import filter_universe
from aimoon.screener import screen_stock
from aimoon.scoring.rps import compute_rps
from aimoon.backtest import BacktestEngine

# 获取数据
spot = get_spot()
filtered = filter_universe(spot)

# 筛选单只股票
kline = get_kline("000001").unwrap()
result = screen_stock("000001", "平安银行", kline)

# 回测
engine = BacktestEngine(hold_days=5)
bt = engine.run("000001", "平安银行", kline)
print(f"收益率: {bt.total_return:+.2f}%, 胜率: {bt.win_rate:.0%}")
```

---

## 项目结构

```
src/aimoon/
├── cli.py               # CLI 入口
├── config.py             # 配置管理（AppConfig + YAML 加载）
├── result.py             # Ok/Err 结果类型
├── models.py             # 数据模型
├── cache.py              # 文件缓存（pickle + TTL）
├── screener.py           # 筛选编排器
├── backtest.py           # 回测引擎
├── demo.py               # 模拟数据演示
├── output.py             # Rich 表格 + CSV/Markdown 导出
├── data/
│   ├── spot.py           # 实时行情（东方财富）
│   ├── history.py        # 历史 K 线（AKShare + 腾讯备用）
│   └── filters.py        # 基本面 / 北向 / 基金过滤
├── indicators/
│   └── technical.py      # 技术指标计算（MA/RSI/MACD/KDJ/BOLL/ROC/ADX）
└── scoring/
    ├── momentum.py       # 动量评分（ROC/新高新低/加速）
    ├── rps.py            # 相对价格强度
    ├── trend.py          # 趋势评分（MA/ADX）
    ├── macd.py           # MACD 评分
    ├── rsi.py            # RSI 评分
    ├── kdj.py            # KDJ 评分
    ├── bollinger.py      # 布林带评分
    ├── volume.py         # 成交量评分
    └── sector.py         # 板块动量评分
```

---

## 常见问题

**Q: 网络错误？**

AKShare 可能不稳定，系统会自动切换到腾讯 K 线接口。确保能访问 `eastmoney.com` 和 `gtimg.cn`。

**Q: 北向/基金数据获取失败？**

这些数据来自东方财富 datacenter，不影响核心筛选，获取失败时自动跳过。

**Q: RPS 值全是 0？**

RPS 从 K 线数据计算，需要至少 21 天数据。缓存可能导致数据不足，运行 `aimoon cache clear` 后重试。

**Q: CSV/Markdown 输出到哪里？**

默认 `output/` 目录，文件名格式 `screen_YYYYMMDD_HHMMSS.csv/.md`。可通过 `output_dir` 配置修改。
