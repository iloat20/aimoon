# 评分系统重构设计

**日期**: 2026-06-17
**状态**: 已批准（待实施）
**作者**: brainstorming 协作产出

## 1. 背景与动机

当前评分系统存在三个核心问题：

1. **训练耗时长** — 三模型集成（XGBoost + LightGBM + Elastic Net）× 6 折 Purged TSCV × 300 日期 × grid search 集成权重搜索，外加可选 Optuna（80 trials × 5 折）。单次训练数分钟。
2. **因子计算不稳定** — 452 个 Alpha Zoo 因子（gtja191/alpha101/qlib158/academic）经 ICIR 选择、行业/市值中性化、SVD 正交化，链路长、除零/NaN 风险高，结果不稳定。
3. **评分链路复杂** — `hybrid_scorer` 四组加权（ml 0.30 / alpha 0.25 / reversal 0.45 / momentum 0.00）依赖 ~20 个技术信号模块，难维护、不可控。

**目标**：评分系统只用 ML 模型给持仓池股票打分；大幅削减因子，仅保留适合 A 股的少量稳定因子；优化回测与项目运行时间。

## 2. 已批准的设计决策

| # | 决策点 | 选择 |
|---|--------|------|
| 1 | 评分架构 | 彻底替换：删掉技术信号模块，最终分数 = ML 百分位 |
| 2 | 因子保留 | 精简手写 11 个 A 股因子（去掉小市值 log_size，保留北向资金） |
| 3 | ML 模型 | 单 LightGBM（删 XGBoost/Elastic Net/stacking/grid search） |
| 4 | 时序特征 | 删除 Alpha360（360维）+ Robust，特征 = 11 因子 + 基础技术统计 |
| 5 | 回测 | 简化为 ML 分数驱动单引擎，预计算分数 |
| 6 | 删除策略 | 激进删除（彻底删文件） |
| 7 | CV | 2 折 Purged TSCV + n_dates=120 |
| 8 | 入场价 | T 日收盘价（隐含"收盘附近可成交"假设，轻微前瞻，用户明确选择） |
| 9 | 退出规则 | 保留止损/止盈/跟踪止损（参数已调优） |

## 3. 架构与数据流

### 3.1 新数据流

```
CLI (cli.py)
  → Config (config.py: 精简，删 use_alpha/use_reversal 等开关)
    → 持仓池 (data/filters.py + holdings_pool.py: 保留不动)
      → K线数据 (data/history.py: 三级兜底保留)
        → 因子层 (factors/ashare.py: 新文件，11 个手写 A 股因子)
          → ML (ml/trainer.py 精简: 单 LightGBM, 2 折 Purged TSCV, ~18 特征)
            → Screener (screener.py: score = ML 百分位, 删技术信号)
              → 回测 (backtest.py: ML 分数驱动单引擎, 预计算分数)
                → Output (output.py: CSV + Markdown)
```

### 3.2 核心变化：分数 = ML 百分位

`ScoredStock.ml_score`（0-100 百分位）即为最终分数，`total_score = ml_score`。删除 `hybrid_scorer` 四组加权与 `signals` 元组复杂拼装。`signals` 字段保留但置空或仅放 1 个摘要信号（如 `ml_rank`），不再驱动评分。

### 3.3 删除范围总览

| 删除 | 保留 |
|------|------|
| `scoring/` 下 ~20 个技术信号模块 + `hybrid_scorer` + `combiner` + `signal_map` | `scoring/portfolio.py`（持仓约束，回测仍用）、`scoring/__init__.py`（薄封装） |
| `factors/zoo/` 全部 452 因子 + `registry`/`panel`/`dag`/`genetic`/`incremental`/`quality`/`weighting`/`scorer` | `factors/base.py`（基础算子，新因子复用）、新 `factors/ashare.py` |
| `ml/` 中 alpha360、alpha360_robust、stacking、meta_ensemble、hyperopt、incremental_trainer、icir_weighter、factor_decay、factor_quality、factor_importance、covariance_estimator、feature_selector、slippage_model、walk_forward、ensemble、ensemble_signals | `ml/trainer.py`（精简）、`ml/feature_pipeline.py`（精简）、`ml/label_engine.py`、`ml/purged_tscv.py`、`ml/model_persistence.py`、`ml/optimized_config.py`（精简）、`ml/training_loop.py`（精简）、`ml/data_collection.py` |
| 专有因子族大部分、`regime_enhanced`、`rumi_*`、`adaptive_strategy`、`factor_eval`、`factor_model_optimizer/`、`grid_search`、`optimizer`、`self_learning` | `data/` 全部、`indicators/technical.py`（技术统计/止损复用）、`enhanced_backtest/metrics.py` |

> 删除前用 `Grep` 验证无其他模块引用，避免 import 断链。

## 4. 因子设计（`factors/ashare.py`）

### 4.1 11 个 A 股因子

每个因子均经 A 股文献验证、计算稳定（无复杂链式算子、除零保护）、向量化快。返回 panel 对齐的 `dict[factor_id, DataFrame(日期×股票)]`，供 `extract_features` 按目标日期切片。

| # | factor_id | 含义 | A 股逻辑 | 数据来源 | 符号方向 |
|---|-----------|------|---------|----------|----------|
| 1 | `rev_5d` | 5 日反转 | A 股短期反转效应极强 | close | 取负 |
| 2 | `rev_20d` | 20 日反转 | 中短期反转 | close | 取负 |
| 3 | `turnover_20d` | 20 日平均换手率 | 高换手→低未来收益（流动性溢价反转） | turnover | 取负 |
| 4 | `vol_20d` | 20 日实现波动率 | 低波动异常 | close | 取负 |
| 5 | `mom_60d` | 60 日动量 | A 股中期动量有效，短期反转主导故用长窗 | close | 正 |
| 6 | `amihud_20d` | Amihud 非流动性 | 非流动性溢价 | close+amount | 正 |
| 7 | `ep` | 盈利收益率 = 1/PE | 价值因子 | PE | 正 |
| 8 | `bp` | 账面市值比 = 1/PB | 价值因子 | PB | 正 |
| 9 | `div_yield` | 股息率 | 红利溢价 | dividend | 正 |
| 10 | `northbound_chg_20d` | 北向持仓 20 日变化 | A 股特有聪明资金信号 | northbound | 正 |
| 11 | `sector_mom_20d` | 板块 20 日动量 | 行业轮动效应 | close+sector | 正 |

> 注：原设计中 `log_size`（小市值）已应要求去掉——近两年 A 股小盘溢价收窄甚至反转。

### 4.2 计算原则（稳定性保证）

1. **无行业/市值中性化** — 旧系统的中性化 + SVD 正交化是不稳定主因。新设计仅做截面稳健 z-score（中位数/MAD，clip ±3），保留因子原始经济含义。
2. **除零保护** — 所有除法 `np.where(denom < eps, np.nan, ...)`，Amihud/EP/BP 分母全保护。
3. **缺失值** — 基本面缺失（PE/PB/股息/北向）填截面中位数后 z-score，不丢弃股票。
4. **复用 `factors/base.py`** — 用现有 `ts_mean`/`stddev`/`rank` 等算子，不重造。
5. **因子列表为模块顶部常量** `ASHARE_FACTORS`，方便增删调参，无需 AST 注册。

### 4.3 训练/推理一致性

`extract_features` 对每个目标日期：从 `compute_ashare_factors` 输出切片该日截面 → 稳健 z-score → 与基础技术统计拼接 → ~18 特征。训练和推理走同一函数，用训练时保存的 `feature_medians` 填推理 NaN。

## 5. ML 训练重构

### 5.1 单 LightGBM 训练

| 项 | 旧 | 新 |
|----|----|----|
| 模型 | XGB + LGBM + EN 三模型 + stacking + grid search | **单 LightGBM** |
| 训练函数 | `train_model` + `train_ensemble` + `train_incremental_dual` | 仅 `train_model`（精简） |
| CV | 6 折 Purged TSCV | **2 折** Purged TSCV（保留防前瞻，大幅减开销） |
| 特征 | 数百维（Alpha360=360 + Robust + ≤120 Alpha Zoo + ICIR + 中性化 + SVD） | **~18 维**（11 因子 z-score + 6 技术统计） |
| 超参搜索 | Optuna 80 trials × 5 folds | **删除**（固定合理超参） |
| 增量学习 | A/B 双模型 + EWC + 智能增量 | **删除**（仅 `--force` 全量重训） |
| n_dates | 300 | **120** |
| forward_days | 5 | 5 |

### 5.2 LightGBM 固定超参

```
max_depth: 4
num_leaves: 31
n_estimators: 300
learning_rate: 0.03
min_child_samples: 50
subsample: 0.7
colsample_bytree: 0.6
reg_lambda: 5.0
reg_alpha: 2.0
early_stopping_rounds: 30
objective: regression
metric: rmse
random_state: 42
```

### 5.3 特征提取（`ml/feature_pipeline.py` 精简重写）

`extract_features(panel, target_date, sector_map, fundamentals, feature_medians)`:
1. 从 `compute_ashare_factors` 切片目标日 → 11 因子截面
2. 稳健 z-score（中位数/MAD，clip ±3）
3. + 基础技术统计：5/10/20d 波动率、5/10/20d 收益率（6 维）
4. 训练时保存 `feature_medians`（推理填 NaN 用）
5. **删除**：ICIR 因子选择循环、行业/市值中性化、SVD 正交化、Alpha360、Robust、PCA/KMeans

### 5.4 预测与打分

- 删除 `EnsemblePredictor`，新建 `ml/predictor.py` 的 `MLPredictor`：加载单 LightGBM 模型 + feature_names + feature_medians。
- `predict(panel)` → 原始分数 → **截面百分位排名 → 0-100** = `ml_score`。
- `screener.screen_stock` 精简：`total_score = ml_score`，`signals` 置空或仅 1 个摘要信号。
- 无 ML 模型时：`ml_score = None`，`total_score = 0`，明确提示需训练，**不回退技术信号**。

### 5.5 训练时间预估

| 步骤 | 旧 | 新 |
|------|----|----|
| 因子计算 | 452 因子 × ICIR 选择(90日 rank IC) + 中性化 + SVD | 11 因子向量化，无选择循环 |
| 特征数 | ~400 | ~18 |
| CV | 6 折 × 3 模型 + grid search | 2 折 × 1 模型 |
| Optuna | 80 trials × 5 折 | 无 |

**预估**：从数分钟降至 **~20-40 秒**（80 股票，120 日期）。

### 5.6 模型持久化

保存：LightGBM 模型 + `feature_names.json` + `feature_medians.json` + `meta.json`（IC、训练日期、n_dates）。7 天 TTL。JSON 序列化（无 pickle，安全合规）。

## 6. 回测重构（`backtest.py` 单引擎）

### 6.1 核心优化：预计算分数

当前回测慢的主因是每天重算因子 + ML。新设计分两阶段：

1. **预计算阶段**（一次性）：对整个回测区间 panel，`compute_ashare_factors` 一次算出 11 因子完整时间序列 → 按日切片 → `extract_features` → `predict` → 截面百分位 → `scores: dict[date, dict[code, 0-100]]`。向量化，快。
2. **回测循环阶段**：纯 Python 遍历预计算分数，无任何因子/ML 计算。每日检查入场/退出，极快。

### 6.2 单引擎逻辑

```
每个交易日 t：
  1. 退出检查（对持仓）：
     - 止损：当日 low ≤ 买入价 × (1 - stop_loss_pct) → 止损价卖出
     - 止盈：当日 high ≥ 买入价 × (1 + take_profit_pct) → 止盈价卖出
     - 跟踪止损：峰值利润 ≥3% 触发保本、≥6% 锁定利润，回落触发卖出
     - 最大持有天数：持有 ≥ hold_days → 卖出
  2. 入场检查（空仓槽位可用）：
     - score[t][code] ≥ entry_threshold
     - 用 T 日收盘价买入（用户明确选择；隐含"收盘附近可成交"假设，轻微前瞻）
     - 等权或按分数加权，受 max_positions 限制
  3. 记录净值
```

### 6.3 参数（沿用已调优值）

| 参数 | 值 | 说明 |
|------|----|----|
| `entry_threshold` | 60 | ML 分数入场阈值 |
| `stop_loss_pct` | 0.04 | 止损 4% |
| `take_profit_pct` | 0.14 | 止盈 14% |
| `trailing_breakeven` | 0.03 | +3% 保本 |
| `trailing_lock` | 0.06 | +6% 锁利 |
| `hold_days` | 12 | 最大持有 12 日 |
| `max_positions` | 4 | 最多 4 仓 |
| `benchmark_code` | 000300 | 沪深 300 基准 |

### 6.4 回测时间预估

| 项 | 旧 | 新 |
|----|----|----|
| 每日计算 | 重算因子+ML | 查预计算分数表 O(1) |
| 引擎 | qf 事件引擎 + 多策略 + regime | 单循环 |
| walk-forward | 滚动重训 + regime | 无 |

**预估**：从分钟级降至 **~5-15 秒**。

### 6.5 输出指标（沿用 `enhanced_backtest/metrics.py`）

总收益、年化、夏普、最大回撤、胜率、盈亏比、交易次数、平均持有天数 + 基准对比。

## 7. 错误处理

| 场景 | 处理 |
|------|------|
| ML 模型缺失/过期（7 天 TTL） | `ml_score=None`，`total_score=0`，提示"请先 `aimoon train-model`"，不回退技术信号 |
| 单股因子计算失败 | 该股该因子 NaN → 截面中位数填充，不崩溃 |
| 数据不足（<60 根 K 线） | 跳过该股，记录 warning |
| 除零（PE=0/成交量=0） | `np.where(denom<eps, np.nan, ...)` 全保护 |
| 回测持仓池为空 | 明确报错退出 |
| 数据源三级兜底失败 | 沿用 `result.py` Result 类型，记录错误跳过 |

## 8. 测试计划（pytest，目标 80%）

- **单元**：11 个因子各自（构造输入 → 断言输出，含除零/NaN 边界）
- **单元**：稳健 z-score、百分位排名
- **单元**：特征提取训练/推理一致性（同一函数、feature_medians 填充）
- **集成**：`train_model` 小合成 panel → 产出模型 + IC > 0
- **集成**：`backtest` 单引擎合成数据 → 产出指标
- **回归**：`total_score == ml_score`（无 hybrid 回退）

## 9. 文件清单

### 9.1 新建

- `factors/ashare.py` — 11 因子 + `compute_ashare_factors()`
- `backtest.py` — ML 分数驱动单引擎 + 预计算分数
- `ml/predictor.py` — `MLPredictor`（替代 EnsemblePredictor）

### 9.2 修改（精简）

- `cli.py` — 删 `--no-alpha`/`--reversal`/`--optuna`/`--smart-incremental` 开关，`train-model` 调单模型，`backtest` 调新引擎
- `config.py` — 删 `use_alpha`/`use_reversal`，保留回测参数
- `screener.py` — `screen_stock` 精简为 ML 分数，删 `_inject_alpha_signals`/`_inject_ml_signals`/增量版
- `models.py` — `ScoredStock` 简化（`total_score=ml_score`，`signals` 可空）
- `output.py` — 删技术信号展示，改 ML 分数为主
- `ml/trainer.py` — 仅 `train_model` 单 LightGBM，删 ensemble/dual
- `ml/feature_pipeline.py` — 精简为 11 因子 + 技术统计
- `ml/training_loop.py` — 2 折 Purged TSCV，删 Optuna
- `ml/optimized_config.py` — 仅 LightGBM 参数，n_dates=120
- `ml/data_collection.py` — 精简日期选择
- `scoring/__init__.py` — 薄封装（或删，screener 直接用 ml_score）
- `factors/__init__.py` / `ml/__init__.py` — 更新导出

### 9.3 删除（激进）

- `scoring/`：hybrid_scorer、combiner、signal_map、momentum、momentum_ext、reversal、mean_reversion、turtle、rsi、macd、kdj、bollinger、trend、trend_ext、volume、sector、rps、fundamentals、adaptive_weight、dedup、_ml_signal（保留 portfolio）
- `factors/`：zoo/（全部 452）、registry、panel、dag、genetic、incremental、quality、weighting、scorer
- `ml/`：alpha360、alpha360_robust、stacking、meta_ensemble、hyperopt、incremental_trainer、icir_weighter、factor_decay、factor_quality、factor_importance、covariance_estimator、feature_selector、slippage_model、walk_forward、ensemble、ensemble_signals、_training_commons（部分）、lgbm_trainer（合并入 trainer）
- 顶层：regime_enhanced、rumi_strategy、rumi_optimizer、adaptive_strategy、grid_search、optimizer、self_learning、factor_eval、factor_model_optimizer/、qf_backtest/、enhanced_backtest/（除 metrics）、demo 简化

> 删除前 `Grep` 验证无引用，避免 import 断链。

## 10. 范围说明

本设计为单次实施计划范围内的工作。删除清单虽大，但目标单一：以 ML 百分位为唯一评分、11 个稳定 A 股因子、单 LightGBM、ML 分数驱动回测单引擎。实施时按"新建 → 修改核心 → 验证 → 激进删除 → 全量验证"顺序推进，每步可独立验证。
