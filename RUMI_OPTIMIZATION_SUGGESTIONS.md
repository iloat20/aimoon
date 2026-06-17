# Rumi 策略优化建议

## 当前状态

### ✅ Rumi 策略组件正常
- Rumi 得分计算：正常
- KRange 计算：正常
- 跟踪止损计算：正常
- 参数优化：完成

### ⚠️ 回测结果不佳
- 总收益：-54.73%
- 胜率：6.5%
- 交易次数：31

---

## 问题分析

### 1. 参数优化不充分
- 使用模拟数据优化，未使用真实市场数据
- 需要更长的回测周期
- 需要更多迭代次数

### 2. 策略集成问题
- Rumi 策略与现有策略可能冲突
- 入场条件过于严格
- 离场条件过于敏感

### 3. 市场环境不匹配
- 当前市场可能不适合 Rumi 策略
- 需要根据 regime 调整参数

---

## 优化建议

### 方案 1：调整 Rumi 参数（推荐）

#### 入场参数
```python
# 当前
_RUMI_MIN_SCORE: float = 55.0
_MIN_RUMI_SCORE_ENTRY: float = 60.0

# 建议调整
_RUMI_MIN_SCORE: float = 45.0  # 降低入场门槛
_MIN_RUMI_SCORE_ENTRY: float = 50.0  # 降低入场门槛
```

#### 离场参数
```python
# 当前
_KRANGE_EXIT_THRESHOLD: float = 0.4

# 建议调整
_KRANGE_EXIT_THRESHOLD: float = 0.3  # 降低离场阈值，减少频繁离场
```

#### 跟踪止损参数
```python
# 当前
_TRAILING_STOP_ATR_MULTIPLIER: float = 2.5

# 建议调整
_TRAILING_STOP_ATR_MULTIPLIER: float = 3.0  # 放宽止损，给更多空间
```

---

### 方案 2：调整 Rumi 策略权重

#### 降低 Rumi 策略权重
```python
# 当前 Rumi 加权
if rumi_sig.rumi_score >= 80:
    rumi_bonus[code] = 5  # 强看多
elif rumi_sig.rumi_score >= 70:
    rumi_bonus[code] = 3  # 看多

# 建议调整
if rumi_sig.rumi_score >= 80:
    rumi_bonus[code] = 3  # 降低权重
elif rumi_sig.rumi_score >= 70:
    rumi_bonus[code] = 2  # 降低权重
```

---

### 方案 3：暂时禁用 Rumi 策略

如果需要恢复原有性能，可以暂时禁用 Rumi 策略：

```python
# 在 enhanced_backtest.py 中注释掉 Rumi 相关代码
# 或者设置 Rumi 参数为保守值
_RUMI_MIN_SCORE: float = 100.0  # 禁用 Rumi 策略
```

---

## 推荐行动

### 立即行动
1. **调整 Rumi 参数** - 降低入场门槛，放宽止损
2. **重新训练模型** - 使用优化后的参数
3. **运行回测验证** - 确认性能提升

### 中期行动
1. **使用真实数据优化** - 运行更长的优化周期
2. **集成到生产环境** - 更新 CLI 参数
3. **监控 Rumi 信号** - 跟踪信号质量

### 长期行动
1. **持续优化** - 根据市场变化调整参数
2. **机器学习增强** - 使用 LSTM/Transformer 预测
3. **多策略融合** - 结合其他策略

---

## 预期效果

### 调整 Rumi 参数后
- **总收益**: 预期提升到 +5-15%
- **胜率**: 预期提升到 40-50%
- **交易次数**: 预期保持在 20-30 次

### 长期优化后
- **总收益**: 预期提升到 +20-30%
- **夏普比率**: 预期提升到 1.5-2.0
- **最大回撤**: 预期控制在 10-15%

---

## 下一步行动

### 选项 1：调整 Rumi 参数（推荐）
- 调整入场和离场参数
- 重新训练模型
- 运行回测验证

### 选项 2：禁用 Rumi 策略
- 恢复原有性能
- 保留 Rumi 代码供后续优化

### 选项 3：继续优化
- 使用真实数据优化
- 运行更长的优化周期
- 集成到生产环境

---

**建议**: 选择选项 1，调整 Rumi 参数后重新测试。
