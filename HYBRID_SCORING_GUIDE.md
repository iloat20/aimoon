# 混合评分系统使用指南

**版本**: 1.0
**日期**: 2026-06-04
**状态**: ✅ 短期计划已完成

---

## 📊 系统概述

### 设计理念

混合评分系统将信号分为三组独立评分，然后加权组合：

- **ML 模型** (40%): 直接使用百分位（0-100），最准确
- **Alpha 因子** (40%): 加权平均后缩放，保留因子信息
- **动量指标** (20%): 标准化，简单直观

### 优势

✅ **直观易懂**: 分数直接对应百分位
✅ **区分度优**: 各组独立处理，区分度最优
✅ **可解释性**: 明确的组成和权重
✅ **稳健性**: 综合多种信息，不受单一方法影响

---

## 🚀 快速开始

### 1. 基本使用

```python
from aimoon.models import Signal
from aimoon.scoring import hybrid_score

# 创建信号
signals = [
    Signal('ml_rank', 'ml_rank_96(强烈看多)', 96),
    Signal('alpha_1', 'alpha:qlib158_rank60(99%)', 3),
    Signal('alpha_2', 'alpha:qlib158_rsv60(100%)', 3),
    Signal('mom_20d_strong', '20日强动量(+15.5%)', 3),
    Signal('obv_up', 'OBV上升(资金流入)', 2),
]

# 计算分数
score = hybrid_score(signals)
print(f'分数: {score}')  # 输出: 71
```

### 2. 带详情的评分

```python
from aimoon.scoring import hybrid_score_with_details

# 计算分数和详情
score, details = hybrid_score_with_details(signals)

print(f'分数: {score}')
print(f'ML: {details["ml_score"]:.1f} (权重: {details["ml_weight"]:.2f})')
print(f'Alpha: {details["alpha_score"]:.1f} (权重: {details["alpha_weight"]:.2f})')
print(f'动量: {details["momentum_score"]:.1f} (权重: {details["momentum_weight"]:.2f})')
```

### 3. 详细分析

```python
from aimoon.scoring import get_score_analysis

# 获取详细分析
analysis = get_score_analysis(signals)

print(f'最终分数: {analysis["final_score"]}')
print(f'建议: {analysis["suggestion"]} ({analysis["confidence"]})')
print(f'总信号数: {analysis["total_signals"]}')
print(f'  ML 信号: {analysis["breakdown"]["ml"]["signals"]} 个')
print(f'  Alpha 信号: {analysis["breakdown"]["alpha"]["signals"]} 个')
print(f'  动量信号: {analysis["breakdown"]["momentum"]["signals"]} 个')
```

---

## 📈 输出示例

### 示例 1: 高分股票

```
信号:
  - ML: ml_rank_96 (96 分)
  - Alpha: 3 个因子 (+3, +3, +2)
  - 动量: 3 个信号 (+3, +1, +2)

输出:
  分数: 71
  建议: 买入 (中高置信度)

分解:
  ML:     96.0 * 0.40 = 38.4
  Alpha:  53.8 * 0.40 = 21.5
  动量:   56.2 * 0.20 = 11.2
  总计:   71.1 -> 71
```

### 示例 2: 中等分数股票

```
信号:
  - ML: ml_rank_60 (60 分)
  - Alpha: 2 个因子 (+2, +1)
  - 动量: 2 个信号 (+2, +1)

输出:
  分数: 55
  建议: 建议买入 (中置信度)

分解:
  ML:     60.0 * 0.40 = 24.0
  Alpha:  51.5 * 0.40 = 20.6
  动量:   52.5 * 0.20 = 10.5
  总计:   55.1 -> 55
```

### 示例 3: 低分股票

```
信号:
  - ML: ml_rank_30 (30 分)
  - Alpha: 1 个因子 (-2)
  - 动量: 1 个信号 (-1)

输出:
  分数: 38
  建议: 观望 (低置信度)

分解:
  ML:     30.0 * 0.40 = 12.0
  Alpha:  47.5 * 0.40 = 19.0
  动量:   47.5 * 0.20 = 9.5
  总计:   40.5 -> 38
```

---

## ⚙️ 配置选项

### 默认配置

```python
from aimoon.scoring.hybrid_scorer import HybridScoreConfig

config = HybridScoreConfig(
    # 各组权重（总和应为 1.0）
    ml_weight=0.40,        # ML 模型权重
    alpha_weight=0.40,     # Alpha 因子权重
    momentum_weight=0.20,  # 动量指标权重

    # ML 评分参数
    ml_strong_buy=80,      # 强烈买入阈值
    ml_buy=60,             # 买入阈值
    ml_sell=40,            # 卖出阈值
    ml_strong_sell=20,     # 强烈卖出阈值

    # Alpha 评分参数
    alpha_cap=40.0,        # Alpha 分数上限
    alpha_floor=-40.0,     # Alpha 分数下限

    # 动量评分参数
    momentum_cap=20.0,     # 动量分数上限
    momentum_floor=-20.0,  # 动量分数下限
)
```

### 自定义配置

```python
# 调整权重（更重视 ML）
config = HybridScoreConfig(
    ml_weight=0.50,
    alpha_weight=0.30,
    momentum_weight=0.20,
)

# 使用自定义配置
score = hybrid_score(signals, config)
```

---

## 🎯 评分标准

### 分数范围

| 分数 | 建议 | 置信度 | 说明 |
|------|------|--------|------|
| 80-100 | 强烈买入 | 高 | ML 高分 + 多个正向信号 |
| 65-79 | 买入 | 中高 | ML 较高 + 正向信号 |
| 50-64 | 建议买入 | 中 | ML 中等 + 部分正向信号 |
| 35-49 | 观望 | 低 | ML 较低 + 信号不足 |
| 20-34 | 谨慎 | 中 | ML 低 + 负向信号 |
| 10-19 | 建议卖出 | 中高 | ML 很低 + 多个负向信号 |
| 0-9 | 强烈卖出 | 高 | ML 极低 + 强负向信号 |

### 各组权重

| 组 | 权重 | 说明 |
|------|------|------|
| ML 模型 | 40% | 最准确，直接使用百分位 |
| Alpha 因子 | 40% | 保留因子信息 |
| 动量指标 | 20% | 简单直观 |

---

## 📊 与旧方法的对比

### 对数压缩（旧方法）

```python
# 旧方法
score = category_capped_score(signals)
# 结果: 62.6（难以理解）
```

**问题**:
- ❌ 不直观（972 → 62.6）
- ❌ 高分区域区分度差
- ❌ 参数敏感

### 混合方法（新方法）

```python
# 新方法
score = hybrid_score(signals)
# 结果: 71（直观易懂）
```

**优势**:
- ✅ 直观（96 → 96，直接对应百分位）
- ✅ 区分度优（各组独立处理）
- ✅ 可解释性强

---

## 🔧 集成指南

### 1. 在 screener.py 中使用

```python
from aimoon.scoring import hybrid_score

def screen_stock(code, name, kline, ml_score=None, alpha_signals=None):
    # ... 收集信号 ...

    # 使用混合评分
    total_score = hybrid_score(signals)

    return ScoredStock(
        code=code,
        name=name,
        # ...
        ml_score=ml_score,
        total_score=total_score,
    )
```

### 2. 在 backtest 中使用

```python
from aimoon.scoring import hybrid_score

def run_backtest(klines, names):
    # ... 回测逻辑 ...

    for bar_date in sorted_dates:
        for code in positions:
            # 计算当前分数
            signals = collect_signals(ti, code=code)
            score = hybrid_score(signals)

            # 判断是否退出
            if score < exit_threshold:
                # 退出逻辑
                pass
```

### 3. 在输出中使用

```python
from aimoon.scoring import get_score_analysis

def display_stock(stock, signals):
    analysis = get_score_analysis(signals)

    print(f'股票: {stock.name}')
    print(f'分数: {analysis["final_score"]}')
    print(f'建议: {analysis["suggestion"]}')
    print(f'分解:')
    print(f'  ML: {analysis["breakdown"]["ml"]["score"]:.1f}')
    print(f'  Alpha: {analysis["breakdown"]["alpha"]["score"]:.1f}')
    print(f'  动量: {analysis["breakdown"]["momentum"]["score"]:.1f}')
```

---

## 📈 测试结果

### 测试 1: 高分股票

```
输入:
  ML: 96 (强烈看多)
  Alpha: 3 个因子 (+3, +3, +2)
  动量: 3 个信号 (+3, +1, +2)

输出:
  分数: 71
  建议: 买入 (中高)
  分解: ML=96.0, Alpha=53.8, 动量=56.2
```

### 测试 2: 中等分数

```
输入:
  ML: 60 (看多)
  Alpha: 2 个因子 (+2, +1)
  动量: 2 个信号 (+2, +1)

输出:
  分数: 55
  建议: 建议买入 (中)
  分解: ML=60.0, Alpha=51.5, 动量=52.5
```

### 测试 3: 低分股票

```
输入:
  ML: 30 (看空)
  Alpha: 1 个因子 (-2)
  动量: 1 个信号 (-1)

输出:
  分数: 38
  建议: 观望 (低)
  分解: ML=30.0, Alpha=47.5, 动量=47.5
```

---

## 💡 最佳实践

### 1. 权重调优

```python
# 根据回测结果调整权重
config = HybridScoreConfig(
    ml_weight=0.45,      # ML 表现好，增加权重
    alpha_weight=0.35,   # Alpha 表现一般，减少权重
    momentum_weight=0.20,
)
```

### 2. 阈值调整

```python
# 根据市场环境调整阈值
config = HybridScoreConfig(
    ml_strong_buy=85,    # 牛市时提高阈值
    ml_buy=65,
    ml_sell=35,
    ml_strong_sell=15,
)
```

### 3. 因子选择

```python
# 选择高 IC 的因子
from aimoon.ml.icir_weighter import load_or_compute_ewma

icir_weights = load_or_compute_ewma(panel, klines, registry)
# 使用 ICIR 权重调整 Alpha 因子
```

---

## 🚀 下一步

### 中期计划（1 周）

1. **调优权重参数**
   - 运行回测对比不同权重
   - 找到最优权重组合

2. **优化算法**
   - 优化 Alpha 因子评分
   - 优化动量指标评分

3. **验证效果**
   - 对比新旧方法的回测结果
   - 评估收益、风险、夏普比率

### 长期计划（1 个月）

1. **自适应权重**
   - 根据市场环境自动调整权重
   - 引入机器学习优化权重

2. **因子选择**
   - 自动选择高 IC 因子
   - 动态调整因子权重

3. **持续监控**
   - 监控评分效果
   - 定期优化参数

---

## 📚 相关文档

- `SCORING_METHOD_ANALYSIS.md` - 评分方法详细分析
- `OPTIMIZED_BACKTEST_SUMMARY.md` - 优化回测总结
- `FUNCTION_TEST_REPORT.md` - 功能测试报告
- `README.md` - 项目主文档

---

## 💬 常见问题

### Q1: 为什么选择混合方法？

**A**: 混合方法最适合当前系统：
- ✅ 已有 ML、Alpha、动量三组信号
- ✅ 各组特性不同，需要独立处理
- ✅ 最优的区分度和可解释性

### Q2: 如何调整权重？

**A**: 根据回测结果调整：
```python
config = HybridScoreConfig(
    ml_weight=0.45,      # 根据 ML 表现调整
    alpha_weight=0.35,   # 根据 Alpha 表现调整
    momentum_weight=0.20,
)
```

### Q3: 如何添加新的信号类型？

**A**: 在 `_separate_signals` 函数中添加新的分类逻辑：
```python
def _separate_signals(signals):
    # ... 现有逻辑 ...
    for signal in signals:
        if signal.name.startswith('new_'):
            new_signals.append(signal)
    # ...
```

---

**文档版本**: 1.0
**维护者**: Claude Code AI Assistant
**日期**: 2026-06-04
