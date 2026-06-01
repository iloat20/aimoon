# aimoon

A股量化筛选与交易建议系统 — 100分制加权评分，动量驱动调仓，Alpha Zoo 截面因子为核心引擎。

部分模块整合自 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)（MIT License, 香港大学数据科学实验室），详见[致谢](#致谢)。

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

运行后将看到30只模拟股票的筛选结果，包含评分、建议。

### 实时筛选

```bash
aimoon                  # 默认筛选全市场（机构持仓池）
aimoon --top 10         # 显示前10名
aimoon --workers 10     # 10线程加速
aimoon --no-alpha       # 禁用 Alpha Zoo 因子
```

---

## 三个核心命令

### 1. 筛选 — `aimoon`

从机构持仓池（北向>=1亿 + 基金>=5% + ROE>10%）中筛选强势股。

```bash
aimoon                      # 默认筛选
aimoon --config my.yaml     # 使用自定义配置
aimoon --demo               # 模拟数据体验
aimoon --no-alpha           # 禁用 Alpha Zoo 因子
```

筛选流程：
1. 机构持仓池过滤（北向资金 + 基金持仓 + ROE + 上市时间）
2. 基本面过滤（市值、换手率、价格区间）
3. 排除规则（ST、退市、北交所）
4. 多线程获取历史 K 线（mootdx TCP → 腾讯 → AKShare，带缓存）
5. 10 个评分器计算 40+ 个信号
6. Alpha Zoo 452 个截面因子计算（默认启用）
7. RPS 多周期排名（3/5/10/15/20/40/60日）
8. **100分制加权评分**（Alpha Zoo 60分 / 动量 20分 / 趋势量价 20分）
9. 输出排名表格 + CSV + Markdown

### 2. 回测 — `aimoon backtest`

动量驱动回测，自动对比沪深300基准。

```bash
aimoon backtest                     # 默认回测
aimoon backtest --stocks 000333,000568,000651,000858,600519  # 指定股票
```

回测引擎特性：
- **动量驱动调仓**：持仓动量衰减时主动退出，新强势股出现时替换入场
- **止损黑名单**：被止损1次的股票永久排除
- **动态ATR止损**：基于14日ATR计算每只股票的止损线（3%-5%）
- **Regime检测**：熊市/高波动自动减仓
- **RPS截面排名**：每轮调仓计算跨股票相对强度

默认参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| hold_days | 5 | 调仓周期 |
| max_positions | 5 | 最大同时持仓数 |
| entry_threshold | 50 | 入场阈值（百分制） |
| exit_ratio | 0.5 | 退出阈值 = entry × 0.5 |
| stop_loss_pct | 0.05 | 止损比例 |
| take_profit_pct | 0.20 | 止盈比例 |
| commission | 0.0003 | 佣金（万三） |
| slippage | 0.001 | 滑点 |
| stamp_tax | 0.0005 | 印花税 |

### 3. 因子评估 — `aimoon evaluate`

用 IC/ICIR 分析评估每个因子的预测能力。

```bash
aimoon evaluate --stocks 000333,000568,000651,000858 --forward-days 5
```

---

## 100分制评分体系

总分 = Alpha Zoo(60分) + 动量(20分) + 趋势量价(20分) = 0~100分

### 权重分配

| 类别 | 权重 | 说明 |
|------|------|------|
| **Alpha Zoo** | **60分** | 452个截面因子（gtja191/alpha101/qlib158/academic） |
| **动量** | 20分 | ROC多周期、RPS排名、加速度、新高新低、ADX |
| **趋势量价** | 20分 | 均线排列、MACD、KDJ、成交量（反向）、布林带、估值 |

### 评分规则

| 总分 | 建议 | 置信度 |
|------|------|--------|
| >= 80 | 强烈买入 | 高 |
| >= 65 | 买入 | 中高 |
| >= 50 | 建议买入 | 中 |
| >= 35 | 观望 | 低 |
| >= 20 | 谨慎 | 中 |
| >= 10 | 建议卖出 | 中高 |
| < 10 | 强烈卖出 | 高 |

### 因子有效性（IC分析）

| 因子 | IC | 方向 | 说明 |
|------|-----|------|------|
| 成交量 | -0.16 | 反向 | 放量预测下跌，缩量预测上涨 |
| Alpha Zoo | — | 正向 | 452因子聚合，核心驱动 |
| 趋势 | +0.03 | 弱正向 | 均线排列有一定预测力 |

成交量是唯一显著的传统技术指标信号（IC=-0.16），系统已将其设为**反向**评分（放量=减分，缩量=加分）。

---

## Alpha Zoo 因子库

整合自 [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) 的 452 个截面 alpha 因子，默认启用。

### 因子来源

| 来源 | 数量 | 说明 |
|------|------|------|
| gtja191 | 191 | 国泰君安 2014 短周期交易型 alpha 因子研报 |
| alpha101 | 101 | Kakushadze "101 Formulaic Alphas" (arXiv:1601.00991) |
| qlib158 | 154 | Microsoft Qlib Alpha158 特征 (Apache-2.0) |
| academic | 6 | Fama-French 5 因子 + Carhart 动量 |

### 工作原理

1. 筛选时收集所有候选股票的 K 线数据
2. 构建宽表（index=日期, columns=股票代码）面板
3. 并行计算所有因子的截面值
4. 取最后一行做截面百分位排名
5. 转换为 aimoon Signal（≥80% → +3, ≥65% → +2, ≤20% → -3, ≤35% → -2）

Alpha 信号归入 `"alpha"` 类别，在100分制中占 **60分** 权重。

---

## 统计验证工具

移植自 Vibe-Trading 的回测验证框架，评估策略稳健性：

```python
from aimoon.validation import run_validation

result = run_validation(
    equity_curve=portfolio.equity_curve,
    trade_returns=[t.return_pct for t in portfolio.trades],
    entry_dates=[t.entry_date for t in portfolio.trades],
    exit_dates=[t.exit_date for t in portfolio.trades],
)
```

| 工具 | 用途 | 输出 |
|------|------|------|
| Monte Carlo | 打乱交易顺序，检验 Sharpe/回撤 p 值 | p_value_sharpe, p_value_max_dd |
| Bootstrap CI | 重采样收益，估计 Sharpe 置信区间 | ci_lower, ci_upper, prob_positive |
| Walk-Forward | 分窗口检验收益一致性 | consistency_rate, 窗口统计 |

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
hold_days: 5                  # 调仓周期
max_positions: 5              # 最大持仓数
entry_threshold: 50           # 入场阈值（百分制）

# 缓存
cache_ttl_hours: 24

# 技术指标
ma_short: 5
ma_mid: 20
ma_long: 60
rsi_period: 10
macd_fast: 10
macd_slow: 20
macd_signal: 6
kdj_period: 10
boll_period: 20
boll_std: 2.0
volume_ma_period: 20

# 排除规则
exclude_boards: ["ST", "退", "北交所"]
exclude_prefixes: ["8", "4"]

# Alpha Zoo
use_alpha: true             # 是否启用 Alpha Zoo 截面因子（默认启用）
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

# 组合回测（含新指标）
engine = BacktestEngine(hold_days=5, max_positions=5)
bt = engine.run_portfolio(klines, names, benchmark_kline=bench_kline)
print(f"收益: {bt.total_return:+.2f}%, Sharpe: {bt.sharpe_ratio:+.2f}")
```

### Alpha Zoo 因子 API

```python
from aimoon.factors.registry import get_default_registry
from aimoon.factors.panel import build_panel
from aimoon.factors.scorer import compute_alpha_signals

registry = get_default_registry()
panel = build_panel(all_klines)
signals = compute_alpha_signals(registry, panel)
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
├── models.py             # Signal / ScoredStock 数据模型（100分制）
├── screener.py           # 并发筛选编排器（支持 Alpha Zoo 注入）
├── backtest.py           # 基础回测引擎（含 RPS、regime、分类上限评分）
├── enhanced_backtest.py  # 增强回测（动量驱动调仓、动态止损、黑名单）
├── validation.py         # 统计验证（Monte Carlo / Bootstrap CI / Walk-Forward）
├── factor_eval.py        # IC/ICIR 因子评估
├── cache.py              # 文件缓存（pickle + TTL）
├── demo.py               # 模拟数据演示
├── output.py             # Rich 表格 + CSV/Markdown 导出
├── result.py             # Ok/Err 结果类型
├── data/
│   ├── spot.py           # 实时行情（东方财富）
│   ├── history.py        # 历史 K 线（mootdx TCP → 腾讯 → AKShare）
│   ├── mootdx_source.py  # mootdx TCP 数据源（通达信协议直连）
│   └── filters.py        # 机构持仓 / 基本面 / 板块过滤
├── factors/              # Alpha Zoo 因子系统（Vibe-Trading 移植）
│   ├── base.py           # 16 个基础算子
│   ├── registry.py       # AST 扫描 + 惰性计算注册表
│   ├── panel.py          # 单股 DataFrame → 宽表面板转换
│   ├── scorer.py         # 截面因子值 → 每股 Signal 转换
│   └── zoo/              # 452 个因子文件
├── indicators/
│   └── technical.py      # 技术指标（MA/MACD/KDJ/BOLL/ROC/ADX/ATR/OBV/VWAP）
└── scoring/
    ├── __init__.py       # 评分注册表 + 100分制加权评分
    ├── momentum.py       # 动量评分（ROC/新高新低/加速/ADX）
    ├── momentum_ext.py   # 扩展动量（量价背离/均值回归/波动率调整）
    ├── rps.py            # RPS 相对价格强度
    ├── trend.py          # 趋势评分（MA排列/金叉死叉）
    ├── trend_ext.py      # 扩展趋势（ADX方向/MACD柱体/EMA斜率）
    ├── combiner.py       # IC 加权 + 行业中性化
    ├── macd.py           # MACD 评分
    ├── kdj.py            # KDJ 评分
    ├── bollinger.py      # 布林带评分
    ├── volume.py         # 成交量评分（反向：放量=看空）
    └── sector.py         # 板块动量评分
```

---

## 致谢

本项目整合了以下开源项目的代码：

### Vibe-Trading

- **项目**: [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)
- **作者**: HKU Data Science Lab (HKUDS), 香港大学
- **许可证**: MIT License
- **整合内容**:
  - **Alpha Zoo 因子库**（`src/aimoon/factors/`）— 452 个量化 alpha 因子
  - **mootdx 数据源**（`src/aimoon/data/mootdx_source.py`）— 通达信 TCP 协议直连
  - **统计验证工具**（`src/aimoon/validation.py`）— Monte Carlo / Bootstrap CI / Walk-Forward
  - **增强回测指标**（`src/aimoon/backtest.py`）— 盈亏因子、最大连续亏损、信息比率
