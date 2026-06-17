# Zipline-Reloaded 集成评估

## 执行时间
2026-06-05

## 📊 Zipline-Reloaded 概述

### 核心特性
- ✅ **事件驱动回测引擎**: 模拟真实交易环境
- ✅ **Pipeline API**: 构建截面因子模型
- ✅ **多资产支持**: 股票、期货等
- ✅ **Python 3.8+ 支持**: 现代 Python 版本
- ✅ **Pandas 集成**: 与 Pandas 无缝集成
- ✅ **Machine Learning 集成**: Stefan Jansen 的 ML 交易策略

### 优势
- ✅ **成熟稳定**: 经过多年验证的回测框架
- ✅ **社区支持**: 活跃的社区维护
- ✅ **文档完善**: 详细的文档和示例
- ✅ **可扩展性**: 易于集成自定义因子和策略

---

## 🎯 集成方案

### 方案 1: 完全替换 aimoon 回测引擎（不推荐）

**原因**:
- ❌ aimoon 回测引擎已经优化（+38.99% 收益）
- ❌ 自定义功能（Rumi 策略、KRange 离场）难以迁移
- ❌ 已修复的前瞻偏差问题可能重新引入
- ❌ 需要大量重构工作

### 方案 2: 作为验证工具（推荐）

**目标**: 使用 Zipline-Reloaded 验证 aimoon 策略的有效性

**步骤**:
1. 将 aimoon 的交易信号导入 Zipline
2. 使用 Zipline 的回测引擎验证策略
3. 对比两个引擎的结果
4. 识别差异并优化

**优势**:
- ✅ 验证 aimoon 策略的有效性
- ✅ 使用成熟的回测框架
- ✅ 保留 aimoon 的自定义功能
- ✅ 识别潜在问题

### 方案 3: 混合使用（可选）

**目标**: 结合两个框架的优势

**步骤**:
1. 使用 aimoon 进行筛选和信号生成
2. 使用 Zipline 进行回测和验证
3. 结合两个框架的结果

---

## 🚀 实施方案 2: 作为验证工具

### 步骤 1: 安装 Zipline-Reloaded

```bash
pip install zipline-reloaded
```

### 步骤 2: 创建 Zipline 适配器

```python
# src/aimoon/zipline_adapter.py

import pandas as pd
import numpy as np
from zipline import run_algorithm
from zipline.api import order_target_percent, record, symbol
from zipline.finance import commission, slippage

class AimoonZiplineAdapter:
    """将 aimoon 策略适配到 Zipline 框架。"""

    def __init__(self, aimoon_signals: pd.DataFrame):
        """
        Args:
            aimoon_signals: aimoon 生成的交易信号
                columns: ['date', 'code', 'signal', 'score']
        """
        self.signals = aimoon_signals

    def initialize(self, context):
        """初始化策略。"""
        context.assets = [symbol(code) for code in self.signals['code'].unique()]
        context.lookback = 20
        context.rebalance_period = 5
        context.last_rebalance = None

        # 设置交易成本
        context.set_commission(commission.PerShare(cost=0.0003, min_trade_cost=5))
        context.set_slippage(slippage.FixedSlippage(spread=0.002))

    def handle_data(self, context, data):
        """处理每个 bar 的数据。"""
        current_date = data.current_dt

        # 检查是否需要调仓
        if context.last_rebalance is None or \
           (current_date - context.last_rebalance).days >= context.rebalance_period:
            self._rebalance(context, data, current_date)
            context.last_rebalance = current_date

    def _rebalance(self, context, data, current_date):
        """调仓逻辑。"""
        # 获取当天的信号
        day_signals = self.signals[self.signals['date'] == current_date]

        if day_signals.empty:
            return

        # 计算目标权重
        total_score = day_signals['score'].sum()
        if total_score <= 0:
            return

        # 清空现有持仓
        for asset in context.portfolio.positions:
            order_target_percent(asset, 0)

        # 按分数分配权重
        for _, row in day_signals.iterrows():
            try:
                asset = symbol(row['code'])
                weight = row['score'] / total_score
                order_target_percent(asset, weight)
            except Exception:
                continue

    def create_strategy(self):
        """创建 Zipline 策略函数。"""
        def strategy(context, data):
            self.initialize(context)
            self.handle_data(context, data)
        return strategy
```

### 步骤 3: 运行 Zipline 回测

```python
# src/aimoon/zipline_runner.py

import pandas as pd
from zipline import run_algorithm
from zipline.data import bundles

def run_aimoon_zipline_backtest(
    aimoon_signals: pd.DataFrame,
    start_date: str = '2024-01-01',
    end_date: str = '2026-06-01',
    capital_base: float = 100000.0,
) -> dict:
    """使用 Zipline 运行 aimoon 策略回测。

    Args:
        aimoon_signals: aimoon 生成的交易信号
        start_date: 回测开始日期
        end_date: 回测结束日期
        capital_base: 初始资金

    Returns:
        回测结果字典
    """
    from aimoon.zipline_adapter import AimoonZiplineAdapter

    # 创建适配器
    adapter = AimoonZiplineAdapter(aimoon_signals)
    strategy = adapter.create_strategy()

    # 运行回测
    result = run_algorithm(
        initialize=strategy,
        start=pd.Timestamp(start_date),
        end=pd.Timestamp(end_date),
        capital_base=capital_base,
        bundle='quandl',  # 或其他数据源
        trading_calendar='NYSE',
    )

    return {
        'returns': result.returns,
        'positions': result.positions,
        'transactions': result.transactions,
        'gross_leverage': result.gross_leverage,
    }
```

### 步骤 4: 对比两个引擎的结果

```python
# src/aimoon/engine_comparison.py

import pandas as pd
import numpy as np

def compare_engines(
    aimoon_result: dict,
    zipline_result: dict,
) -> dict:
    """对比 aimoon 和 Zipline 的回测结果。

    Args:
        aimoon_result: aimoon 回测结果
        zipline_result: Zipline 回测结果

    Returns:
        对比结果字典
    """
    comparison = {}

    # 收益对比
    aimoon_return = aimoon_result.get('total_return', 0)
    zipline_return = zipline_result.get('total_return', 0)
    comparison['return_diff'] = aimoon_return - zipline_return

    # 风险对比
    aimoon_drawdown = aimoon_result.get('max_drawdown', 0)
    zipline_drawdown = zipline_result.get('max_drawdown', 0)
    comparison['drawdown_diff'] = aimoon_drawdown - zipline_drawdown

    # 交易对比
    aimoon_trades = aimoon_result.get('trade_count', 0)
    zipline_trades = zipline_result.get('trade_count', 0)
    comparison['trade_count_diff'] = aimoon_trades - zipline_trades

    # 差异分析
    comparison['return_ratio'] = aimoon_return / zipline_return if zipline_return != 0 else 0
    comparison['drawdown_ratio'] = aimoon_drawdown / zipline_drawdown if zipline_drawdown != 0 else 0

    return comparison
```

---

## 📊 预期效果

### 验证策略有效性
- ✅ 使用成熟的回测框架验证 aimoon 策略
- ✅ 识别潜在的前瞻偏差问题
- ✅ 验证交易成本假设

### 识别差异
- ✅ 对比两个引擎的结果差异
- ✅ 识别潜在的改进点
- ✅ 优化交易策略

### 提升信心
- ✅ 使用多个框架验证策略
- ✅ 增强策略的可信度
- ✅ 为实盘交易提供信心

---

## 🎯 实施计划

### 短期（1-2 天）
1. ⏳ 安装 Zipline-Reloaded
2. ⏳ 创建 Zipline 适配器
3. ⏳ 运行简单的回测验证

### 中期（1 周）
1. ⏳ 实现完整的适配器
2. ⏳ 运行对比分析
3. ⏳ 识别差异并优化

### 长期（1 个月）
1. ⏳ 集成到 aimoon 工作流
2. ⏳ 自动化对比分析
3. ⏳ 持续监控和优化

---

## 💡 总结

### 推荐方案
- ✅ **方案 2**: 作为验证工具（推荐）
- ✅ **目标**: 验证 aimoon 策略的有效性
- ✅ **优势**: 保留 aimoon 的自定义功能，使用成熟的框架

### 预期效果
- ✅ **验证策略有效性**: 使用 Zipline 验证 aimoon 策略
- ✅ **识别差异**: 对比两个引擎的结果
- ✅ **提升信心**: 为实盘交易提供信心

### 实施建议
1. ⏳ 安装 Zipline-Reloaded
2. ⏳ 创建 Zipline 适配器
3. ⏳ 运行对比分析
4. ⏳ 识别差异并优化

---

**执行人**: AI 架构评估系统
**执行日期**: 2026-06-05
**执行状态**: ✅ 评估完成
**下一步**: 实施 Zipline-Reloaded 集成

Zipline-Reloaded 是一个优秀的回测框架，可以作为验证工具来增强 aimoon 策略的可信度！
