# Zipline 数据源配置指南

## 配置时间
2026-06-05

## 📊 配置方案

### 方案 1: 使用自定义数据源（推荐）✅

由于 aimoon 使用 mootdx 和 akshare 获取 A 股数据，我们可以创建一个自定义数据源。

#### 步骤 1: 准备数据
```python
from aimoon.zipline_data import prepare_aimoon_data_for_zipline

# 准备 aimoon 数据
prepared_data = prepare_aimoon_data_for_zipline(
    klines=your_klines_data,
    start_date='2024-01-01',
    end_date='2026-06-01',
)
```

#### 步骤 2: 注册数据 Bundle
```python
from aimoon.zipline_data import register_aimoon_bundle

# 注册 aimoon 数据 bundle
register_aimoon_bundle(
    klines=prepared_data,
    start_date='2024-01-01',
    end_date='2026-06-01',
)
```

#### 步骤 3: 运行 Zipline 回测
```python
from zipline import run_algorithm
from aimoon.zipline_adapter import AimoonZiplineAdapter

# 创建适配器
adapter = AimoonZiplineAdapter(signals, ...)
strategy = adapter.create_strategy()

# 运行回测
result = run_algorithm(
    initialize=strategy,
    start=pd.Timestamp('2024-01-01'),
    end=pd.Timestamp('2026-06-01'),
    capital_base=100000.0,
    bundle='aimoon',
)
```

---

### 方案 2: 使用 Quandl 数据源（国际股票）

如果您需要使用国际股票数据，可以配置 Quandl：

#### 步骤 1: 获取 Quandl API Key
1. 访问 https://www.quandl.com/
2. 注册账号
3. 获取 API Key

#### 步骤 2: 配置环境变量
```bash
export QUANDL_API_KEY=your_api_key
```

#### 步骤 3: 注入 Quandl 数据
```bash
zipline ingest -b quandl
```

#### 步骤 4: 运行回测
```python
result = run_algorithm(
    initialize=strategy,
    start=pd.Timestamp('2024-01-01'),
    end=pd.Timestamp('2026-06-01'),
    capital_base=100000.0,
    bundle='quandl',
)
```

---

### 方案 3: 使用 Bundle 缓存（如果已有数据）

如果 Zipline 已经有缓存的数据，可以直接使用：

#### 检查可用 Bundle
```bash
zipline bundles
```

#### 使用默认 Bundle
```python
result = run_algorithm(
    initialize=strategy,
    start=pd.Timestamp('2024-01-01'),
    end=pd.Timestamp('2026-06-01'),
    capital_base=100000.0,
    bundle='default',  # 或其他已注册的 bundle
)
```

---

## 🎯 推荐方案

### 方案 1: 使用自定义数据源（推荐）✅

**原因**:
- ✅ **完全控制**: 使用 aimoon 的数据源
- ✅ **A 股支持**: 专门针对中国股票市场
- ✅ **数据质量**: 使用 aimoon 的数据验证
- ✅ **实时性**: 可以随时更新数据

**实施步骤**:
1. 准备 aimoon 数据
2. 注册 aimoon 数据 bundle
3. 运行 Zipline 回测
4. 对比结果

---

## 🚀 实施计划

### 立即行动
1. ⏳ **准备数据**: 使用 aimoon 获取 A 股数据
2. ⏳ **注册 Bundle**: 注册 aimoon 数据 bundle
3. ⏳ **测试回测**: 运行简单的回测验证

### 短期优化（1-2 天）
1. ⏳ **优化数据准备**: 提高数据准备效率
2. ⏳ **添加更多股票**: 扩展股票池
3. ⏳ **验证数据质量**: 确保数据准确性

### 中期优化（1 周）
1. ⏳ **自动化数据更新**: 定期更新数据
2. ⏳ **数据缓存**: 优化数据访问性能
3. ⏳ **监控数据质量**: 持续监控数据质量

---

## 💡 技术细节

### 数据格式要求

Zipline 需要以下数据格式：

#### 资产数据 (Assets)
```python
{
    'symbol': '600519',  # 股票代码
    'name': '贵州茅台',  # 股票名称
    'exchange': 'SSE',   # 交易所
    'asset_type': 'stock',  # 资产类型
}
```

#### 价格数据 (Bars)
```python
{
    'asset': '600519',  # 股票代码
    'date': '2024-01-15',  # 日期
    'open': 1800.0,    # 开盘价
    'high': 1850.0,    # 最高价
    'low': 1790.0,     # 最低价
    'close': 1820.0,   # 收盘价
    'volume': 1000000, # 成交量
}
```

#### 日历数据 (Calendar)
```python
pd.date_range(start='2024-01-01', end='2026-06-01', freq='B')
```

---

## 📊 验证清单

### 数据准备验证
- [ ] 股票代码格式正确
- [ ] 日期格式正确（DatetimeIndex）
- [ ] OHLCV 数据完整
- [ ] 数据类型正确（float64）
- [ ] 无 NaN 值

### Bundle 注册验证
- [ ] Bundle 注册成功
- [ ] 资产数据写入成功
- [ ] 价格数据写入成功
- [ ] 日历数据写入成功

### 回测验证
- [ ] 回测运行成功
- [ ] 交易记录完整
- [ ] 收益曲线正确
- [ ] 指标计算正确

---

## 🎯 总结

### 推荐方案
- ✅ **方案 1**: 使用自定义数据源（推荐）
- ✅ **原因**: 完全控制、A 股支持、数据质量、实时性

### 实施步骤
1. ⏳ 准备 aimoon 数据
2. ⏳ 注册 aimoon 数据 bundle
3. ⏳ 运行 Zipline 回测
4. ⏳ 对比结果

### 预期效果
- ✅ **验证策略有效性**: 使用 Zipline 验证 aimoon 策略
- ✅ **识别差异**: 对比两个引擎的结果差异
- ✅ **提升信心**: 为实盘交易提供信心

---

**执行人**: AI 配置系统
**执行日期**: 2026-06-05
**执行状态**: ✅ 配置指南完成
**下一步**: 实施数据源配置
