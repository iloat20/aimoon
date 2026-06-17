# Rumi 策略 + KRange 自适应离场机制 - 完整实现

## 执行时间
2026-06-05

## ✅ 实现完成

### Rumi 策略概述

**Rumi** (Relative Strength + Momentum) 是一种基于动量和相对强度的量化交易策略。

**核心组件**:
1. **动量得分 (Momentum Score)**: 基于价格动量和成交量动量
2. **相对强度 (Relative Strength)**: 基于价格相对于移动平均线的强度
3. **波动率得分 (Volatility Score)**: 基于 ATR 和价格波动范围
4. **Rumi 综合得分**: 加权组合以上三个维度

**Rumi 公式**:
```
Rumi Score = (Momentum × 0.4) + (Relative Strength × 0.3) + (Volatility × 0.3)
```

---

### KRange 自适应离场机制

**KRange** (Keltner Range) 是一种基于波动率的自适应离场机制。

**核心组件**:
1. **KRange 上轨**: 当前价格 + 2 × ATR
2. **KRange 下轨**: 当前价格 - 2 × ATR
3. **ATR (Average True Range)**: 衡量价格波动的指标

**KRange 公式**:
```
KRange Upper = Close + (Multiplier × ATR)
KRange Lower = Close - (Multiplier × ATR)
```

---

### 智能跟踪止损机制

**自适应跟踪止损**根据以下因素动态调整:

1. **Rumi 得分**: 高 Rumi 得分 → 更宽松止损，低 Rumi 得分 → 更紧止损
2. **市场状态 (Regime)**: 牛市 → 宽松，熊市 → 紧，危机 → 最紧
3. **盈利水平**: 盈利越高 → 止损越紧（保护利润）
4. **波动率**: 波动率越高 → 止损越紧

**止损阶梯**:
- 盈利 ≥ 10%: 保本保护（止损 = max(入场价, 最高价 - 1.5×ATR)）
- 盈利 ≥ 5%: 锁定 50% 利润
- 盈利 ≥ 2%: 保本
- 盈利 < 2%: 基于 ATR 的动态止损

---

## 📊 测试结果

### 测试通过
```
✅ Rumi strategy module imported successfully
✅ Rumi Score: 34.3
✅ Momentum Score: 47.3
✅ Relative Strength: 51.2
✅ Volatility: 0.0
✅ KRange Upper: 146.47
✅ KRange Lower: 64.60
✅ ATR Value: 20.47
✅ Adaptive Trailing Stop: 103.00
```

---

## 🎯 Rumi 策略详解

### 1. Rumi 综合得分计算

```python
def compute_rumi_score(kline, lookback=20, 
                       momentum_weight=0.4,
                       relative_strength_weight=0.3,
                       volatility_weight=0.3):
    # 1. 动量得分 (0-100)
    momentum_score = (price_momentum * 100 + volume_momentum * 50 + 100) / 2
    
    # 2. 相对强度 (0-100)
    relative_strength = (price_vs_ma20 * 0.6 + price_vs_ma60 * 0.4 + 100) / 2
    
    # 3. 波动率得分 (0-100)
    volatility = max(0, 100 - atr_ratio * 1000)
    
    # 4. 综合 Rumi 得分
    rumi_score = (momentum * 0.4 + relative_strength * 0.3 + volatility * 0.3)
    
    return rumi_score, momentum_score, relative_strength, volatility
```

### 2. KRange 计算

```python
def compute_krange(kline, atr_period=14, krange_multiplier=2.0):
    # 计算 ATR
    atr = TrueRange.rolling(window=atr_period).mean()
    
    # 计算 KRange 上下轨
    krange_upper = current_price + krange_multiplier * atr
    krange_lower = current_price - krange_multiplier * atr
    
    return krange_upper, krange_lower, atr
```

### 3. 自适应跟踪止损

```python
def compute_adaptive_trailing_stop(position, current_price, atr_value, 
                                   rumi_score, regime):
    # 基础止损：基于 ATR
    base_stop = highest_price - 2.0 * atr_value
    
    # Rumi 得分调整：高得分 → 宽松，低得分 → 紧
    rumi_factor = rumi_score / 100.0
    rumi_adjusted_stop = highest_price - (2.0 - rumi_factor * 0.5) * atr_value
    
    # 市场状态调整
    regime_factor = {"bull": 1.2, "bear": 0.8, "sideways": 1.0, ...}
    regime_adjusted_stop = highest_price - (2.0 * regime_factor) * atr_value
    
    # 盈利保护
    if pnl >= 0.10:
        profit_stop = max(entry_price, highest_price - 1.5 * atr_value)
    elif pnl >= 0.05:
        profit_stop = entry_price + (highest_price - entry_price) * 0.5
    elif pnl >= 0.02:
        profit_stop = entry_price
    
    # 取最高止损
    trailing_stop = max(base_stop, rumi_adjusted_stop, regime_adjusted_stop, profit_stop)
    
    # 确保止损不低于入场价格的 8%
    min_stop = entry_price * 0.92
    trailing_stop = max(trailing_stop, min_stop)
    
    return trailing_stop
```

---

## 🚀 使用方式

### 1. 生成 Rumi 信号

```python
from aimoon.rumi_strategy import generate_rumi_signals

# 生成信号
signals = generate_rumi_signals(klines, names, lookback=20, min_rumi_score=60.0)

# 查看信号
for signal in signals[:10]:
    print(f"{signal.code}: Rumi={signal.rumi_score:.1f}, Type={signal.signal_type}")
```

### 2. 检查 KRange 离场信号

```python
from aimoon.rumi_strategy import check_krange_exit

# 检查离场信号
exit_signal = check_krange_exit(
    position=position,
    kline=kline,
    current_date=current_date,
    rumi_score=rumi_score,
    regime="bull",
    exit_threshold=0.5,
)

if exit_signal:
    print(f"Exit: {exit_signal.exit_type} at {exit_signal.exit_price}")
    print(f"Reason: {exit_signal.exit_reason}")
```

### 3. 计算自适应跟踪止损

```python
from aimoon.rumi_strategy import compute_adaptive_trailing_stop

# 计算跟踪止损
trailing_stop = compute_adaptive_trailing_stop(
    position=position,
    current_price=current_price,
    atr_value=atr_value,
    rumi_score=rumi_score,
    regime="bull",
)

print(f"Trailing Stop: {trailing_stop:.2f}")
```

---

## 📈 策略优势

### 1. Rumi 策略优势 ✅
- **多维度评估**: 动量 + 相对强度 + 波动率
- **自适应**: 根据市场状态调整
- **信号清晰**: 0-100 得分，易于决策
- **风险控制**: 低波动率优先

### 2. KRange 机制优势 ✅
- **波动率自适应**: 基于 ATR 动态调整
- **上下轨清晰**: 明确的入场/离场信号
- **风险可控**: 限制单笔亏损
- **利润保护**: 上轨突破自动止盈

### 3. 智能跟踪止损优势 ✅
- **动态调整**: 根据 Rumi 得分和市场状态调整
- **阶梯式保护**: 盈利越高，止损越紧
- **风险控制**: 最大亏损限制 8%
- **利润保护**: 锁定已实现利润

---

## 🎯 集成到回测引擎

### 集成步骤

1. **在 enhanced_backtest.py 中导入 Rumi 策略**
```python
from aimoon.rumi_strategy import (
    generate_rumi_signals,
    check_krange_exit,
    compute_adaptive_trailing_stop,
)
```

2. **在 Phase 4 中使用 Rumi 信号**
```python
# 生成 Rumi 信号
rumi_signals = generate_rumi_signals(klines, names)

# 使用 Rumi 得分优化入场
for signal in rumi_signals:
    if signal.rumi_score >= 60 and signal.signal_type == "buy":
        # 添加到候选列表
        scored_candidates.append((signal.code, signal.name, signal.rumi_score))
```

3. **在 Phase 1 中使用 KRange 离场**
```python
# 检查 KRange 离场信号
for code, pos in positions.items():
    exit_signal = check_krange_exit(
        position=pos,
        kline=klines[code],
        current_date=bar_date,
        rumi_score=pos.rumi_score,
        regime=current_regime,
    )
    
    if exit_signal:
        # 执行离场
        to_close.append((code, exit_signal.exit_price, exit_signal.exit_type, ...))
```

4. **使用自适应跟踪止损**
```python
# 计算跟踪止损
trailing_stop = compute_adaptive_trailing_stop(
    position=pos,
    current_price=current_price,
    atr_value=atr_value,
    rumi_score=rumi_score,
    regime=current_regime,
)

# 更新止损
pos.trailing_stop = trailing_stop
```

---

## 📊 预期效果

### 入场优化
- ✅ **信号质量提升**: Rumi 得分综合评估动量、相对强度、波动率
- ✅ **入场时机优化**: 高 Rumi 得分时入场
- ✅ **风险筛选**: 低波动率优先

### 离场优化
- ✅ **自适应止损**: 根据 Rumi 得分和市场状态调整
- ✅ **利润保护**: 阶梯式移动止损
- ✅ **风险控制**: KRange 上下轨限制亏损

### 整体效果
- ✅ **收益提升**: 更好的入场和离场时机
- ✅ **风险降低**: 智能止损控制亏损
- ✅ **稳定性**: 自适应机制应对不同市场环境

---

## 🎯 总结

### 已实现功能
- ✅ **Rumi 策略**: 动量 + 相对强度 + 波动率综合评估
- ✅ **KRange 机制**: 基于波动率的自适应离场
- ✅ **智能跟踪止损**: 根据 Rumi 得分和市场状态动态调整
- ✅ **多维度止损**: 止损、止盈、跟踪止损、时间止损

### 关键优势
1. ✅ **信号质量**: Rumi 得分综合评估多维度指标
2. ✅ **风险控制**: KRange 机制限制亏损
3. ✅ **利润保护**: 阶梯式移动止损锁定利润
4. ✅ **自适应性**: 根据市场状态动态调整

### 后续行动
1. ⏳ 集成到回测引擎
2. ⏳ 优化 Rumi 参数
3. ⏳ 测试不同市场环境
4. ⏳ 实盘验证

---

**执行人**: AI 策略开发系统
**执行日期**: 2026-06-05
**执行状态**: ✅ 实现完成
**下一步**: 集成到回测引擎并测试
