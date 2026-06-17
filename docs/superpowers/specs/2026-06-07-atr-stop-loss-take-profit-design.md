# ATR 动态止损止盈设计文档

**生成日期**: 2026-06-07
**状态**: 待审核
**目标**: 将胜率从 43.8% 提升至 ≥50%，盈亏比从 0.82 提升至 ≥1.5

---

## 1. 问题分析

### 1.1 当前回测表现（2026-06-07）

| 指标 | 当前值 | 目标值 |
|------|--------|--------|
| 胜率 | 43.8% | ≥50% |
| 盈亏比 | 0.82 | ≥1.5 |
| 平均盈利 | +6.36% | - |
| 平均亏损 | -6.01% | - |
| 交易次数 | 16 | - |

### 1.2 根本原因

| 退出原因 | 次数 | 占比 | 平均收益 | 胜率 |
|---------|------|------|---------|------|
| stop_loss | 7 | 44% | -4.87% | 0% |
| take_profit | 4 | 25% | +10.69% | 100% |
| profit_protection | 2 | 12.5% | +0.08% | 100% |
| data_gap | 3 | 19% | -6.14% | 33% |

**核心问题**：
1. **固定 4% 止损太紧** — A 股日波动率约 3-5%，4% 止损容易被正常波动触发。44% 交易以止损结束
2. **固定 15% 止盈太远** — 只有 25% 交易能达到止盈目标。多数盈利交易提前退出（profit_protection/momentum_exit）
3. **平均亏损 ≈ 平均盈利** — 盈亏比接近 1:1，系统期望收益接近 0

---

## 2. 设计方案：ATR 动态止损止盈

### 2.1 核心原理

用 ATR(14)（Average True Range，14 日平均真实波幅）的倍数替代固定百分比，使止损/止盈自适应每只股票的波动率。

**ATR 含义**：
- ATR 衡量股票的平均日内波动幅度
- 低波动股（如长江电力）：ATR% ≈ 1.5%
- 中波动股（如中国平安）：ATR% ≈ 4%
- 高波动股（如兖矿能源）：ATR% ≈ 6%

### 2.2 止损：`1.5×ATR` 动态入场止损

**公式**：
```
stop_loss_pct = ATR(14) / EntryPrice × STOP_LOSS_ATR_MULTIPLIER
```

**实际效果**（`1.0×ATR`, clamp `[2%, 6%]`）：

| 股票类型 | ATR% | 止损% | 对比当前 4% |
|---------|------|------|------------|
| 低波动 | 1.5% | **2%**（clamp下限） | 更紧 ✅ |
| 中波动 | 4% | **4%** | 持平 |
| 高波动 | 6% | **6%**（clamp上限） | 更宽（减少误止损） ✅ |

**优势**：
- 低波动股自动收紧止损到 2%（合理）
- 高波动股止损不超过 6%（用户指定）
- 减少高波动股的误止损

### 2.3 止盈：`3×ATR` 动态止盈（固定 2:1 盈亏比）

**公式**：
```
take_profit_pct = ATR(14) / EntryPrice × TAKE_PROFIT_ATR_MULTIPLIER
```

**实际效果**（`4.0×ATR`, clamp `[9%, 18%]`）：

| 股票类型 | ATR% | 止盈% | reward:risk | 对比当前 15% |
|---------|------|------|-------------|------------|
| 低波动 | 1.5% | **9%**（clamp下限） | 4.5:1 | 更容易达标 ✅ |
| 中波动 | 4% | **16%** | 4:1 | 略高 |
| 高波动 | 6% | **18%**（clamp上限） | 3:1 | 稍高，但让利润奔跑 ✅ |

**优势**：
- 低波动股止盈不低于 9%（用户指定），reward:risk 高达 4.5:1
- 中波动股止盈 16%，reward:risk=4:1
- 高波动股止盈 18%，reward:risk=3:1
- 所有场景 R:R ≥ 3:1，远超 1.5 目标

### 2.4 增强 Chandelier 移动止盈

**当前实现**：仅在 `pnl > 0` 时启用，从最高价回撤 `2.5×ATR`

**增强方案**：
- 全程启用 Chandelier 跟踪（不仅盈利时）
- 从入场开始就跟踪最高价
- 当价格从最高价回撤 `2.5×ATR` 时退出
- 与固定止盈取较低者（谁先触发用谁）

```
chandelier_stop_pct = (highest_price - 2.5 × ATR) / entry_price - 1
effective_stop = max(fixed_stop, chandelier_stop)  # 取较紧的
```

### 2.5 保留现有保护机制

所有现有利润保护机制保留不变：
- **阶梯移动止损**：盈利≥3%保本、≥6%锁55%、≥10%锁45%、≥15%锁35%
- **硬止损上限**：单笔最大亏损 8%（gap-down 保护）
- **利润保护**：峰值≥3%且回撤至≤1%时退出
- **时间衰减**：15天"死钱"退出，10天亏损收紧

---

## 3. 预期效果

### 3.1 定量预测

| 指标 | 当前值 | ATR 方案预期 |
|------|--------|-------------|
| stop_loss 占比 | 44% | ~30% |
| take_profit 占比 | 25% | ~35% |
| 平均亏损 | -6.01% | ~-4.5% |
| 平均盈利 | +6.36% | ~+8% |
| **胜率** | **43.8%** | **~50%** |
| **盈亏比** | **0.82** | **~1.8** |

### 3.2 期望收益计算

```
期望收益 = 胜率 × 平均盈利 - (1-胜率) × 平均亏损
         = 0.50 × 8% - 0.50 × 4.5%
         = +1.75%（每笔交易）
```

比当前的 `0.438 × 6.36% - 0.562 × 6.01% = -0.59%` 显著改善。

---

## 4. 改动范围

### 4.1 文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `src/aimoon/backtest/risk_controls.py` | 新增常量 | 添加 ATR 乘数 |
| `src/aimoon/enhanced_backtest.py` | 修改引擎 | 核心逻辑：ATR 止损/止盈计算 |
| `src/aimoon/config.py` | 新增参数 | 添加 ATR 乘数配置 |
| `src/aimoon/cli.py` | 新增参数 | 添加 CLI `--sl-atr`, `--tp-atr` |

### 4.2 无需改动的文件

- `src/aimoon/scoring/` — 评分系统不变
- `src/aimoon/ml/` — ML 模型不变
- `src/aimoon/screener.py` — 筛选逻辑不变
- `src/aimoon/models.py` — 数据模型不变
- `src/aimoon/backtest/position.py` — 仓位计算不变
- `src/aimoon/indicators/` — 技术指标不变

### 4.3 具体改动

#### risk_controls.py

```python
# ── ATR 动态止损止盈参数 ──
STOP_LOSS_ATR_MULTIPLIER: float = 1.0    # 止损 ATR 倍数（1.0×ATR）
TAKE_PROFIT_ATR_MULTIPLIER: float = 4.0  # 止盈 ATR 倍数（4.0×ATR）
_MIN_STOP_LOSS_PCT: float = 0.02         # 最小止损（2%）
_MAX_STOP_LOSS_PCT: float = 0.06         # 最大止损（6%，用户指定：高波动股止损6%）
_MIN_TAKE_PROFIT_PCT: float = 0.09       # 最小止盈（9%，用户指定：低波动止盈9%）
_MAX_TAKE_PROFIT_PCT: float = 0.18       # 最大止盈（18%）
```

#### enhanced_backtest.py

```python
# `__init__` 新增参数：
#   stop_loss_atr_multiplier: float = STOP_LOSS_ATR_MULTIPLIER (1.0)
#   take_profit_atr_multiplier: float = TAKE_PROFIT_ATR_MULTIPLIER (4.0)

# `_phase0_execute_pending` 修改：
#   使用 ATR 替代固定百分比计算 stop_loss
#   dynamic_sl = _compute_atr_stop_loss(entry_window, self.stop_loss_atr_multiplier, self.stop_loss_pct)

# `_phase1_stop_loss_take_profit` 修改：
#   使用 ATR 止盈替代固定百分比
#   dynamic_tp = _compute_atr_take_profit(...)

# 新增辅助函数：
# def _compute_atr_stop_loss(kline, atr_multiplier, fallback_pct) -> float
# def _compute_atr_take_profit(kline, atr_multiplier, stop_loss_pct) -> float
```

#### config.py / cli.py

```python
# 新增参数
#   "--sl-atr": type=float, default=1.5, help="ATR 止损倍数"
#   "--tp-atr": type=float, default=3.0, help="ATR 止盈倍数"
```

---

## 5. 风险与缓解

### 5.1 风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 高波动股止损过宽（>8%） | 单笔亏损过大 | 硬止损上限 8% 已存在 |
| 低波动股止盈过低（<5%） | 盈利不足以覆盖成本 | 最小止盈 2×ATR + 滑点保护 |
| 宽止损降低交易次数 | 降低资金利用率 | 不影响入场条件，只影响退出 |
| ATR 在快速波动时滞后 | 止损跟不上市场变化 | 使用 14 日 EMA 平滑 ATR |

### 5.2 回滚方案

保留 `--sl-atr 0` 和 `--tp-atr 0` 作为"使用固定百分比"的 fallback：
```python
atr_based_sl = _compute_atr_stop_loss(...) if atr_multiplier > 0 else fallback_pct
```

---

## 6. 验证计划

1. 运行 `aimoon backtest` 验证改动不破坏现有功能
2. 对比 ATR 方案回测结果与当前结果
3. 检查胜率、盈亏比、总收益等核心指标
4. 逐笔检查退出原因分布
5. 运行 `ruff check src/aimoon` 确保代码质量

---

## 7. 后续优化

- 参数网格搜索：`sl_atr=[1.0, 1.5, 2.0]`, `tp_atr=[2.0, 2.5, 3.0, 4.0]`
- 按市场 regime 动态调整 ATR 乘数
- 按个股流动性调整止损
