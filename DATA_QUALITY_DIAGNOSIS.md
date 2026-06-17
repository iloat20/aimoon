# 数据质量问题诊断和解决方案

**日期**: 2026-06-04
**状态**: ⚠️ **问题已识别，需要进一步解决**

---

## 📊 诊断结果

### ✅ 数据质量正常

**持仓池**: 267 只股票 ✓
**行情数据**: 173 只股票 ✓
**K 线数据**: 160 天 ✓
**价格数据**: 正常 ✓
**涨跌数据**: 正常 ✓

---

### ⚠️ 发现的问题

#### 问题 1: K 线数据日期格式

**现象**：
```
日期类型: <class 'int'>
日期示例: [0, 1, 2]
```

**原因**：
- K 线数据的 index 是整数（0, 1, 2, ...）
- 有一个 `date` 列包含真正的日期
- 回测引擎期望日期对象，但收到整数

**影响**：
- 回测引擎无法正确处理日期
- 入场条件无法满足
- 无法产生交易

---

#### 问题 2: 数据缓存问题

**现象**：
- 尝试修复日期格式
- 但回测仍然使用旧的缓存数据

**原因**：
- 缓存中的数据仍然是整数日期
- 需要清除缓存并重新获取

---

## 🔍 问题根因分析

### 根本原因

1. **数据获取逻辑问题**
   - K 线数据返回时，index 是整数而不是日期
   - `date` 列存在但没有被用作 index

2. **缓存机制问题**
   - 缓存保存的是原始数据（整数日期）
   - 修复后的数据没有被正确缓存

3. **回测引擎假设**
   - 回测引擎假设 index 是日期对象
   - 实际收到的是整数

---

## 💡 解决方案

### 方案 1: 修改数据获取逻辑（推荐）

**修改文件**: `src/aimoon/data/history.py`

**修改内容**:
```python
def get_kline(code: str, days: int, cache: DataCache) -> Result[pd.DataFrame, str]:
    """获取历史 K 线"""

    # 检查缓存
    cached = cache.get(code)
    if cached is not None:
        # 确保日期格式正确
        if 'date' in cached.columns:
            cached['date'] = pd.to_datetime(cached['date'])
            cached = cached.set_index('date')
        return Ok(cached)

    # 获取数据
    # ... 现有逻辑 ...

    # 保存前修复日期格式
    if 'date' in kline.columns:
        kline['date'] = pd.to_datetime(kline['date'])
        kline = kline.set_index('date')

    # 保存到缓存
    cache.put(code, kline)

    return Ok(kline)
```

---

### 方案 2: 清除缓存并重新获取

**步骤**:
```bash
# 1. 清除缓存
python -m aimoon cache clear

# 2. 重新获取数据
python scripts/fix_data_fetching.py

# 3. 重新运行回测
python scripts/optimized_hybrid_backtest.py
```

---

### 方案 3: 修改回测引擎

**修改文件**: `src/aimoon/enhanced_backtest.py`

**修改内容**:
```python
def run_portfolio(self, klines, names, ctx=None):
    """运行回测"""

    # 修复日期格式
    for code, kline in klines.items():
        if 'date' in kline.columns:
            kline['date'] = pd.to_datetime(kline['date'])
            kline = kline.set_index('date')
            klines[code] = kline

    # ... 现有逻辑 ...
```

---

## 🚀 实施步骤

### 步骤 1: 修改数据获取逻辑

```bash
# 编辑 src/aimoon/data/history.py
# 在 get_kline 函数中添加日期修复逻辑
```

### 步骤 2: 清除缓存

```bash
python -m aimoon cache clear
```

### 步骤 3: 重新获取数据

```bash
python scripts/fix_data_fetching.py
```

### 步骤 4: 验证修复

```bash
python scripts/debug_backtest.py
```

### 步骤 5: 运行回测

```bash
python scripts/optimized_hybrid_backtest.py
```

---

## 📊 预期结果

### 修复后

**K 线数据**:
```
日期类型: <class 'pandas.Timestamp'>
日期范围: 2025-09-29 - 2026-06-03
数据量: 160 天
```

**回测结果**:
```
总收益: X%
交易次数: Y
胜率: Z%
```

---

## 💡 最佳实践

### 1. 数据验证

```python
def validate_kline(kline: pd.DataFrame) -> bool:
    """验证 K 线数据"""
    # 检查日期格式
    if not isinstance(kline.index, pd.DatetimeIndex):
        return False

    # 检查数据完整性
    if len(kline) < 60:
        return False

    # 检查价格数据
    if 'close' not in kline.columns:
        return False

    return True
```

### 2. 缓存管理

```python
def get_kline_with_validation(code, days, cache):
    """获取并验证 K 线数据"""
    r = get_kline(code, days, cache)
    if r.is_ok():
        kline = r.unwrap()
        if validate_kline(kline):
            return r
        else:
            # 清除无效缓存
            cache.delete(code)
            # 重新获取
            return get_kline(code, days, cache)
    return r
```

### 3. 回测引擎健壮性

```python
def run_portfolio(self, klines, names):
    """运行回测（带数据验证）"""
    # 验证所有 K 线数据
    valid_klines = {}
    for code, kline in klines.items():
        if validate_kline(kline):
            valid_klines[code] = kline
        else:
            logger.warning(f"Invalid kline data for {code}")

    if not valid_klines:
        logger.error("No valid kline data")
        return self._empty_result()

    # 使用有效数据运行回测
    return self._run_backtest(valid_klines, names)
```

---

## 📚 相关文档

- `BACKTEST_STATUS_SUMMARY.md` - 回测状态总结
- `HYBRID_SCORING_GUIDE.md` - 混合评分指南
- `LONG_TERM_PLAN_SUMMARY.md` - 长期计划总结
- `scripts/diagnose_data.py` - 数据诊断脚本
- `scripts/debug_backtest.py` - 回测调试脚本
- `scripts/fix_data_fetching.py` - 数据修复脚本

---

## 💡 总结

### 问题识别 ✅

1. **K 线数据日期格式问题** - index 是整数而不是日期
2. **缓存机制问题** - 修复后的数据没有被正确缓存
3. **回测引擎假设** - 期望日期对象，收到整数

### 解决方案 ✅

1. **修改数据获取逻辑** - 在 get_kline 中添加日期修复
2. **清除缓存并重新获取** - 确保使用正确的数据
3. **修改回测引擎** - 添加数据验证

### 下一步行动

1. ⏳ 修改 `src/aimoon/data/history.py`
2. ⏳ 清除缓存
3. ⏳ 重新获取数据
4. ⏳ 运行回测验证

---

**状态**: ⚠️ **问题已识别，需要实施解决方案**
**维护者**: Claude Code AI Assistant
**日期**: 2026-06-04

数据质量问题已诊断清楚，需要修改数据获取逻辑来修复日期格式问题。实施解决方案后，回测应该能正常产生交易。
