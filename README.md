# aimoon

> A 股量化筛选与交易建议系统 — 452 因子 + ML 集成 + 自学习权重 + 交易策略引擎

[![Python 3.13+](https://img.shields.io/badge/Python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-0.2.2-orange.svg)](pyproject.toml)
[![uv](https://img.shields.io/badge/uv-managed-4051b5.svg)](https://docs.astral.sh/uv/)

---

## 项目简介

aimoon 是一个面向 A 股市场的量化筛选与交易建议系统。它通过 452 个 Alpha Zoo 因子、XGBoost/LightGBM 集成学习、自适应 ICIR 权重和因子衰减检测，为用户提供每日选股排名与交易计划。

### 核心亮点

| 特性 | 说明 |
|------|------|
| **452 Alpha Zoo 因子** | 覆盖 gtja191、alpha101、qlib158、学术因子 + 5 组私有因子（25 个子因子） |
| **ML 集成排名** | XGBoost + LightGBM 并行训练，IC 加权集成，Purged TimeSeriesSplit 防前瞻偏差 |
| **自学习系统** | ICIR 动态因子加权、因子衰减检测（CUSUM）、集成权重自适应（24h-7d 缓存） |
| **三级数据源兜底** | mootdx（TCP 直连）→ Tencent（HTTP）→ AKShare（HTTP），确保数据可用性 |
| **完整交易策略** | Kelly 准则仓位管理、阶梯移动止损、Regime 自适应离场、Walk-Forward 验证 |
| **安全缓存** | 全部使用 JSON 序列化（消除 pickle CWE-502 漏洞），原子写入防崩溃损坏 |

---

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | ≥ 3.13 | 使用现代类型注解语法（`int \| None`），uv 自动管理 |
| TA-Lib | ≥ 0.4 | C 库，需先安装系统依赖（见下方提示） |
| 内存 | ≥ 4 GB | 452 因子计算需要较多内存 |
| 磁盘 | ≥ 1 GB | 缓存目录 `.aimoon_cache/` |

> **提示：TA-Lib 安装**
>
> TA-Lib 是 C 语言库的 Python 封装，需要先安装系统级依赖：
>
> ```bash
> # Windows（推荐使用 conda）
> conda install -c conda-forge ta-lib
>
> # macOS
> brew install ta-lib
>
> # Ubuntu/Debian
> sudo apt-get install ta-lib
>
> # 然后安装 Python 包
> uv pip install ta-lib
> ```
>
> 如果安装失败，可以跳过 TA-Lib，系统会自动降级到 pandas-ta 实现。

---

## 安装步骤

### 方式一：从 PyPI 安装

```bash
uv pip install aimoon
```

### 方式二：从源码安装（推荐，使用 uv）

```bash
git clone https://github.com/iloat20/aimoon.git
cd aimoon
uv venv --python 3.13 .venv
uv pip install -e .
```

> 如果未安装 uv，先执行 `pip install uv`。uv 会自动下载 Python 3.13，无需系统级 Python 安装。

### 验证安装

```bash
aimoon --help
aimoon --demo  # 快速验证，无需训练模型
```

> **注意：首次运行会自动下载数据**
>
> 首次运行需要从网络获取股票数据和机构持仓信息，请确保网络连接正常。数据获取失败时会自动尝试备用数据源。

---

## 快速开始

### 30 秒体验（Demo 模式）

```bash
aimoon --demo
```

Demo 模式使用持仓池真实股票代码，跳过 ML 模型训练，使用简化评分。适合快速体验系统功能。

输出示例：

```
┌────┬────────┬──────────┬────────┬───────┬──────┬────────────┬───────┐
│ No │ Code   │ Name     │  Price │  Chg% │ Score│ Suggestion │ Conf. │
├────┼────────┼──────────┼────────┼───────┼──────┼────────────┼───────┤
│ 1  │ 002568 │ 百润股份 │  20.36 │ +0.00 │   76 │ 强烈买入   │ 高    │
│ 2  │ 001286 │ 陕西能源 │  12.23 │ +0.00 │   76 │ 强烈买入   │ 高    │
│ 3  │ 000333 │ 美的集团 │  83.23 │ +0.00 │   72 │ 买入       │ 中    │
└────┴────────┴──────────┴────────┴───────┴──────┴────────────┴───────┘
```

### 完整筛选（需要训练模型）

```bash
# 第一步：训练模型（首次需要，后续增量更新）
aimoon train-model

# 第二步：（可选）添加自选股票
aimoon watchlist add 600519,000858

# 第三步：运行筛选
aimoon

# 查看筛选结果
ls output/
# aimoon_20260615_105750.csv
# aimoon_20260615_105750.md
```

### 回测交易策略

```bash
# 使用默认参数回测
aimoon backtest

# Walk-Forward 验证（更严格）
aimoon backtest --walk-forward
```

---

## 详细配置说明

### 配置优先级

```
命令行参数 > YAML 配置文件 > 默认值
```

### 配置文件示例

创建 `my_config.yaml`：

```yaml
# ── 筛选参数 ──
history_days: 250          # 历史 K 线天数
top_n: 20                  # 显示前 N 只股票
min_market_cap_yi: 10.0    # 最小市值（亿元）
max_market_cap_yi: 10000.0 # 最大市值（亿元）
min_price: 0.0             # 最低价格
max_price: 99999.0         # 最高价格
min_turnover_pct: 0.0      # 最小换手率（%）
max_turnover_pct: 100.0    # 最大换手率（%）

# ── 估值过滤 ──
max_pe_ttm: 26.0           # 最大市盈率（TTM）
max_pb: 10.0               # 最大市净率
min_dividend_yield: 1.5    # 最小股息率（%）

# ── 机构持仓过滤 ──
min_northbound_cap: 1.0    # 最小北向持仓（亿元）
min_fund_pct: 5.0          # 最小基金持仓比例（%）

# ── 交易策略 ──
entry_threshold: 55        # ML 分数入场阈值
stop_loss_pct: 0.04        # 止损比例
take_profit_pct: 0.15      # 止盈比例
hold_days: 10              # 持仓天数
max_positions: 5           # 最大持仓数

# ── 风控参数 ──
max_position_pct: 0.10     # 单只股票最大仓位
max_sector_pct: 0.30       # 单行业最大暴露
max_drawdown_limit: 0.15   # 最大回撤限制

# ── ML 与缓存 ──
use_alpha: true            # 启用 Alpha Zoo 因子
cache_ttl_hours: 24        # 缓存过期时间（小时）
workers: 20                # 并行工作线程数

# ── 排除规则 ──
exclude_boards:            # 排除的板块
  - "ST"
  - "退"
  - "北交所"
exclude_prefixes:          # 排除的代码前缀
  - "8"                    # 北交所
  - "4"                    # 老三板
```

使用配置文件：

```bash
aimoon --config my_config.yaml
```

### 全部参数一览

| 类别 | 参数 | 类型 | 默认值 | 说明 |
|------|------|------|--------|------|
| **筛选** | `history_days` | int | 250 | 历史 K 线天数 |
| | `top_n` | int | 20 | 显示前 N 只 |
| | `min_market_cap_yi` | float | 10.0 | 最小市值（亿元） |
| | `max_market_cap_yi` | float | 10000.0 | 最大市值（亿元） |
| | `min_price` | float | 0.0 | 最低价格 |
| | `max_price` | float | 99999.0 | 最高价格 |
| | `min_turnover_pct` | float | 0.0 | 最小换手率（%） |
| | `max_turnover_pct` | float | 100.0 | 最大换手率（%） |
| | `min_list_days` | int | 250 | 最小上市天数 |
| **估值** | `max_pe_ttm` | float | 26.0 | 最大市盈率（TTM） |
| | `max_pb` | float | 10.0 | 最大市净率 |
| | `min_dividend_yield` | float | 1.5 | 最小股息率（%） |
| **机构持仓** | `min_northbound_cap` | float | 1.0 | 最小北向持仓（亿元） |
| | `min_fund_pct` | float | 5.0 | 最小基金持仓比例（%） |
| **技术指标** | `ma_short` | int | 5 | 短期均线周期 |
| | `ma_mid` | int | 20 | 中期均线周期 |
| | `ma_long` | int | 60 | 长期均线周期 |
| | `rsi_period` | int | 10 | RSI 周期 |
| | `macd_fast` | int | 10 | MACD 快线 |
| | `macd_slow` | int | 20 | MACD 慢线 |
| | `macd_signal` | int | 6 | MACD 信号线 |
| **交易策略** | `entry_threshold` | float | 50.0 | ML 分数入场阈值 |
| | `stop_loss_pct` | float | 0.05 | 止损比例 |
| | `take_profit_pct` | float | 0.20 | 止盈比例 |
| | `hold_days` | int | 10 | 持仓天数 |
| | `max_positions` | int | 5 | 最大持仓数 |
| **风控** | `max_position_pct` | float | 0.10 | 单只股票最大仓位 |
| | `max_sector_pct` | float | 0.30 | 单行业最大暴露 |
| | `max_drawdown_limit` | float | 0.15 | 最大回撤限制 |
| | `target_volatility` | float | 0.15 | 目标波动率 |
| **ML** | `use_alpha` | bool | true | 启用 Alpha Zoo 因子 |
| | `use_reversal` | bool | false | 启用反转因子 |
| **缓存** | `cache_dir` | str | `.aimoon_cache` | 缓存目录 |
| | `cache_ttl_hours` | int | 24 | 缓存过期时间（小时） |
| **输出** | `output_dir` | str | `output` | 输出目录 |
| | `no_csv` | bool | false | 禁用 CSV 导出 |
| **排除** | `exclude_boards` | tuple | `("ST","退","北交所")` | 排除板块 |
| | `exclude_prefixes` | tuple | `("8","4")` | 排除代码前缀 |

---

## CLI 命令参考

### 筛选命令

| 命令 | 说明 |
|------|------|
| `aimoon` | 运行完整筛选（ML 排名） |
| `aimoon --demo` | Demo 模式（跳过 ML 训练） |
| `aimoon --top N` | 显示前 N 只股票 |
| `aimoon --refresh` | 强制刷新缓存 |
| `aimoon --no-alpha` | 禁用 Alpha Zoo 因子 |
| `aimoon --config FILE` | 使用自定义配置文件 |

### 模型训练

| 命令 | 说明 |
|------|------|
| `aimoon train-model` | 增量训练（warm_start） |
| `aimoon train-model --force` | 强制全量重训练 |

### 回测命令

| 命令 | 说明 |
|------|------|
| `aimoon backtest` | 默认回测 |
| `aimoon backtest --walk-forward` | Walk-Forward 验证 |
| `aimoon backtest --stocks CODES` | 指定股票（逗号分隔） |
| `aimoon backtest --hold-days N` | 持仓天数 |
| `aimoon backtest --max-positions N` | 最大持仓数 |
| `aimoon backtest --stop-loss PCT` | 止损比例 |
| `aimoon backtest --take-profit PCT` | 止盈比例 |
| `aimoon backtest --benchmark CODE` | 基准指数代码 |

### 参数优化

| 命令 | 说明 |
|------|------|
| `aimoon optimize --params PARAMS` | 优化指定参数 |
| `aimoon optimize --metric sharpe` | 优化目标（sharpe/sortino/return） |
| `aimoon optimize --trials N` | 网格搜索次数 |

### 自选股管理

| 命令 | 说明 |
|------|------|
| `aimoon watchlist add CODES` | 添加自选股（逗号分隔） |
| `aimoon watchlist remove CODES` | 删除自选股 |
| `aimoon watchlist list` | 查看自选列表 |
| `aimoon watchlist clear` | 清空自选列表 |

### 缓存管理

| 命令 | 说明 |
|------|------|
| `aimoon cache clear` | 清除所有缓存 |
| `aimoon update` | 清除缓存并重新获取数据 |
| `aimoon refresh-pool` | 强制刷新机构持仓池 |

### 其他命令

| 命令 | 说明 |
|------|------|
| `aimoon schedule --time HH:MM` | 定时筛选（每日） |
| `aimoon evaluate --stocks CODES` | 因子评估 |

---

## 常见问题

### Q1: 安装 TA-Lib 失败怎么办？

TA-Lib 是 C 语言库的 Python 封装，需要先安装系统级依赖。

**Windows**：推荐使用 conda 安装：

```bash
conda install -c conda-forge ta-lib
uv pip install ta-lib
```

**Linux**：需要先安装编译工具：

```bash
sudo apt-get install build-essential
uv pip install ta-lib
```

如果仍然失败，可以跳过 TA-Lib，系统会自动降级到 pandas-ta 实现，功能不受影响。

---

### Q2: 数据获取超时或网络错误？

系统使用三级数据源兜底：mootdx → Tencent → AKShare。如果所有源都失败：

1. 检查网络连接
2. 尝试使用 VPN 或代理
3. 运行 `aimoon --refresh` 强制刷新缓存
4. 运行 `aimoon cache clear && aimoon update` 清除并重新获取数据

---

### Q3: 如何自定义选股池？

有两种方式：

**方式一：添加自选股（推荐）**

```bash
aimoon watchlist add 600519,000858,002304  # 添加贵州茅台、五粮液、洋河股份
aimoon watchlist list                       # 查看自选列表
aimoon                                     # 运行筛选（自选股自动加入）
```

自选股不受机构持仓过滤条件限制，但仍然会经过基本筛选（市值、价格等）。

**方式二：修改配置文件**

调整 `min_market_cap_yi`、`max_pe_ttm` 等参数扩大或缩小筛选范围。

---

### Q4: 缓存不更新怎么办？

缓存有过期时间（默认 24 小时）。如果需要强制更新：

```bash
aimoon --refresh        # 强制刷新本次运行的缓存
aimoon cache clear      # 清除所有缓存
aimoon update           # 清除缓存并重新获取所有数据
```

缓存目录位于 `.aimoon_cache/`，也可以手动删除该目录。

---

### Q5: ML 模型训练太慢？

首次训练模型可能需要几分钟。后续运行会使用增量学习（warm_start），速度会快很多。

如果仍然太慢，可以：

1. 使用 Demo 模式跳过训练：`aimoon --demo`
2. 减少并行线程数：`aimoon --workers 4`
3. 减少历史数据天数：在配置文件中设置 `history_days: 120`

---

### Q6: 如何添加自选股？

```bash
# 添加单只股票
aimoon watchlist add 600519

# 添加多只股票（逗号分隔）
aimoon watchlist add 600519,000858,002304

# 查看自选列表
aimoon watchlist list

# 删除自选股
aimoon watchlist remove 600519

# 清空所有自选股
aimoon watchlist clear
```

自选股存储在 `.aimoon_watchlist.json` 文件中。

---

### Q7: 输出文件在哪里？

每次筛选/回测会自动导出到 `output/` 目录：

| 文件类型 | 说明 |
|----------|------|
| `aimoon_YYYYMMDD_HHMMSS.csv` | CSV 报告（含止损/止盈价） |
| `aimoon_YYYYMMDD_HHMMSS.md` | Markdown 报告（含交易策略） |
| `backtest_report_YYYYMMDD_HHMMSS.md` | 回测报告 |
| `equity_curve.png` | 资金曲线图 |
| `drawdown.png` | 回撤图 |
| `monthly_returns.png` | 月度收益图 |

使用 `--no-csv` 参数可禁用 CSV 导出。

---

### Q8: 如何只运行筛选不训练模型？

```bash
aimoon --demo  # 使用 Demo 模式
```

Demo 模式使用简化评分，跳过 ML 模型训练，适合快速验证系统功能。

---

## 交易策略

系统包含完整的交易策略引擎，输出报告中包含具体交易计划。

### 选股逻辑

- **模型**: XGBoost + LightGBM 集成学习（Alpha Zoo 452 因子 + Alpha360 时序特征）
- **排名**: ML 模型预测未来收益 → 百分位排名 (0-100 分)
- **入场**: ML 分数 >= 55 才买入

### 入场择时

- 优先买入近 5 日跌超 5% 的高分股（A 股散户市反转效应强）
- 近 5 日涨超 5% 的高分股暂缓买入（避免追高）
- 止损黑名单：被止损过的股票永久排除

### 仓位管理

- **Kelly 准则**: 按历史胜率/盈亏比动态计算最优仓位（半 Kelly）
- **波动率目标**: 市场波动大时自动减仓，波动小时加仓
- **个股上限**: 单只股票仓位不超过 15%
- **持仓上限**: 最多同时持 5 只股票

### 退出规则

| 规则 | 说明 |
|------|------|
| **阶梯移动止损** | 盈利 >=6% 时止损上移到 +3%（锁利），盈利 >=3% 时止损上移到成本价（保本） |
| 基础止损 | 4% |
| 止盈 | 15% |
| 持仓上限 | 10 天（到期自动卖出） |
| 动量退出 | 评分连续 2 次低于阈值时退出 |

---

## 自学习系统

每次运行自动触发（后台线程，不阻塞主流程）：

| 技术 | 作用 | 缓存 TTL |
|------|------|----------|
| **集成权重自适应** | 按滚动 IC 动态调 XGB/LGBM 权重 | 24 小时 |
| **ICIR 动态加权** | 高预测力因子增强，低预测力因子削弱 | 7 天 |
| **因子衰减检测** | CUSUM 检测因子预测力下降，自动降权 | 7 天 |
| **自动退化检测** | 过拟合比 >5.0 时丢弃 warm-start 重训 | 每次 train-model |
| **增量学习** | warm_start 避免全量重训练 | 每次 train-model |

---

## Alpha Zoo 因子库

| 来源 | 数量 | 说明 |
|------|------|------|
| gtja191 | 191 | 国泰君安短周期交易型 alpha 因子 |
| alpha101 | 101 | Kakushadze "101 Formulaic Alphas" |
| qlib158 | 154 | Microsoft Qlib Alpha158 特征 |
| academic | 6 | Fama-French 5 因子 + Carhart 动量 |
| **proprietary** | **25** | **私有因子库（5 组，每组 5 个子因子）** |

处理流程：原始值 → 行业/市值 OLS 中性化 → 百分位 → robust z-score → 因子缓存 → ICIR 加权

### 私有因子库

| 因子 | 描述 |
|------|------|
| proprietary_microstructure | 市场微观结构因子 |
| proprietary_alternative | 另类数据因子 |
| proprietary_advanced_tech | 高级技术因子 |
| proprietary_northbound | 北向资金因子 |
| proprietary_sector_rotation | 板块轮动因子 |

---

## 数据管线

历史 K 线数据使用三级兜底策略：

```
mootdx（TCP 直连，速度最快）→ Tencent（HTTP）→ AKShare（HTTP，带重试）
```

- 个股和指数统一走同一套接口
- 自动处理整数索引、缺失日期列等数据格式问题
- 原子写入缓存，崩溃不会损坏数据文件
- 实时行情 TTL 300 秒（5 分钟），确保交易时间内数据新鲜

---

## 缓存管理

```bash
aimoon cache clear    # 清除所有缓存
aimoon update         # 清除缓存并重新获取数据
aimoon refresh-pool   # 强制刷新机构持仓池
```

缓存目录：`.aimoon_cache/`

| 子目录 | 内容 | TTL | 格式 |
|--------|------|-----|------|
| `ml/` | XGBoost + LightGBM 模型 | 7 天 | JSON |
| `ml/adaptive_weights.json` | 集成自适应权重 | 24 小时 | JSON |
| `icir/` | ICIR 因子权重 | 7 天 | JSON |
| `factor_decay/` | 因子衰减检测结果 | 7 天 | JSON |
| `*.json` | K 线数据缓存 | 24 小时 | JSON |

---

## 输出文件

每次筛选/回测自动导出到 `output/` 目录：

- `aimoon_YYYYMMDD_HHMMSS.csv` — CSV（含 action/stop_loss/take_profit 列）
- `aimoon_YYYYMMDD_HHMMSS.md` — Markdown（含完整交易策略和交易计划）
- `backtest_report_YYYYMMDD_HHMMSS.md` — 回测报告
- `equity_curve.png` / `drawdown.png` / `monthly_returns.png` — 图表

---

## 项目结构

```
src/aimoon/
├── cli.py               # CLI 入口
├── config.py            # 配置管理（frozen dataclass）
├── models.py            # Signal / ScoredStock 数据模型
├── screener.py          # 纯 ML 排名筛选器
├── enhanced_backtest/   # 回测引擎
│   ├── engine.py        # 核心回测逻辑
│   ├── entry_rules.py   # 入场规则
│   ├── exit_rules.py    # 退出规则
│   ├── risk_controls.py # 风控模块
│   └── portfolio_runner.py  # 组合回测
├── ml/                  # ML 集成学习
│   ├── trainer.py       # XGBoost 训练
│   ├── lgbm_trainer.py  # LightGBM 训练
│   ├── ensemble.py      # 集成预测器
│   ├── alpha360.py      # Alpha360 时序特征
│   ├── feature_pipeline.py  # 特征提取
│   ├── icir_weighter.py # ICIR 动态加权
│   └── factor_decay.py  # 因子衰减检测
├── data/                # 数据层
│   ├── filters.py       # 持仓池过滤
│   ├── spot.py          # 实时行情
│   ├── history.py       # 历史 K 线
│   └── sector.py        # 行业数据
├── factors/             # Alpha Zoo 因子系统
│   ├── base.py          # 16 个基础算子
│   ├── registry.py      # AST 扫描 + 惰性计算
│   ├── panel.py         # 宽表面板转换
│   ├── scorer.py        # 因子评分
│   └── zoo/             # 452 个因子文件
└── scoring/             # 评分系统
    ├── __init__.py      # 分类上限评分
    └── adaptive_weight.py  # Regime 自适应权重
```

---

## 如何贡献

### 开发环境搭建

```bash
git clone https://github.com/iloat20/aimoon.git
cd aimoon
uv venv --python 3.13 .venv
uv pip install -e .
```

### 代码质量检查

```bash
ruff check src/aimoon          # Linting
black --check src/aimoon       # 格式化检查
mypy src/aimoon --ignore-missing-imports  # 类型检查
bandit -r src/aimoon -ll -ii   # 安全扫描
```

### 自动修复

```bash
ruff check src/aimoon --fix    # 修复 linting 问题
black src/aimoon --target-version py312  # 格式化代码
```

### 提交前检查清单

- [ ] 运行 `ruff check src/aimoon --fix` 修复 linting 问题
- [ ] 运行 `black src/aimoon` 格式化代码
- [ ] 运行 `bandit -r src/aimoon` 检查安全漏洞
- [ ] 确保 `aimoon --demo` 能正常运行

### 代码风格

- 使用类型注解（type hints）
- 函数不超过 50 行
- 文件不超过 800 行
- 避免魔法数字（使用常量或配置）
- 不得使用 pickle 进行反序列化
- 不得使用 bare except
- 不得硬编码密码或 API 密钥

---

## 许可证

[MIT License](LICENSE)

---

## 致谢

### Vibe-Trading

- **项目**: [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)
- **作者**: HKU Data Science Lab (HKUDS), 香港大学
- **整合内容**: Alpha Zoo 因子库、mootdx 数据源、统计验证工具、增强回测指标

### 参考文献

- Kakushadze, "101 Formulaic Alphas" (arXiv:1601.00991)
- Lopez de Prado, "Advances in Financial Machine Learning" (Wiley)
- Grinold & Kahn, "Active Portfolio Management"
- Microsoft QLib — [github.com/microsoft/qlib](https://github.com/microsoft/qlib)

---

**最后更新**: 2026-06-15
**版本**: 0.2.2
**维护者**: iloat
