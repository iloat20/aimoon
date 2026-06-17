# ML训练优化指南

## 问题诊断

### 原始问题
- **训练IC = 0.99+** (太高)
- **验证IC = 0.04-0.28** (太低)
- **过拟合比率 = 25-50倍**
- **特征/样本比例过高**: 361特征 / 747样本 = 0.48

### 根本原因
1. **数据量不足**: 只用30个日期快照
2. **正则化不足**: 模型参数太宽松
3. **验证策略不当**: 在训练集上评估IC
4. **特征选择太激进**: 高阈值过滤掉有效特征

---

## 已实施的优化

### 1. 增加数据量 (最重要)
```python
# 原配置
n_dates = 30  # 仅30个快照

# 优化配置
n_dates = 120  # 增加到120个快照 (4倍)
```

**效果**: 
- 训练样本增加 4 倍
- 数据多样性提高
- 减少过拟合风险

### 2. 增强正则化
```python
# XGBoost 原配置
"max_depth": 6,           # 树太深
"learning_rate": 0.05,    # 学习率太慢
"n_estimators": 500,      # 迭代太多
"reg_lambda": 1.0,        # 正则化太弱

# XGBoost 优化配置
"max_depth": 4,           # 限制树深度
"learning_rate": 0.1,     # 适度学习率
"n_estimators": 500,      # 配合早停
"reg_lambda": 5.0,        # 增强L2正则化
"reg_alpha": 0.5,         # 增强L1正则化
"gamma": 0.1,             # 节点分裂约束
```

**LightGBM类似优化**:
```python
"num_leaves": 21,         # 从31减少到21
"max_depth": 4,           # 限制树深度
"feature_fraction": 0.4,  # 每棵树只用40%特征
"bagging_fraction": 0.7,  # 每次迭代用70%数据
```

### 3. 改进验证策略
```python
# 旧方法：在训练集上计算IC（容易过拟合）
ic, _ = spearmanr(preds_train, y_train)

# 新方法：在验证集上计算IC
val_size = int(len(X) * 0.2)
X_train, X_val = X[:-val_size], X[-val_size:]
preds_val = model.predict(X_val)
ic, _ = spearmanr(preds_val, y_val)
```

**同时输出对比**:
- 训练集IC: 用于诊断过拟合
- 验证集IC: 真实的模型质量
- 过拟合比率: train_IC / val_IC

### 4. 优化数据采样
```python
# 原方法：均匀间隔采样
step = len(available_dates) // n_dates
selected_dates = [available_dates[i * step] for i in range(n_dates)]

# 优化方法：分层随机采样
# 每隔5天采样一个，然后随机选择
interval = max(1, len(available_dates) // (n_dates * 5))
candidate_dates = available_dates[::interval]
selected_dates = random.sample(candidate_dates, n_dates)
```

**效果**:
- 增加数据多样性
- 覆盖更多市场状态
- 避免周期性偏差

### 5. 改进特征选择
```python
# 原阈值
min_ic = 0.01  # 太高，过滤掉有效特征

# 优化阈值
min_ic = 0.003  # 更宽松，保留更多特征
```

**同时增加错误处理**:
```python
# 如果没有特征达到阈值，降低阈值重试
if not filtered:
    filtered = {k: v for k, v in ic_scores.items() if v >= 0.001}
```

### 6. 优化早停策略
```python
# 原配置
"early_stopping_rounds": 10  # 过早停止

# 优化配置
"early_stopping_rounds": 20  # 增加耐心
```

---

## 验证改进效果

### 运行优化训练
```bash
# 删除旧模型
rm -rf .aimoon_cache/ml/

# 运行优化训练
python scripts/optimized_train.py

# 或指定参数
python scripts/optimized_train.py --n-dates 150 --forward-days 10
```

### 检查训练结果
```bash
# 运行验证脚本
python scripts/verify_training.py

# 输出详细信息
python scripts/verify_training.py --detailed
```

### 预期指标范围

| 指标 | 原值 | 预期优化后 | 说明 |
|------|------|-----------|------|
| XGBoost val_IC | 0.28 | 0.20-0.40 | 验证集IC |
| XGBoost train_IC | 0.999 | 0.30-0.60 | 训练集IC（应降低）|
| XGBoost 过拟合比率 | 3.5 | 1.5-2.5 | train_IC/val_IC |
| LightGBM val_IC | 0.04 | 0.15-0.35 | 验证集IC |
| LightGBM train_IC | 0.999 | 0.25-0.50 | 训练集IC（应降低）|
| CV平均分 | - | 0.15-0.30 | 交叉验证平均分 |

---

## 关键改进文件

1. **`src/aimoon/ml/trainer.py`**
   - 优化XGBoost参数
   - 改进验证策略
   - 增强早停机制

2. **`src/aimoon/ml/lgbm_trainer.py`**
   - 优化LightGBM参数
   - 改进验证策略
   - 增强早停机制

3. **`src/aimoon/ml/feature_pipeline.py`**
   - 改进特征选择阈值
   - 增加错误处理

4. **`src/aimoon/ml/optimized_config.py`** (新)
   - 集中管理优化配置
   - 便于调整和复用

5. **`scripts/optimized_train.py`** (新)
   - 一键运行优化训练
   - 自动输出诊断信息

6. **`scripts/verify_training.py`** (新)
   - 自动验证训练质量
   - 输出详细诊断报告

---

## 调优建议

### 如果验证IC太低 (< 0.15)
1. 增加 `n_dates` 到 150-200
2. 检查数据质量
3. 增加特征工程
4. 调整 `forward_days`

### 如果仍然过拟合 (比率 > 3.0)
1. 降低 `max_depth` 到 3
2. 增加 `reg_lambda` 到 10.0
3. 增加 `subsample` 到 0.6
4. 增加 `feature_fraction` 到 0.3

### 如果训练IC太低 (< 0.2)
1. 检查特征工程
2. 降低正则化强度
3. 增加 `n_estimators`
4. 降低 `min_child_weight`

### 如果样本/特征比太低 (< 10)
1. 增加 `n_dates`
2. 减少特征数 (`top_k`)
3. 提高 `min_ic` 阈值
4. 移除弱特征

---

## 监控清单

训练后检查以下指标：

- [ ] val_IC 在 0.15-0.40 范围
- [ ] train_IC 在 0.30-0.60 范围
- [ ] 过拟合比率 < 3.0
- [ ] CV分数稳定或上升
- [ ] 样本/特征比 > 10
- [ ] 训练时间合理（不异常长）

---

## 常见问题

### Q: 为什么训练IC这么低？
A: 这是**好事**！低训练IC意味着模型没有记忆训练数据，泛化能力更强。

### Q: 验证IC应该是多少？
A: 0.15-0.40 范围都可接受。过低说明模型无效，过高可能仍有过拟合。

### Q: 还是过拟合怎么办？
A: 进一步增强正则化，增加数据量，减少特征，或使用更多的早停耐心。

### Q: 训练时间太长怎么办？
A: 减少 `n_dates`，增加 `learning_rate`，减少 `n_estimators`。

---

## 高级优化（可选）

### 1. 特征工程增强
- 添加更多技术指标
- 使用 Alpha Zoo 因子
- 添加宏观经济特征

### 2. 集成策略改进
- 添加第三个模型（如CatBoost）
- 使用Stacking而非简单平均
- 动态调整集成权重

### 3. 数据增强
- 使用滑动窗口生成更多样本
- 添加噪声数据增强
- 使用迁移学习

### 4. 模型架构改进
- 尝试神经网络（MLP）
- 使用Transformer
- 添加注意力机制

---

## 结论

通过这些优化，预期能达到以下效果：

1. **降低过拟合**: 训练IC从0.99降到0.3-0.6
2. **提高泛化**: 验证IC从0.04提高到0.15-0.40
3. **更稳定**: CV分数更一致
4. **更可靠**: 过拟合比率控制在合理范围

**下一步**: 运行优化训练，检查验证报告，根据指标进一步微调。
