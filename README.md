# aimoon

A股量化筛选与交易建议系统 — 动量为主、趋势为辅的多因子打分筛选、回测与分析工具。

---

## 快速开始

### 安装

```bash
pip install aimoon
```

或从源码安装：

```bash
git clone https://github.com/iloat20/aimoon.git
cd aimoon
pip install -e .
```

### 30秒体验

```bash
aimoon --demo
```

运行后将看到30只模拟股票的筛选结果，包含评分、建议和触发的信号列表。

### 实时筛选

```bash
aimoon                  # 默认筛选全市场（机构持仓池）
aimoon --top 10         # 显示前10名
aimoon --workers 10     # 10线程加速
```

---

## 三个核心命令

### 1. 筛选 — `aimoon`

从机构持仓池（北向>=1亿 + 基金>=5% + ROE>10%）中筛选强势股。

```bash
aimoon                      # 默认筛选
aimoon --config my.yaml     # 使用自定义配置
aimoon --demo               # 模拟数据体验
```

筛选流程：
1. 机构持仓池过滤（北向资金 + 基金持仓 + ROE + 上市时间）
2. 基本面过滤（市值、换手率、价格区间）
3. 排除规则（ST、退市、北交所）
4. 多线程获取历史 K 线（AKShare + 腾讯备用，带缓存）
5. 10 个评分器计算 40+ 个信号
6. RPS 多周期排名（3/5/10/15/20/40/60日）
7. 输出排名表格 + CSV + Markdown

### 2. 回测 — `aimoon backtest`

组合级回测，自动对比沪深300基准。

```bash
# 单股回测
aimoon backtest --stocks 000001

# 多股组合回测（推荐）
aimoon backtest --stocks 000333,000568,000651,000858,600519

# 自定义参数
aimoon backtest --stocks 000333,000568 --hold-days 20 --max-positions 2
```

默认参数（经回测优化）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| hold_days | 20 | 持仓天数 |
| max_positions | 2 | 最大同时持仓数 |
| commission | 0.0003 | 佣金（万三） |
| slippage | 0.001 | 滑点 |
| stamp_tax | 0.0005 | 印花税 |

回测输出：
- 总收益率 / 年化收益率
- 夏普比率 / Calmar 比率
- 最大回撤 / 胜率
- 沪深300基准对比 / 超额收益

### 3. 因子评估 — `aimoon evaluate`

用 IC/ICIR 分析评估每个因子的预测能力。

```bash
aimoon evaluate --stocks 000333,000568,000651,000858 --forward-days 5
```

输出每个因子的：
- **Mean IC** — 因子值与未来收益的秩相关（>0.03 为有效）
- **ICIR** — IC/标准差，衡量稳定性（>0.5 为稳定）
- **IC>0%** — IC 为正的比例
- **L-S** — 多空收益差（top组 - bottom组）
- **五分位收益** — 按因子值分5组的平均收益

---

## 评分体系

总分 = 各信号分数之和。动量因子权重最高，趋势因子辅助，其他因子补充。

### 动量因子（核心）— 2 个评分器，18 类信号

| 因子 | 看多 | 看空 |
|------|------|------|
| ROC 3/5/10/20/40/60/120日 | 强势(+5%+): +1~4 | 弱势(-5%-): -1~4 |
| RPS 多周期 (3/5/10/15/20/40/60日) | 3-4周期翻红: +5, 双线红: +3 | — |
| 长周期动量确认 (3/40/60/120日) | >=3周期强势: +3 | — |
| 波动率调整动量 (ROC/ATR) | 高性价比: +2 | 下跌波动大: -2 |
| 动量持久度 (20日正收益占比) | >70%: +2 | <30%: -2 |
| 上涨/下跌成交量比 | >1.5: +2 | <0.7: -2 |
| OBV 趋势 (10日斜率) | 上升: +2 | 下降: -2 |
| VWAP 偏离 (20日) | 强于VWAP: +2 | 弱于VWAP: -2 |
| 收益偏度 (20日) | 正偏: +1 | 负偏: -1 |
| 新高新低比 (60日) | 净新高: +2 | 净新低: -2 |
| 最大回撤恢复 (60日) | 深跌后反弹: +2 | — |
| 动量加速 | 加速: +3 | 减速: -3 |
| 5/10/20日新高 | 新高: +1~3 | 5/10/20日新低: -1~3 |
| ADX (>25) | 强趋势: +2 | — |
| 超跌保护 (5日<-10%) | — | 暴跌风险: -3 |

### 趋势因子（辅助）— 2 个评分器，9 类信号

| 因子 | 看多 | 看空 |
|------|------|------|
| MA5/10/20/60 排列 | 完美多头: +3 | 空头: -2 |
| MA 金叉/死叉 | 金叉: +2 | 死叉: -2 |
| 价格 vs MA20+MA60 | 均在上方: +2 | 均在下方: -2 |
| ADX 方向 (+DI/-DI) | 强多头: +2 | 强空头: -2 |
| MACD 连续柱体 | 连续红柱: +2 | 连续绿柱: -2 |
| EMA20 斜率 | 上升: +1 | 下降: -1 |
| MACD 金叉/死叉 | 金叉: +2 | 死叉: -2 |
| MACD 零轴 | 零轴上方: +1 | 零轴下方: -1 |

### 其他因子 — 6 个评分器

| 评分器 | 看多 | 看空 |
|--------|------|------|
| RSI (14日) | 强势(>60): +2 | 弱势(<40): -2 |
| KDJ | 金叉: +1, 超卖: +1 | 死叉: -1, 超买: -1 |
| 成交量 | 放量(2x+): +2 | 缩量: -1 |
| 布林带 | 触及下轨: +1 | 触及上轨: -1 |
| 板块动量 | 强势板块(Top N%): +3 | — |

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

## 回测表现

以188只机构持仓池股票为标的，最近1年数据，对比沪深300（+8.17%）：

| 参数 | 总收益 | 超额 | Sharpe | 最大回撤 | 胜率 |
|------|--------|------|--------|---------|------|
| **hold=20, top=2** | **+60.2%** | **+52.0%** | **+2.46** | **3.2%** | **56%** |
| hold=10, top=5 | +84.6% | +76.5% | +2.60 | 16.9% | 59% |
| hold=5, top=10 | +76.0% | +67.8% | +2.71 | 18.1% | 51% |
| hold=10, top=10 | +53.6% | +45.4% | +2.39 | 14.2% | 56% |

默认使用 `hold=20, top=2`（最优风险调整收益）。

---

## 输出文件

每次筛选自动导出到 `output/` 目录：

- `aimoon_YYYYMMDD_HHMMSS.csv` — CSV 格式
- `aimoon_YYYYMMDD_HHMMSS.md` — Markdown 格式

---

## 配置文件

```bash
aimoon --config my_config.yaml
```

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
min_list_days: 250

# 机构持仓
min_northbound_cap: 1.0       # 北向持股市值（亿元）
min_fund_pct: 5.0             # 基金持股占比（%）

# 回测参数
hold_days: 20                 # 持仓天数
max_positions: 2              # 最大持仓数

# 缓存
cache_ttl_hours: 24

# 技术指标
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
exclude_boards: ["ST", "退", "北交所"]
exclude_prefixes: ["8", "4"]
```

参数优先级：`命令行参数 > 配置文件 > 默认值`

---

## Python API

```python
from aimoon.data.spot import get_spot
from aimoon.data.history import get_kline
from aimoon.data.filters import filter_universe
from aimoon.screener import screen_stock
from aimoon.scoring.rps import compute_rps
from aimoon.backtest import BacktestEngine

# 筛选单只股票
kline = get_kline("000001").unwrap()
result = screen_stock("000001", "平安银行", kline)
print(f"评分: {result.total_score}, 建议: {result.suggestion}")

# 组合回测
engine = BacktestEngine(hold_days=20, max_positions=2)
bt = engine.run_portfolio(klines, names)
print(f"收益: {bt.total_return:+.2f}%, Sharpe: {bt.sharpe_ratio:+.2f}")
```

---

## 缓存管理

K线数据和机构持仓数据缓存到 `.aimoon_cache/` 目录。

```bash
aimoon cache clear    # 清除所有缓存
aimoon update         # 清除缓存并重新获取数据
```

---

## 项目结构

```
src/aimoon/
├── cli.py               # CLI 入口（筛选/回测/评估/缓存）
├── config.py             # 配置管理（frozen dataclass + YAML）
├── models.py             # Signal / ScoredStock 数据模型
├── screener.py           # 并发筛选编排器
├── backtest.py           # 回测引擎（单股 + 组合级）
├── factor_eval.py        # IC/ICIR 因子评估
├── cache.py              # 文件缓存（pickle + TTL）
├── demo.py               # 模拟数据演示
├── output.py             # Rich 表格 + CSV/Markdown 导出
├── result.py             # Ok/Err 结果类型
├── data/
│   ├── spot.py           # 实时行情（东方财富）
│   ├── history.py        # 历史 K 线（AKShare + 腾讯备用）
│   └── filters.py        # 机构持仓 / 基本面 / 板块过滤
├── indicators/
│   └── technical.py      # 技术指标（MA/RSI/MACD/KDJ/BOLL/ROC/ADX/ATR/OBV/VWAP）
└── scoring/
    ├── momentum.py       # 动量评分（ROC/新高新低/加速/ADX）
    ├── momentum_ext.py   # 扩展动量（波动率调整/持久度/OBV/VWAP/偏度/回撤恢复）
    ├── rps.py            # RPS 相对价格强度（7周期）
    ├── trend.py          # 趋势评分（MA排列/金叉死叉）
    ├── trend_ext.py      # 扩展趋势（均线排列度/ADX方向/MACD柱体/EMA斜率）
    ├── combiner.py       # IC 加权 + 行业中性化
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

**Q: 回测结果和实盘不一样？**

回测使用收盘价成交，无流动性限制。实盘需考虑：滑点更大、涨跌停无法成交、停牌等因素。

**Q: 如何自定义评分器？**

```python
from aimoon.scoring import SCORERS
from aimoon.models import Signal

def my_scorer(ti, code="", ctx=None):
    if ti.roc_signal(20) > 10:
        return Signal("my_signal", "自定义信号", +3)
    return None

SCORERS.append(my_scorer)
```
