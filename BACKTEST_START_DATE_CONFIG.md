# Aimoon 配置说明

## 默认起始日期配置

系统已配置为默认从 **2024年2月5日** 开始进行训练和回测。

### 配置详情

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `backtest_start_date` | `2024-02-05` | 回测和训练的默认起始日期 |
| `history_days` | `850` | 历史数据天数（覆盖2024/02至今，约600个交易日） |

### 使用方式

#### 1. 默认使用（自动）

所有回测和训练都会默认从2024年2月5日开始：

```bash
# 回测 - 自动从2024-02-05开始
aimoon backtest

# 训练模型 - 使用2024年2月以来的数据
aimoon train-model

# 完整筛选 - 使用历史数据进行分析
aimoon
```

#### 2. 自定义起始日期

可以通过命令行参数覆盖默认日期：

```bash
# 指定回测起始日期
aimoon backtest --backtest-start-date 2024-06-01

# 指定训练起始日期（通过配置文件）
aimoon --config my_config.yaml train-model
```

#### 3. 配置文件

创建 `my_config.yaml` 文件：

```yaml
# 自定义起始日期
backtest_start_date: "2024-06-01"

# 其他配置
history_days: 850
hold_days: 10
max_positions: 5
stop_loss_pct: 0.04
take_profit_pct: 0.15
```

然后使用：
```bash
aimoon --config my_config.yaml backtest
```

### 配置文件位置

- **默认配置**: `src/aimoon/config.py` - Config类定义
- **CLI入口**: `src/aimoon/cli.py` - 命令行参数处理
- **回测引擎**: `src/aimoon/enhanced_backtest.py` - EnhancedBacktestEngine
- **训练模块**: `src/aimoon/ml/trainer.py` - ML模型训练

### 验证起始日期

运行回测后，检查报告中的参数：

```
## 一、回测参数

| 参数 | 值 | 说明 |
|------|----|------|
| history_days | 850 | 历史数据天数 |
| backtest_start_date | 2024-02-05 | 回测起始日期 |
```

### 注意事项

1. **历史数据覆盖**: 850天 ≈ 600个交易日，覆盖2024年2月至今
2. **训练数据**: ML模型使用这850天的历史数据进行训练
3. **回测区间**: 回测从2024-02-05开始，到当前日期结束
4. **增量学习**: 如果模型存在，会进行增量学习（warm_start）

### 修改默认起始日期

如需更改默认起始日期，修改 `src/aimoon/config.py`：

```python
@dataclass(frozen=True)
class Config:
    # ... 其他配置 ...
    
    # Default start date for training and backtesting (YYYY-MM-DD)
    backtest_start_date: str = "2024-02-05"  # 修改这里
```

### 快速验证

```bash
# 验证配置生效
aimoon backtest --backtest-start-date 2024-02-05

# 查看生成的报告
ls -lt output/backtest_report_*.md | head -5
cat output/backtest_report_<latest>.md | grep "backtest_start_date"
```

---

**配置生效时间**: 2026-06-02
**默认起始日期**: 2024-02-05
**用途**: 训练、回测、完整筛选
