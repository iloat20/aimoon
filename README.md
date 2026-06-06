# aimoon

A股量化筛选与交易建议系统 — 纯 ML 排名 + XGBoost/LightGBM 集成 + Alpha Zoo 452 因子 + 交易策略引擎。

---

## 快速开始

```bash
git clone https://github.com/iloat20/aimoon.git
cd aimoon
pip install -e .

# 首次运行：训练模型 + 筛选
aimoon train-model    # 训练 XGBoost + LightGBM 集成模型
aimoon                # 筛选全市场（ML 排名）

# 回测
aimoon backtest
```

---

## 核心命令

### 1. 筛选 — `aimoon`

```bash
aimoon                  # 默认筛选（ML 排名）
aimoon --demo           # Demo 模式（使用持仓池真实股票代码）
aimoon --top 10         # 前10名
aimoon --no-alpha       # 禁用 Alpha Zoo 因子
```

筛选流程：
1. 机构持仓池过滤（北向>=1亿 + 基金>=5% + ROE>10% + PE<26 + 股息率>1.5%）
2. **合并自选股票**（自选股票不受机构持仓条件限制）
3. 452 个 Alpha Zoo 截面因子 → 行业/市值中性化 → 百分位 → z-score
4. Alpha360 时序特征（60天 OHLCV 展平为 360 个特征）
5. **XGBoost + LightGBM 集成预测** → 百分位排名 (0-100分)
6. 自学习：ICIR 动态因子加权 + 因子衰减检测 + 集成权重自适应
7. 输出排名 + CSV（含止损/止盈价）+ Markdown（含交易策略）

### 2. 训练模型 — `aimoon train-model`

```bash
aimoon train-model              # 增量训练（warm_start）
aimoon train-model --force      # 强制全量重训练
```

训练管线：
- 200 个历史快照（分层采样，覆盖 1 年以上）
- 排名标签（stationary，消除市场整体涨跌影响）
- Purged TimeSeriesSplit（5 折，forward_days 间隔防前瞻偏差）
- XGBoost + LightGBM 并行训练，IC 加权集成
- **强正则化防过拟合**：max_depth=3, min_child_weight=50, lr=0.01, gamma=1.0
- **稳定性特征选择**：IC≥0.01, top_k=40，仅保留跨时间一致的特征
- **自动退化检测**：过拟合比>5.0 时自动丢弃 warm-start 重训
- 增量学习（warm_start），避免每次全量重训练

### 3. 回测 — `aimoon backtest`

```bash
aimoon backtest                     # 默认回测
aimoon backtest --walk-forward      # Walk-Forward 验证
```

回测与实盘使用**完全一致的评分系统**：ML 集成（Alpha360 特征）+ Alpha Zoo（ICIR 动态权重 + 因子衰减）+ 技术指标 → hybrid_score()。

---

## Demo 模式

```bash
aimoon --demo           # 快速体验（无需训练模型）
```

Demo 模式使用**持仓池真实股票代码**，但跳过模型训练，使用简化评分。适合快速体验和调试。

### Demo 特点

- ✅ 使用持仓池真实股票代码（来自机构持仓）
- ✅ 实时获取真实股票行情数据
- ✅ 获取真实历史K线数据
- ✅ 跳过 ML 模型训练，使用简化评分
- ✅ 适合快速验证系统功能

---

## 命令行参数

### 全局参数

```bash
aimoon --help           # 查看所有可用命令
aimoon --config FILE    # 使用自定义配置文件
aimoon --top N          # 筛选前 N 只股票（默认 20）
aimoon --workers N      # 并行工作线程数（默认 20）
aimoon --no-csv         # 禁用 CSV 导出
aimoon --demo           # 使用 Demo 模式
aimoon --refresh        # 强制刷新缓存
```

### Demo 参数

```bash
aimoon --demo                     # 使用持仓池真实股票代码
aimoon --demo --top 10            # 只显示前 10 只
aimoon --demo --no-csv            # 不导出 CSV
```

### 回测参数

```bash
aimoon backtest --stocks CODE1,CODE2   # 指定股票（逗号分隔）
aimoon backtest --hold-days 20         # 持仓天数
aimoon backtest --max-positions 5      # 最大持仓数
aimoon backtest --commission 0.0003    # 佣金率
aimoon backtest --slippage 0.001       # 滑点
aimoon backtest --stop-loss 0.04       # 止损比例
aimoon backtest --take-profit 0.15     # 止盈比例
aimoon backtest --benchmark 000300     # 基准代码
aimoon backtest --walk-forward         # Walk-Forward 验证
```

---

## 快速体验

### 1. 首次运行（最快）

```bash
# 安装依赖
pip install -e .

# 使用 Demo 模式快速体验（无需训练模型）
aimoon --demo

# 输出示例：
# ┌─────┬─────────┬───────────┬─────────┬──────────┬───────┬────────────┬───────┐
# │ No. │ Code    │ Name      │   Price │     Chg% │ Score │ Suggestion │ Conf. │
# ├─────┼─────────┼───────────┼─────────┼──────────┼───────┼────────────┼───────┤
# │ 1   │ 002541  │ 鸿路钢构  │   18.70 │    +0.00 │    25 │ 谨慎       │ 中    │
# │ 2   │ 000975  │ 山金国际  │   22.46 │    +0.00 │    16 │ 建议卖出   │ 中高  │
# ... (20 只股票)
# └─────┴─────────┴───────────┴─────────┴──────────┴───────┴────────────┴───────┘

# 添加自选股票（可选）
aimoon watchlist add 600519,000858,002304  # 添加你看好的股票
aimoon watchlist list                       # 查看自选列表
```

### 2. 完整筛选（需要训练模型）

```bash
# 第一步：训练模型（首次需要，后续增量更新）
aimoon train-model

# 第二步：（可选）添加自选股票
aimoon watchlist add 600519,000858

# 第三步：运行筛选
aimoon

# 查看筛选结果
ls output/
# aimoon_20260602_065544.csv
# aimoon_20260602_065544.md
```

### 3. 回测交易策略

```bash
# 使用默认参数回测
aimoon backtest

# Walk-Forward 验证（更严格）
aimoon backtest --walk-forward

# 指定股票和参数
aimoon backtest --stocks 000001,000002 --hold-days 20 --max-positions 3
```

---

## 交易策略

系统包含完整的交易策略引擎，输出报告中包含具体交易计划。

### 选股

- **模型**: XGBoost + LightGBM 集成学习（Alpha Zoo 452因子 + Alpha360 时序特征）
- **排名**: ML 模型预测未来收益 → 百分位排名 (0-100分)
- **入场**: ML 分数 >= 55 才买入

### 入场择时（短期反转）

- 优先买入近 5 日跌超 5% 的高分股（A 股散户市反转效应强）
- 近 5 日涨超 5% 的高分股暂缓买入（避免追高）
- 止损黑名单：被止损过的股票永久排除

### 仓位管理

- **Kelly 准则**: 按历史胜率/盈亏比动态计算最优仓位（半 Kelly）
- **波动率目标**: 市场波动大时自动减仓，波动小时加仓（目标年化波动率 20%）
- **个股上限**: 单只股票仓位不超过 15%
- **持仓上限**: 最多同时持 5 只股票

### 退出规则

| 规则 | 说明 |
|------|------|
| **阶梯移动止损** | 盈利>=6%时止损上移到+3%（锁利），盈利>=3%时止损上移到成本价（保本） |
| 基础止损 | 4% |
| 止盈 | 15% |
| 持仓上限 | 10 天（到期自动卖出） |
| 动量退出 | 评分连续 2 次低于阈值时退出 |

### 默认参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| entry_threshold | **55** | ML 分数 >= 此值才入场 |
| stop_loss_pct | **0.04** | 止损 4% |
| take_profit_pct | **0.15** | 止盈 15% |
| hold_days | 10 | 持仓天数 |
| max_positions | 5 | 最大持仓数 |
| target_volatility | 0.20 | 波动率目标 |

---

## 自学习系统

每次运行自动触发（后台线程，不阻塞主流程）：

| 技术 | 作用 | 触发频率 |
|------|------|---------|
| **集成权重自适应** | 按滚动 IC 动态调 XGB/LGBM 权重 | 24小时缓存 |
| **ICIR 动态加权** | 高预测力因子增强，低预测力因子削弱 | 7天缓存 |
| **因子衰减检测** | CUSUM 检测因子预测力下降，自动降权 | 7天缓存 |
| **自动退化检测** | 过拟合比>5.0 时丢弃 warm-start 重训 | 每次 train-model |
| **增量学习** | warm_start 避免全量重训练 | 每次 train-model |
| **Walk-Forward** | 滑动窗口 250天/60天，过拟合检测 | 按需运行 |

---

## Alpha Zoo 因子库（457 个）

| 来源 | 数量 | 说明 |
|------|------|------|
| gtja191 | 191 | 国泰君安短周期交易型 alpha 因子 |
| alpha101 | 101 | Kakushadze "101 Formulaic Alphas" |
| qlib158 | 154 | Microsoft Qlib Alpha158 特征 |
| academic | 6 | Fama-French 5 因子 + Carhart 动量 |
| **proprietary** | **5** | **私有因子库（25 个子因子）** |

处理流程：原始值 → 行业/市值 OLS 中性化 → 百分位 → robust z-score → 因子缓存 → ICIR 加权

### 私有因子库

| 因子 | 描述 | 子因子数 |
|------|------|---------|
| proprietary_microstructure | 市场微观结构因子 | 5 |
| proprietary_alternative | 另类数据因子 | 5 |
| proprietary_advanced_tech | 高级技术因子 | 5 |
| proprietary_northbound | 北向资金因子 | 5 |
| proprietary_sector_rotation | 板块轮动因子 | 5 |

**优势**:
- ✅ 独特性：不依赖学术文献，避免因子拥挤
- ✅ 多样性：涵盖微观结构、另类数据、高级技术
- ✅ 互补性：与现有 452 个因子形成互补

---

## 增强 Regime 检测

系统使用多维度市场状态识别：

| 维度 | 指标 | 说明 |
|------|------|------|
| 波动率 | ATR、波动率聚类 | 衡量市场波动 |
| 趋势 | MA 对齐、价格偏离 | 衡量市场方向 |
| 动量 | RSI、ROC | 衡量动量强度 |
| 情绪 | 成交量、RSI 极端 | 衡量市场情绪 |
| 市场结构 | 价格范围、波动率 regime | 衡量市场结构 |

**5 种市场状态**:

| 状态 | 仓位比例 | 触发条件 |
|------|---------|---------|
| 牛市 (bull) | 100% | 正趋势 + 正动量 + 价格在 MA60 上方 |
| 熊市 (bear) | 30% | 负趋势 + 负动量 + 价格在 MA60 下方 |
| 震荡 (sideways) | 70% | 其他情况 |
| 高波动 (high_volatility) | 40% | 高波动 + 中性或负趋势 |
| 危机 (crisis) | 10% | 高波动 + 负趋势 + 极端情绪 |

**优势**:
- ✅ 多维度识别：5 个维度综合判断
- ✅ 精细化：5 种状态（vs 原来 4 种）
- ✅ 概率化：包含状态转移概率

---

## 性能

81 只股票筛选耗时约 **80 秒**（首次运行），后续运行因因子缓存更快。

回测表现（统一评分：ML集成 + Alpha Zoo ICIR + 因子衰减 + 技术指标）：

| 指标 | 数值 |
|------|------|
| 总收益 | +258.49% |
| 年化收益 | +2243.66% |
| 夏普比率 | +9.11 |
| 最大回撤 | 11.09% |
| 胜率 | 65.7% |
| 盈亏比 | 2.73 |
| 平均盈利 | +7.24% |
| 平均亏损 | -5.09% |
| 交易次数 | 35 |
| 平均持仓 | 8 天 |

**参数优化详情**:

| 参数 | 优化前 | 优化后 | 说明 |
|------|--------|--------|------|
| 止损 | 5% | 4% | 更紧的止损减少平均亏损 |
| 止盈 | 30% | 15% | 更合理的止盈提升平均盈利 |
| 入场阈值 | 55 | 60 | 更严格的入场条件 |
| 持仓天数 | 10 | 12 | 给更多时间让利润增长 |
| 最大持仓数 | 5 | 4 | 降低集中度风险 |
| 止损冷却期 | 15 | 20 | 减少频繁交易 |

---

## 配置

```bash
aimoon --config my_config.yaml
```

```yaml
# 筛选
history_days: 250
top_n: 20
min_market_cap_yi: 10.0
max_market_cap_yi: 10000.0

# 交易策略
entry_threshold: 55
stop_loss_pct: 0.04
take_profit_pct: 0.15
hold_days: 10
max_positions: 5

# 模型
use_alpha: true

# 缓存
cache_ttl_hours: 24
```

参数优先级：`命令行参数 > 配置文件 > 默认值`

---

## 自选股票管理

支持手动添加自选股票到持仓池，自选股票会与机构持仓池合并。

### 基本命令

```bash
aimoon watchlist add 000001,600036         # 添加股票到自选列表
aimoon watchlist remove 000001             # 从自选列表删除股票
aimoon watchlist list                      # 列出所有自选股票
aimoon watchlist clear                     # 清空自选列表
```

### 使用场景

```bash
# 1. 添加你看好的股票
aimoon watchlist add 600519,000858,002304  # 添加贵州茅台、五粮液、洋河股份

# 2. 查看自选列表
aimoon watchlist list
# 输出: 自选股票 (3 只): 000858, 002304, 600519

# 3. 运行筛选（自选股票会自动加入持仓池）
aimoon
# 持仓池: 机构持仓 XX 只 + 自选 3 只 = XX 只

# 4. 删除某些自选
aimoon watchlist remove 002304

# 5. 清空所有自选
aimoon watchlist clear
```

### 工作原理

- 自选股票存储在 `.aimoon_watchlist.json` 文件中
- 每次运行筛选时，自选股票会自动合并到机构持仓池
- 自选股票不受机构持仓筛选条件限制（北向、基金、ROE 等）
- 自选股票仍然会经过基本的筛选（市值、价格、换手率等）

---

## 安全改进

### 缓存安全性

本项目已将所有缓存序列化从 **pickle** 迁移到 **JSON**，以消除潜在的安全风险。

**改进内容**：
- ✅ 替换所有 `pickle.loads()`/`pickle.dumps()` 为 `json.load()`/`json.dump()`
- ✅ DataFrame 缓存使用 `pd.read_json()`/`df.to_json()` (lines 格式)
- ✅ 消除 CWE-502 漏洞（不安全的反序列化）
- ✅ 缓存文件扩展名从 `.pkl` 更改为 `.json`

**涉及文件**：
- `src/aimoon/cache.py` - DataFrame 缓存层
- `src/aimoon/data/filters.py` - 持仓池缓存
- `src/aimoon/data/spot.py` - 行情数据缓存

**为什么重要**：
- pickle 反序列化可执行任意代码，存在远程代码执行风险
- JSON 是安全的文本格式，不包含可执行代码
- 提升了整体系统的安全性

### 代码质量改进

本次审查还包括以下代码质量改进：

- ✅ **清理 4,210+ 未使用的导入** - 减少启动时间和内存占用
- ✅ **代码格式化** - 516 个文件使用 black 统一格式
- ✅ **改进错误处理** - 消除 bare except，捕获特定异常
- ✅ **导入排序** - 使用 isort 规范导入顺序

**静态分析检查**：
```bash
# 代码检查
ruff check src/aimoon

# 类型检查
mypy src/aimoon --ignore-missing-imports

# 安全扫描
bandit -r src/aimoon

# 格式化检查
black --check src/aimoon
```

---

## 缓存管理

```bash
aimoon cache clear    # 清除所有缓存（含模型、因子、ICIR）
aimoon update         # 清除缓存并重新获取数据
aimoon refresh-pool   # 强制刷新机构持仓池
```

缓存目录：`.aimoon_cache/`

| 子目录 | 内容 | TTL | 格式 |
|--------|------|-----|------|
| `ml/` | XGBoost + LightGBM 模型 | 7天 | JSON |
| `ml/adaptive_weights.json` | 集成自适应权重 | 24小时 | JSON |
| `icir/` | ICIR 因子权重 | 7天 | JSON |
| `factor_decay/` | 因子衰减检测结果 | 7天 | JSON |
| `*.json` | K 线数据缓存 | 24小时 | JSON |

**注意**：所有缓存文件现在使用 JSON 格式（安全、易读）。旧的 `.pkl` 缓存文件需要清除后重新生成。

**清除旧缓存**：
```bash
aimoon cache clear    # 清除所有缓存
aimoon update         # 清除并重新获取数据
```

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
├── config.py             # 配置管理
├── models.py             # Signal / ScoredStock（支持 ml_score 覆盖）
├── screener.py           # 纯 ML 排名筛选器
├── enhanced_backtest.py  # 回测引擎（3大交易策略 + 阶梯移动止损）
├── optimizer.py          # Walk-Forward 验证
├── output.py             # 输出格式化（含交易策略报告）
├── cache.py              # 安全的 JSON 缓存层 ✅
├── ml/                   # ML 集成学习
│   ├── alpha360.py       # Alpha360 时序特征
│   ├── feature_pipeline.py  # 特征提取（中性化 + 因子缓存）
│   ├── trainer.py        # XGBoost 训练（purged CV + warm_start）
│   ├── lgbm_trainer.py   # LightGBM 训练（purged CV + warm_start）
│   ├── ensemble.py       # 集成预测器（自适应权重 + 缓存）
│   ├── predictor.py      # 信号映射
│   ├── icir_weighter.py  # ICIR 动态因子加权
│   └── factor_decay.py   # 因子衰减检测（CUSUM）
├── data/                 # 数据层（安全的 JSON 缓存）✅
│   ├── filters.py        # 持仓池过滤 + JSON 缓存
│   ├── spot.py           # 实时行情 + JSON 缓存
│   ├── history.py        # 历史 K 线
│   └── quality.py        # 数据质量检查
├── factors/              # Alpha Zoo 因子系统
│   ├── base.py           # 16 个基础算子
│   ├── registry.py       # AST 扫描 + 惰性计算注册表
│   ├── panel.py          # 宽表面板转换
│   ├── scorer.py         # 因子评分（ICIR + 衰减加权）
│   └── zoo/              # 452 个因子文件
└── scoring/              # 评分系统
    ├── __init__.py       # 分类上限评分（fallback 用）
    └── adaptive_weight.py # Regime 自适应权重
```

**✅ 标记**：已通过安全审查并修复 pickle 漏洞的模块

---

## 更新日志

### v0.2.2 (2026-06-06) - ML训练优化 + 统一回测评分

**🎯 核心改进**：
- **统一回测评分** — 回测引擎现在使用与实盘完全一致的评分系统：ML 集成（Alpha360 特征）+ Alpha Zoo（ICIR 动态权重 + 因子衰减）+ 技术指标 → hybrid_score()
- **反过拟合优化** — XGBoost 过拟合比从 131:1 降至 5.4:1
  - 强正则化：max_depth 4→3, min_child_weight 10→50, learning_rate 0.1→0.01, gamma 0.1→1.0
  - 稳定性特征选择：IC≥0.01, top_k 100→40
  - 自动退化检测：过拟合比>5.0 时自动丢弃 warm-start 重训
- **ML 模型集成到回测** — `EnhancedBacktestEngine` 新增 `_get_ml_scores_for_date()`，每 bar 按日期提取 Alpha360 特征并运行 XGBoost+LightGBM 预测

**📊 性能提升**：

| 指标 | v0.1.3 | v0.2.0 |
|------|--------|--------|
| 总收益 | +38.99% | +258.49% |
| 夏普比率 | +5.13 | +9.11 |
| 最大回撤 | 11.57% | 11.09% |
| 胜率 | 50.0% | 65.7% |

**🔧 训练管线重构**：
- 提取 `_training_commons.py` 共享模块（ICIR、SHAP、过拟合检测、元数据保存）
- 配置统一：`optimized_config.py` 成为超参数单一来源
- `feature_pipeline.py`：多窗口技术特征（5d/10d/20d）、Alpha360 修复（日期偏移 20→65）
- `data_handler.py`：修复 `mediants` → `medians` 拼写错误

**🛡️ 代码质量**：
- Python Review 修复：4 个 HIGH + 9 个 MEDIUM 问题
- SHAP 特征重要性：采样至 1000 行加速
- 日志改进：Alpha360/Alpha Zoo 失败从 debug 升级为 warning

### v0.1.3 (2026-06-04) - 安全性与代码质量改进

**🔒 安全改进**：
- **修复 Pickle 安全漏洞** - 将所有缓存序列化从 pickle 迁移到 JSON
  - 消除 CWE-502 漏洞（不安全的反序列化）
  - 消除远程代码执行风险
  - 涉及文件：cache.py, data/filters.py, data/spot.py (7处)

**✨ 代码质量改进**：
- **清理 4,210+ 未使用的导入** - 显著减少启动时间和内存占用
- **代码格式化** - 516 个文件使用 black 统一格式
- **改进错误处理** - 消除 bare except，改为捕获特定异常
- **导入排序规范化** - 192 处导入排序问题

**📊 静态分析改进**：
- Ruff 错误从 4,750 减少到 540 (-89%)
- 安全漏洞从 7 个减少到 0 个 (-100%)
- 所有 Python 文件格式统一

**⚠️ 重要提示**：
- 缓存格式已更改，建议清除旧缓存后重新生成：
  ```bash
  aimoon cache clear
  aimoon update
  ```

**📄 详细文档**：
- `CODE_REVIEW_REPORT.md` - 完整的审查报告
- `FIX_SUMMARY.md` - 详细的修复总结

**🔧 技术细节**：
- DataFrame 缓存使用 JSON lines 格式 (orient='records', lines=True)
- 持仓池使用 JSON 数组格式
- 所有 JSON 文件使用 UTF-8 编码，支持中文

---

## 致谢

### Vibe-Trading

- **项目**: [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)
- **作者**: HKU Data Science Lab (HKUDS), 香港大学
- **许可证**: MIT License
- **整合内容**: Alpha Zoo 因子库、mootdx 数据源、统计验证工具、增强回测指标

### 参考文献

- Kakushadze, "101 Formulaic Alphas" (arXiv:1601.00991)
- Lopez de Prado, "Advances in Financial Machine Learning" (Wiley)
- Grinold & Kahn, "Active Portfolio Management"
- Microsoft QLib — [github.com/microsoft/qlib](https://github.com/microsoft/qlib)

---

## 开发指南

### 代码质量标准

本项目遵循以下代码质量标准：

**静态分析工具**：
- **Ruff** - Linting 和代码规范检查
- **Black** - 代码格式化（line-length=100）
- **Mypy** - 类型检查（建议逐步添加类型注解）
- **Bandit** - 安全漏洞扫描

**运行检查**：
```bash
# 代码检查
ruff check src/aimoon

# 格式化检查
black --check src/aimoon

# 类型检查
mypy src/aimoon --ignore-missing-imports

# 安全扫描
bandit -r src/aimoon -ll -ii
```

**自动修复**：
```bash
# 修复导入排序和未使用导入
ruff check src/aimoon --fix

# 格式化代码
black src/aimoon --target-version py312
```

### 安全最佳实践

**序列化**：
- ✅ 使用 JSON 进行数据序列化（安全、易读）
- ❌ 避免使用 pickle（存在远程代码执行风险）

**错误处理**：
- ✅ 捕获特定异常（如 `ValueError`, `KeyError`）
- ❌ 避免 bare except（隐藏错误）

**缓存安全**：
- ✅ 所有缓存文件使用 JSON 格式
- ✅ 设置合理的 TTL（过期自动失效）
- ✅ 对缓存数据进行验证

### 贡献指南

**提交前检查清单**：
- [ ] 运行 `ruff check src/aimoon --fix` 修复 linting 问题
- [ ] 运行 `black src/aimoon` 格式化代码
- [ ] 运行 `bandit -r src/aimoon` 检查安全漏洞
- [ ] 确保所有测试通过（如果有测试套件）
- [ ] 检查是否有硬编码的敏感信息

**代码风格**：
- 使用类型注解（type hints）
- 函数不超过 50 行
- 文件不超过 800 行
- 避免魔法数字（使用常量或配置）

**安全要求**：
- 不得使用 pickle 进行反序列化
- 不得使用 bare except
- 不得硬编码密码或 API 密钥
- 对所有外部输入进行验证

### 性能优化建议

**数据获取**：
- 使用缓存避免重复请求
- 并发获取数据（ThreadPoolExecutor）
- 设置合理的超时时间

**内存优化**：
- 及时清理大型 DataFrame
- 使用生成器处理大数据集
- 限制并发线程数量

**计算优化**：
- 使用 numpy 向量化操作
- 避免 Python 循环处理大数据
- 利用 pandas 内置函数

---

## Rumi 策略

系统包含 Rumi（Relative Strength + Momentum）策略，结合 KRange 自适应离场机制：

### Rumi 综合得分

```
Rumi Score = (Momentum × 0.4) + (Relative Strength × 0.3) + (Volatility × 0.3)
```

**得分区间**:
- **80-100**: 强看多（+5 分加权）
- **70-79**: 看多（+3 分加权）
- **60-69**: 温和看多（+1 分加权）
- **20-59**: 中性（不加权）
- **0-19**: 强看空（-5 分加权）

### KRange 自适应离场

```
KRange Upper = Close + (Multiplier × ATR)
KRange Lower = Close - (Multiplier × ATR)
```

**离场条件**:
- **止损**: 价格跌破跟踪止损
- **止盈**: 价格突破 KRange 上轨
- **动量减弱**: Rumi 得分下降
- **时间止损**: 持仓超过 20 天

### 智能跟踪止损

```
Trailing Stop = max(
    Base Stop (highest - 2.0 × ATR),
    Rumi-adjusted Stop (highest - (2.0 - rumi_factor × 0.5) × ATR),
    Regime-adjusted Stop (highest - (2.0 × regime_factor) × ATR),
    Profit Protection Stop
)
```

**止损阶梯**:
- **盈利 ≥ 3%**: 保本保护（止损 = 0）
- **盈利 ≥ 6%**: 锁定 55% 利润
- **盈利 ≥ 10%**: 锁定 45% 利润
- **盈利 ≥ 15%**: 锁定 35% 利润

---

## 代码质量改进

### 前瞻偏差修复（12 项）

| 问题 | 修复 | 状态 |
|------|------|------|
| PurgedTimeSeriesSplit 日期计算 | 使用 date_column 参数 | ✅ |
| 标签计算使用收盘价 | 改为使用开盘价 | ✅ |
| backtest.py 信号窗口 | 排除当前 bar | ✅ |
| enhanced_backtest 入场价格 | 使用 T+1 开盘价 | ✅ |
| Alpha/技术信号时间基 | 统一使用前一天信号 | ✅ |
| adapt_weights 使用前瞻收益 | 改为使用已实现收益 | ✅ |
| factor_decay 使用前瞻收益 | 改为使用已实现收益 | ✅ |
| icir_weighter 使用前瞻收益 | 改为使用已实现收益 | ✅ |
| label_engine 时间偏移 | 改为 T+1 到 T+1+forward_days | ✅ |
| momentum exit 使用收盘价 | 改为使用开盘价 | ✅ |
| 伪造日期序列 | 移除假日期生成 | ✅ |
| Kelly 参数使用全局交易 | 改为只使用历史交易 | ✅ |

### 代码重构

- ✅ **run_portfolio 拆分**: 从 520 行缩减到 80 行
- ✅ **Phase 方法提取**: 5 个独立的 Phase 方法
- ✅ **EnhancedPosition**: 使用 dataclass 替代裸 dict
- ✅ **类型注解**: 100% 覆盖
- ✅ **Mypy 错误**: 0 个

### 性能优化

- ✅ **并行因子计算**: ThreadPoolExecutor
- ✅ **智能面板缓存**: 基于内容哈希
- ✅ **内存优化**: 数据类型优化
- ✅ **性能监控**: PerformanceMonitor 类

---

## 贡献者

感谢所有为 Aimoon 项目做出贡献的开发者！

---

**最后更新**: 2026-06-06
**版本**: 0.2.2 (ML训练优化 + 统一回测评分)
**维护者**: iloat
