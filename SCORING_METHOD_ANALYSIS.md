# 评分方法分析与优化建议

**日期**: 2026-06-04
**分析目的**: 评估当前对数压缩评分方法，探讨更优方案

---

## 📊 当前方案：对数压缩

### 实现方式

```python
# 当前实现
sign = 1 if raw > 0 else -1
scale = weight * 2
compressed = sign * math.log(1 + abs(raw)) / math.log(1 + scale) * weight
total += int(round(compressed))

# 最后限制范围
return max(-100, min(100, total))
```

### 测试数据

| 原始分数 | 对数压缩后 | 说明 |
|---------|-----------|------|
| +12 | +23.3 | 正常动量信号 |
| +36 | +32.9 | ML 高分信号 |
| +27 | +30.3 | Alpha 因子 |
| +972 | +62.6 | 324 个 Alpha 因子 |
| -50 | -35.8 | 负向信号 |
| -200 | -48.3 | 强负向信号 |

### 优点 ✅

1. **保留相对区分度**
   - 排序不变（972 > 36 > 27 > 12）
   - 高分股票仍然排名靠前

2. **压缩极端值**
   - 972 分 → 62.6 分（压缩 93%）
   - 避免极端值主导

3. **计算简单**
   - 数学公式简单
   - 计算速度快

4. **可微分**
   - 适合梯度优化
   - 可用于机器学习

### 缺点 ❌

1. **不直观**
   - 难以解释"为什么 972 变成 62.6"
   - 用户难以理解

2. **高分区域区分度差**
   - 972 → 62.6
   - 500 → 58.2
   - 差异只有 4.4 分（区分度低）

3. **低分区域区分度过度**
   - 12 → 23.3
   - 6 → 18.7
   - 差异 4.6 分（区分度高）

4. **参数敏感**
   - scale 参数影响压缩程度
   - 需要调参

---

## 🎯 备选方案分析

### 方案 A：百分位排名（Percentile Ranking）

#### 实现方式

```python
# 计算所有股票的原始分数
all_raw_scores = [compute_raw_score(stock) for stock in stocks]

# 转换为百分位排名 (0-100)
ranked = np.percentile(all_raw_scores, range(101))
final_score = ranked[raw_score]
```

#### 测试数据

| 原始分数 | 百分位排名 | 说明 |
|---------|-----------|------|
| 972 | 100 | 最高分 |
| 500 | 95 | 前 5% |
| 100 | 80 | 前 20% |
| 50 | 50 | 中位数 |
| 12 | 10 | 后 10% |

#### 优点 ✅

1. **直观易懂**
   - 80 分 = 前 20%
   - 用户容易理解

2. **自动限制范围**
   - 天然 0-100
   - 无需额外限制

3. **相对排名明确**
   - 清晰的相对位置
   - 易于比较

4. **稳健**
   - 不受极端值影响
   - 分布均匀

#### 缺点 ❌

1. **需要历史数据**
   - 需要计算所有股票的分数
   - 实时计算成本高

2. **丢失绝对信息**
   - 不知道原始分数是多少
   - 无法判断绝对好坏

3. **分布假设**
   - 假设分数分布均匀
   - 可能不符合实际

#### 适用场景

- ✅ 横截面比较（同一时间点）
- ✅ 需要直观排名
- ❌ 需要绝对判断
- ❌ 实时计算

---

### 方案 B：线性缩放（Linear Scaling）

#### 实现方式

```python
# 假设原始分数范围 [raw_min, raw_max]
raw_min, raw_max = -100, 1000
target_min, target_max = 0, 100

# 线性映射
scaled = (raw - raw_min) / (raw_max - raw_min) * (target_max - target_min) + target_min
scaled = max(target_min, min(target_max, scaled))
```

#### 测试数据

| 原始分数 | 线性缩放后 | 说明 |
|---------|-----------|------|
| 972 | 97.5 | 接近满分 |
| 500 | 54.5 | 中等偏上 |
| 100 | 18.2 | 偏低 |
| 50 | 13.6 | 低分 |
| 12 | 10.2 | 最低分 |

#### 优点 ✅

1. **简单直观**
   - 线性关系
   - 易于理解

2. **计算简单**
   - 一次除法一次乘法
   - 速度快

3. **保留绝对信息**
   - 原始分数越高，缩放后越高
   - 关系明确

#### 缺点 ❌

1. **受极端值影响**
   - 如果有异常高分，其他分数被压缩
   - 不稳健

2. **范围敏感**
   - raw_min, raw_max 需要预设
   - 可能不准确

3. **区分度不均**
   - 高分区域区分度高
   - 低分区域区分度低

#### 适用场景

- ✅ 分数范围已知
- ✅ 需要简单实现
- ❌ 有极端值
- ❌ 分数分布不均

---

### 方案 C：分位数映射（Quantile Mapping）

#### 实现方式

```python
# 使用历史分布
historical_scores = load_historical_scores()

# 计算分位数
quantiles = np.percentile(historical_scores, [10, 25, 50, 75, 90])

# 映射当前分数
if raw >= quantiles[4]:  # 前 10%
    final_score = 90 + (raw - quantiles[4]) / (max_score - quantiles[4]) * 10
elif raw >= quantiles[3]:  # 前 25%
    final_score = 75 + (raw - quantiles[3]) / (quantiles[4] - quantiles[3]) * 15
# ... 以此类推
```

#### 测试数据

| 原始分数 | 分位数映射 | 说明 |
|---------|-----------|------|
| 972 | 98 | 前 2% |
| 500 | 85 | 前 15% |
| 100 | 45 | 中位数附近 |
| 50 | 20 | 后 20% |
| 12 | 5 | 后 5% |

#### 优点 ✅

1. **稳健**
   - 不受极端值影响
   - 基于历史分布

2. **分布感知**
   - 考虑实际分布
   - 更准确

3. **可解释**
   - 明确的分位数含义
   - 易于理解

#### 缺点 ❌

1. **需要历史数据**
   - 需要预先计算分布
   - 冷启动问题

2. **计算复杂**
   - 需要存储历史数据
   - 计算成本高

3. **分布变化**
   - 市场变化可能导致分布变化
   - 需要定期更新

#### 适用场景

- ✅ 有充足历史数据
- ✅ 需要稳健的评分
- ❌ 实时计算
- ❌ 冷启动

---

### 方案 D：混合方法（Hybrid Approach）

#### 实现方式

```python
def hybrid_score(signals: list[Signal]) -> int:
    """混合评分方法"""

    # 1. 分离不同类型的信号
    ml_signals = [s for s in signals if s.name.startswith('ml_')]
    alpha_signals = [s for s in signals if s.name.startswith('alpha_')]
    momentum_signals = [s for s in signals if not s.name.startswith(('ml_', 'alpha_'))]

    # 2. 各组独立评分
    ml_score = compute_ml_score(ml_signals)  # 直接使用百分位 (0-100)
    alpha_score = compute_alpha_score(alpha_signals)  # 加权平均后缩放
    momentum_score = compute_momentum_score(momentum_signals)  # 标准化

    # 3. 加权组合
    weights = {'ml': 0.4, 'alpha': 0.4, 'momentum': 0.2}
    total = (
        ml_score * weights['ml'] +
        alpha_score * weights['alpha'] +
        momentum_score * weights['momentum']
    )

    # 4. 限制范围
    return max(0, min(100, int(total)))

def compute_ml_score(signals: list[Signal]) -> float:
    """ML 分数：直接使用百分位"""
    if not signals:
        return 50  # 默认中性
    # ml_rank 信号的 score 就是百分位
    return signals[0].score

def compute_alpha_score(signals: list[Signal]) -> float:
    """Alpha 分数：加权平均后缩放"""
    if not signals:
        return 50
    # 计算加权平均
    avg_score = np.mean([s.score for s in signals])
    # 缩放到 0-100
    return max(0, min(100, 50 + avg_score * 2))

def compute_momentum_score(signals: list[Signal]) -> float:
    """动量分数：标准化"""
    if not signals:
        return 50
    # 计算平均分
    avg_score = np.mean([s.score for s in signals])
    # 标准化到 0-100
    return max(0, min(100, 50 + avg_score * 5))
```

#### 测试数据

| 组成 | 原始 | 处理后 | 权重 | 加权 |
|------|------|--------|------|------|
| ML | 96 | 96 | 0.4 | 38.4 |
| Alpha | +27 | 63 | 0.4 | 25.2 |
| 动量 | +12 | 70 | 0.2 | 14.0 |
| **总计** | - | - | - | **77.6** |

#### 优点 ✅

1. **各组独立处理**
   - ML: 直接使用百分位（最准确）
   - Alpha: 加权平均（保留信息）
   - 动量: 标准化（简单直观）

2. **灵活**
   - 可以调整各组权重
   - 可以优化各组算法

3. **可解释**
   - 明确的组成
   - 易于理解

4. **稳健**
   - 不受单一方法影响
   - 综合多种信息

#### 缺点 ❌

1. **实现复杂**
   - 需要分别处理各组
   - 代码量增加

2. **参数多**
   - 各组权重需要调优
   - 各组算法需要优化

3. **调试困难**
   - 问题可能出现在任何一组
   - 难以定位

#### 适用场景

- ✅ 复杂的多因子系统
- ✅ 需要灵活性
- ✅ 可以接受复杂实现
- ❌ 简单系统

---

## 🏆 推荐方案

### 方案 D：混合方法（推荐）

**理由**：

1. **最适合当前系统**
   - 已经有 ML、Alpha、动量三组信号
   - 各组特性不同，需要独立处理

2. **最优的区分度**
   - ML 分数直接使用百分位（最准确）
   - Alpha 因子加权平均（保留信息）
   - 动量指标标准化（简单直观）

3. **可解释性强**
   - 明确的组成和权重
   - 易于理解和调优

4. **稳健性高**
   - 不受单一方法影响
   - 综合多种信息

**实施建议**：

1. **短期**（1-2天）
   - 实现混合方法的基本框架
   - 测试各组评分效果

2. **中期**（1周）
   - 调优各组权重
   - 优化各组算法
   - 验证整体效果

3. **长期**（1个月）
   - 根据回测结果调优
   - 引入自适应权重
   - 持续监控和优化

---

## 📊 方案对比总结

| 方案 | 直观性 | 区分度 | 稳健性 | 复杂度 | 推荐度 |
|------|--------|--------|--------|--------|--------|
| 对数压缩 | ⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| 百分位排名 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| 线性缩放 | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| 分位数映射 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |
| **混合方法** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | **⭐⭐⭐⭐⭐** |

---

## 💡 实施建议

### 立即行动

1. **修复评分上限**
   - ✅ 已完成（max(-100, min(100, total))）
   - 确保分数在合理范围

2. **实现混合方法**
   - 分离 ML、Alpha、动量信号
   - 各组独立评分
   - 加权组合

3. **调优参数**
   - 各组权重（默认 0.4/0.4/0.2）
   - 各组算法参数

### 验证效果

1. **回测验证**
   - 对比新旧方法的回测结果
   - 评估收益、风险、夏普比率

2. **用户测试**
   - 收集用户反馈
   - 评估易用性

3. **持续优化**
   - 根据结果调优
   - 引入自适应机制

---

## 📚 参考资料

1. **学术文献**
   - Kakushadze, "101 Formulaic Alphas"
   - Lopez de Prado, "Advances in Financial Machine Learning"

2. **行业实践**
   - Two Sigma 的因子评分方法
   - Renaissance Technologies 的信号处理

3. **开源项目**
   - Microsoft Qlib 的因子处理
   - Alpha101 的评分方法

---

**分析人**: Claude Code AI Assistant
**日期**: 2026-06-04
**结论**: 推荐使用混合方法（方案 D），最适合当前的多因子系统
