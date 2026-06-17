# Factor Research Optimization Design

## Overview

优化 aimoon 项目的因子研究与选股信号系统，提升因子有效性、信号稳健性、风险控制能力。

**Current State:**
- 462 个 Alpha Zoo 因子，但部分因子衰减严重
- 无基本面、另类数据因子
- 无行业市值中性化
- 无 A 股事件处理（业绩预告、限售解禁、大股东减持）
- 无分层回测验证

**Target State:**
- 有效因子筛选（ICIR ≥ 0.3）
- 新增基本面、北向资金、龙虎榜、筹码分布因子
- 行业市值中性化处理
- 信号生成避免偷价
- A 股事件风险规避
- 分层回测验证 Top/Bottom 组合收益区分度

## Implementation Plan

### 1. Factor Effectiveness Testing (`factors/evaluator.py`)

**New module** for factor quality assessment:

```python
# 因子有效性检验
def compute_factor_ic(factor_values, forward_returns):
    """计算因子 IC (Information Coefficient)"""
    # Spearman rank correlation per date
    ...

def compute_factor_icir(ic_series):
    """计算因子 ICIR (IC Information Ratio)"""
    return mean(IC) / std(IC)

def compute_factor_turnover(factor_values, dates):
    """计算因子换手率 (rank quintile change ratio)"""
    ...

def compute_factor_decay(ic_series, window=20):
    """CUSUM 因子衰减检测"""
    ...

def filter_by_effectiveness(factors, min_icir=0.3, max_decay=0.5):
    """筛选有效因子"""
    ...
```

### 2. New Factor Definitions (`factors/zoo/`)

#### Fundamental Factors (`factors/zoo/fundamental/`)

| Factor | Data Source | Logic |
|--------|-------------|-------|
| `ep_ttm` | AKShare `stock_financial_abstract` | 净利润 TTM / 总市值 |
| `bp` | AKShare `stock_financial_abstract` | 净资产 / 总市值 |
| `roe_ttm` | AKShare `stock_financial_abstract` | 净利润 TTM / 净资产 |
| `revenue_growth` | AKShare `stock_financial_abstract` | 营收同比增长率 |
| `net_profit_growth` | AKShare `stock_financial_abstract` | 净利润同比增长率 |

#### Northbound Factors (`factors/zoo/northbound/`)

| Factor | Data Source | Logic |
|--------|-------------|-------|
| `north_flow_20d` | AKShare `stock_hsgt_north_net_flow` | 20 日北向净流入 |
| `north_pct_change` | AKShare `stock_hsgt_hold_stock_em` | 北向持股比例变化 |
| `north_concentration` | AKShare `stock_hsgt_hold_stock_em` | 北向持股集中度 |

#### Dragon Tiger Factors (`factors/zoo/dragon_tiger/`)

| Factor | Data Source | Logic |
|--------|-------------|-------|
| `dt_net_buy` | AKShare `stock_lhb_detail_em` | 龙虎榜净买入额 |
| `dt_buy_ratio` | AKShare `stock_lhb_detail_em` | 买入占比 |
| `dt_inst_activity` | AKShare `stock_lhb_detail_em` | 机构席位活跃度 |

#### Chip Distribution Factors (`factors/zoo/chip/`)

| Factor | Data Source | Logic |
|--------|-------------|-------|
| `chip_concentration` | AKShare `stock_cyq_em` | 筹码集中度 (90% 成本区间) |
| `avg_cost_ratio` | AKShare `stock_cyq_em` | 当前价 / 平均成本 |
| `profit_ratio` | AKShare `stock_cyq_em` | 获利盘比例 |

### 3. Neutralization (`factors/neutralizer.py`)

**New module** for factor neutralization:

```python
# 行业市值中性化
def neutralize_industry_size(factor_values, industry_map, market_cap):
    """OLS 回归去除行业 + 市值影响"""
    X = industry_dummies + log(market_cap)
    residual = factor_values - X @ beta
    return residual

def neutralize_market_cap(factor_values, market_cap):
    """市值中性化"""
    ...

def cross_sectional_zscore(values, method='robust'):
    """截面标准化 (median/MAD)"""
    ...

def batch_neutralize(factors, industry_map, market_cap):
    """批量中性化处理"""
    ...
```

### 4. Composite Scoring (`factors/composer.py`)

**New module** for composite factor construction:

```python
# 复合因子合成
def ic_weighted_score(factors, ic_values):
    """IC 加权复合因子"""
    weights = ic_values / ic_values.sum()
    return (factors * weights).sum(axis=1)

def equal_weight_score(factors):
    """等权复合因子"""
    return factors.mean(axis=1)

def factor_momentum_score(factors, ic_history):
    """因子动量加权"""
    ...
```

### 5. Signal Anti-Lookahead (`scoring/anti_lookahead.py`)

**New module** for signal timing validation:

```python
# 信号防偷价
def check_signal_timing(signal_date, data_date):
    """验证信号只使用 T-1 数据"""
    if signal_date <= data_date:
        raise LookaheadError("Signal uses future data!")

def shift_signals(signals, n_days=1):
    """信号延迟 N 天"""
    return signals.shift(n_days)

def validate_no_future_data(features, target_date):
    """运行时检查未来数据泄露"""
    ...
```

### 6. A-Share Event Handling (`data/events.py`)

**New module** for A-share specific events:

```python
# A 股事件处理
def get_earnings_dates(stock_code):
    """获取业绩预告/快报日期"""
    ...

def get_lockup_expiry(stock_code):
    """获取限售股解禁日期"""
    ...

def get_shareholder_reduction(stock_code):
    """获取大股东减持计划"""
    ...

def filter_event_risk(stocks, date, lookahead_days=5):
    """排除事件风险股"""
    ...
```

### 7. Layered Backtest (`backtest/layered.py`)

**New module** for layered backtesting:

```python
# 分层回测
def run_layered_backtest(factor_values, klines, n_layers=5, hold_days=5):
    """分层回测"""
    layers = {}
    for i in range(n_layers):
        layer_stocks = get_layer_stocks(factor_values, i, n_layers)
        layer_returns = compute_layer_returns(layer_stocks, klines, hold_days)
        layers[i] = layer_returns
    return layers

def compute_long_short(layers):
    """多空组合 (Top - Bottom)"""
    return layers[0] - layers[-1]

def display_layered_results(results):
    """展示分层结果"""
    ...
```

## Implementation Order

1. **Factor effectiveness testing** — `factors/evaluator.py`
2. **New factor definitions** — `factors/zoo/fundamental/`, `northbound/`, `dragon_tiger/`, `chip/`
3. **Neutralization** — `factors/neutralizer.py`
4. **Composite scoring** — `factors/composer.py`
5. **Signal anti-lookahead** — `scoring/anti_lookahead.py`
6. **A-share event handling** — `data/events.py`
7. **Layered backtest** — `backtest/layered.py`

## Testing Strategy

- **Unit tests**: Each new module gets unit tests
- **Integration tests**: Verify factor computation pipeline end-to-end
- **Regression tests**: Ensure existing functionality unchanged
- **Performance benchmarks**: Factor computation time

## Risks

1. **AKShare data availability**: Some APIs may be rate-limited or unavailable
   - Mitigation: Caching with 24h TTL

2. **Fundamental data delay**: Financial statements have reporting lag
   - Mitigation: Use trailing twelve months (TTM) data

3. **Factor correlation**: New factors may be highly correlated with existing ones
   - Mitigation: Correlation filter in factor quality pipeline

## Success Metrics

- Factor ICIR: >0.3 for selected factors
- Top-Bottom spread: >5% annualized
- Event risk reduction: <2% drawdown from event-driven losses
- Signal timing: Zero lookahead bias violations
