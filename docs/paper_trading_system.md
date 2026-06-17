# 实盘模拟系统 - Paper Trading System

**生成时间**: 2026-06-02

---

## 一、系统概述

### 1.1 什么是Paper Trading？

Paper Trading（实盘模拟）是一种**无风险的交易测试系统**，用于：
- ✅ 验证交易策略在实时市场中的表现
- ✅ 测试风控规则是否有效
- ✅ 培养交易纪律和执行能力
- ✅ 优化参数设置和仓位管理

### 1.2 系统特点

| 特点 | 说明 |
|------|------|
| **无风险** | 不使用真实资金，完全模拟 |
| **实时数据** | 接入真实市场数据 |
| **真实成本** | 包含手续费、滑点、印花税 |
| **持久化** | 自动保存交易记录和持仓 |
| **完整风控** | 优化后的止损/止盈/追踪策略 |

---

## 二、系统架构

### 2.1 核心组件

```
PaperTradingEngine
├── 账户管理
│   ├── 初始资金管理
│   ├── 现金余额追踪
│   └── 仓位价值计算
├── 交易执行
│   ├── 开仓逻辑
│   ├── 平仓逻辑
│   └── 成本计算
├── 风控系统
│   ├── 止损检查
│   ├── 止盈检查
│   ├── 追踪止损
│   ├── 利润保护
│   └── Regime自适应
└── 数据持久化
    ├── 持仓状态保存
    ├── 交易记录保存
    └── 组合快照保存
```

### 2.2 数据存储

```
paper_trading/
├── portfolio.json      # 当前持仓和现金
├── trades.json         # 所有交易记录
├── snapshots.json      # 组合快照历史
└── demo_report.txt     # 演示报告
```

---

## 三、优化后的策略特性

### 3.1 入场规则

| 规则 | 说明 | 阈值 |
|------|------|------|
| **评分门槛** | 仅接受高评分股票 | ≥55分 |
| **最大仓位** | 单只股票不超过组合的 | 20% |
| **持仓上限** | 同时持有的股票数量 | 5只 |
| **行业分散** | 单行业不超过组合的 | 25% |

### 3.2 退出规则

#### 止损机制
```
止损 = max(基础止损, ATR止损, 追踪止损)

基础止损: 6%
ATR止损: 2.0倍ATR, 范围[4%, 8%]
追踪止损:
  - +2%: 保本（止损上移至0%）
  - +5%: 追踪峰值的50%
  - +10%: 追踪峰值的40%
```

#### 止盈机制
```
止盈: 15%（固定）
利润保护: 峰值≥5%且回落≤1%时退出
```

#### 动量退出
```
退出阈值: 33分（入场阈值的60%）
持仓期: 最长20天
动量延期: 评分维持≥80%入场评分且收益>0时可延期
```

### 3.3 仓位管理

```
仓位分配: 基于分数比例
  - 高分股（≥70分）: 获得更大仓位
  - 中等股（50-70分）: 标准仓位
  - 低分股（<50分）: 获得更小仓位

Regime适应:
  - 牛市: 降低入场门槛
  - 横盘: 标准门槛
  - 高波动: 提高门槛，减少仓位
  - 熊市: 大幅提高门槛，最小仓位
```

---

## 四、使用方法

### 4.1 快速开始

```bash
# 1. 运行演示
python -m aimoon.paper_trading_example

# 2. 查看持仓
python -m aimoon.paper_trading_demo --positions

# 3. 生成报告
python -m aimoon.paper_trading_demo --report

# 4. 清空重新开始
python -m aimoon.paper_trading_demo --reset
```

### 4.2 完整演示流程

```bash
# 步骤1: 运行初始演示（建立初始持仓）
python -m aimoon.paper_trading_example

# 步骤2: 查看当前持仓
python -m aimoon.paper_trading_demo --positions

# 步骤3: 每日更新（模拟第二天）
python -m aimoon.paper_trading_demo --update

# 步骤4: 查看绩效报告
python -m aimoon.paper_trading_demo --report
```

### 4.3 自定义参数

```python
from aimoon.paper_trading import PaperTradingEngine

engine = PaperTradingEngine(
    initial_capital=500_000,  # 50万初始资金
    max_positions=3,  # 最多3只股票
    entry_threshold=60,  # 入场阈值60分
    stop_loss_pct=0.05,  # 5%止损
    take_profit_pct=0.20,  # 20%止盈
    # ... 其他参数
)
```

---

## 五、交易示例

### 5.1 开仓示例

```python
# 开仓603198（迎驾贡酒）
position = engine.open_position(
    code="603198",
    name="迎驾贡酒",
    price=37.55,
    score=84.0,
    sector="白酒",
    stop_loss_pct=0.06,
    take_profit_pct=0.15,
)

# 输出
# ✅ 开仓成功
#    价格: ¥37.55
#    数量: 5300股
#    成本: ¥199,273.72
```

### 5.2 检查退出条件

```python
# 检查当前价格是否触发退出
exit_reason = engine.check_exit_conditions(
    code="603198",
    current_price=39.50,
    current_score=75.0,
)

# 可能的退出原因:
# - None: 继续持有
# - "stop_loss": 止损退出
# - "take_profit": 止盈退出
# - "profit_protection": 利润保护退出
# - "momentum_exit": 动量退出
# - "hold_period": 持仓期满
```

### 5.3 平仓示例

```python
# 止盈退出
trade = engine.close_position(
    code="603198",
    price=39.50,
    exit_reason="take_profit",
)

# 输出
# ✅ 交易完成
#    入场价: ¥37.55
#    出场价: ¥39.50
#    收益: +4.87%
#    退出原因: take_profit
```

---

## 六、风险控制

### 6.1 内置风控规则

| 风控规则 | 触发条件 | 处理方式 |
|---------|---------|---------|
| **止损** | 亏损≥6% | 自动平仓 |
| **止盈** | 盈利≥15% | 自动平仓 |
| **追踪止损** | 从峰值回落 | 保护利润 |
| **利润保护** | 峰值≥5%且回落≤1% | 保护利润 |
| **动量退出** | 评分<33分 | 考虑平仓 |
| **持仓期** | 超过20天 | 评估是否延期 |
| **行业集中** | 单行业>25% | 拒绝新开仓 |

### 6.2 风控参数

```python
# 风控参数示例
engine = PaperTradingEngine(
    stop_loss_pct=0.06,  # 6%止损
    take_profit_pct=0.15,  # 15%止盈
    trailing_stop_start=0.05,  # +5%开始追踪
    trailing_stop_pct=0.50,  # 追踪50%峰值
    profit_protection_start=0.05,  # +5%启动保护
    profit_protection_floor=0.01,  # 回落到+1%退出
)
```

### 6.3 风控示例

```python
# 场景: 股票价格上涨5%后回落
price_history = [37.55, 38.50, 39.50, 39.00, 37.00]

# 检查退出条件
for price in price_history:
    exit_reason = engine.check_exit_conditions("603198", price)
    pnl = (price - 37.55) / 37.55 * 100
    print(f"¥{price:.2f} ({pnl:+.2f}%) -> {exit_reason or '持有'}")

# 输出:
# ¥37.55 (+0.00%) -> 持有
# ¥38.50 (+2.53%) -> 持有
# ¥39.50 (+5.19%) -> 持有 (启动追踪止损)
# ¥39.00 (+3.86%) -> profit_protection (保护利润)
# ¥37.00 (-1.46%) -> profit_protection (保护利润)
```

---

## 七、绩效评估

### 7.1 关键指标

```python
metrics = engine.get_performance_metrics(current_prices)

# 输出
{
    'total_return': 5.23,  # 总收益%
    'win_rate': 0.65,  # 胜率65%
    'profit_factor': 2.1,  # 盈亏比2.1
    'trade_count': 20,  # 交易次数
    'avg_hold_days': 8.5,  # 平均持仓天数
    'max_drawdown': 3.2,  # 最大回撤%
}
```

### 7.2 生成报告

```python
report = engine.generate_report(current_prices)
print(report)

# 输出包含:
# - 组合摘要（资金、收益、回撤）
# - 交易统计（胜率、盈亏比、持仓天数）
# - 风险指标（最大回撤）
# - 当前持仓详情
# - 最近交易记录
```

### 7.3 查看快照

```python
snapshot = engine.take_snapshot(current_prices)

# 输出
{
    'date': datetime(2026, 6, 2, 10, 30, 0),
    'cash': 500000.0,
    'positions_value': 500000.0,
    'total_value': 1000000.0,
    'positions': {
        '603198': {'shares': 5300, 'entry_price': 37.55, 'current_price': 39.50, 'pnl_pct': 5.19},
    }
}
```

---

## 八、部署建议

### 8.1 渐进式部署

| 阶段 | 时间 | 资金 | 目标 |
|------|------|------|------|
| **模拟交易** | 1个月 | - | 验证策略 |
| **小资金测试** | 1个月 | 10万 | 真实成本验证 |
| **中等资金** | 2个月 | 50万 | 扩大规模 |
| **全面部署** | 持续 | 100万+ | 稳定运行 |

### 8.2 日常操作流程

```
每日流程:
1. 09:00 - 检查市场开盘
2. 09:30 - 运行策略筛选候选股
3. 10:00 - 检查开仓信号
4. 15:00 - 检查平仓信号
5. 15:30 - 记录交易和快照
6. 16:00 - 生成日报

每周流程:
1. 周一 - 回顾上周交易
2. 周五 - 总结本周绩效
3. 周末 - 参数优化和策略调整
```

### 8.3 监控指标

| 指标 | 目标值 | 告警阈值 |
|------|--------|---------|
| 胜率 | ≥60% | <50% |
| 盈亏比 | ≥1.5 | <1.0 |
| 最大回撤 | <10% | >15% |
| 夏普比率 | >2.0 | <1.0 |
| 日均交易 | 1-2次 | >5次 |

---

## 九、故障排除

### 9.1 常见问题

**Q: 开仓失败？**
```
原因: 可能是现金不足、持仓已满、行业集中超限
解决: 检查账户状态和风控规则
```

**Q: 退出条件未触发？**
```
原因: 可能是价格未达到止损/止盈阈值
解决: 检查持仓的止损/止盈设置
```

**Q: 数据未保存？**
```
原因: 可能是文件权限或磁盘空间问题
解决: 检查paper_trading目录权限
```

### 9.2 日志查看

```bash
# 查看交易日志
cat paper_trading.log | tail -50

# 查看错误日志
grep ERROR paper_trading.log

# 查看今日交易
grep "2026-06-02" paper_trading.log
```

---

## 十、下一步

### 10.1 策略优化

- [ ] 调整入场阈值（55→50）平衡胜率和交易频率
- [ ] 优化Regime检测参数
- [ ] 测试不同的ATR乘数（1.5x, 2.0x, 2.5x）
- [ ] 验证追踪止损比例（40%, 50%, 60%）

### 10.2 功能增强

- [ ] 添加实时数据订阅
- [ ] 集成ML模型实时预测
- [ ] 添加自动交易执行
- [ ] 实现Dashboard监控界面

### 10.3 生产部署

- [ ] Walk-Forward验证
- [ ] 样本外测试
- [ ] 风控参数优化
- [ ] 实盘模拟至少1个月
- [ ] 渐进式资金扩大

---

## 十一、资源

### 11.1 相关文档

- [持仓管理优化报告](position_management_optimization_report.md)
- [回测对比分析](backtest_comparison_2024_to_2026.md)
- [系统架构文档](../README.md)

### 11.2 代码位置

```
src/aimoon/
├── paper_trading.py           # 核心引擎
├── paper_trading_demo.py      # 完整演示
└── paper_trading_example.py   # 快速示例
```

### 11.3 数据目录

```
paper_trading/
├── portfolio.json      # 持仓状态
├── trades.json         # 交易记录
├── snapshots.json      # 快照历史
└── paper_trading.log   # 运行日志
```

---

**系统就绪时间**: 2026-06-02
**状态**: ✅ 已完成，可投入使用
