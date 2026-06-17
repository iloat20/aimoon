# aimoon ML 因子合成引擎设计

> 日期：2026-06-01
> 状态：设计稿

---

## 1. 目标

用 XGBoost 替代 Alpha Zoo 452 个因子的简单百分位聚合，学习因子到未来 N 日收益的非线性映射，提升评分系统的预测能力。

## 2. 设计原则

- **可选依赖**：XGBoost 为 optional，无依赖时回退到现有百分位聚合
- **不破坏现有管道**：ML 结果以 Signal 形式注入，已有评分/回测/输出模块无需修改
- **时间序列意识**：严格避免 lookahead，用 expanding window 交叉验证
- **可解释**：输出特征重要性到报告

## 3. 模块结构

`
src/aimoon/ml/
├── __init__.py           # 空
├── feature_pipeline.py   # 从 Alpha Zoo 面板提取特征矩阵
├── label_engine.py       # 生成未来 N 日收益标签
├── trainer.py            # 时间序列交叉验证训练
├── predictor.py          # 模型推理 → Signal 注入
└── models/               # 模型存储目录（.json 格式）
`

## 4. 详细设计

### 4.1 feature_pipeline.py

`python
def extract_features(
    panel: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    lookback: int = 60,
) -> pd.DataFrame:
    """从 Alpha Zoo 面板中提取截面特征矩阵。

    对每只股票，用最近 lookback 天的 Alpha Zoo 因子值构造特征。
    特征工程：
    - 每个因子在目标日期的截面值
    - 每个因子过去 N 天的滚动均值/标准差/斜率
    - 截面排名（横截面百分位）
    - 技术指标补充（波动率、换手率等）

    Returns
    -------
    pd.DataFrame
        index=股票代码, columns=特征名
    """
`

关键特征设计：

| 特征类别 | 数量 | 说明 |
|---------|------|------|
| Alpha Zoo 因子截面值 | 452 | 每个因子在目标日期的原始值 |
| Alpha Zoo 截面排名 | 452 | 横截面百分位排名 |
| 因子滚动均值(5/10/20d) | 452×3 | 因子近期趋势 |
| 因子滚动标准差(10d) | 452 | 因子波动性 |
| 技术特征 | ~20 | 波动率/换手率/市值/价格位置 |
| **总计** | **~2300** | |

### 4.2 label_engine.py

`python
def generate_labels(
    klines: dict[str, pd.DataFrame],
    target_date: pd.Timestamp,
    forward_days: int = 5,
) -> pd.Series:
    """生成未来 N 日收益标签。

    支持多种标签定义：
    - 'return': 未来 N 日简单收益率（默认）
    - 'rank':  未来 N 日截面排名（0-1）
    - 'binary': 是否跑赢中位数（1/0）

    Returns
    -------
    pd.Series
        index=股票代码, value=标签值
    """
`

### 4.3 trainer.py

`python
def train_xgb_model(
    features: pd.DataFrame,
    labels: pd.Series,
    n_splits: int = 5,
    early_stopping_rounds: int = 10,
) -> tuple[xgboost.Booster, dict]:
    """时间序列交叉验证训练 XGBoost 模型。

    使用 PurgedGroupTimeSeriesSplit（剔除 lookahead 泄漏）：
    1. 按日期排序
    2. 每次用前 70% 训练，后 30% 验证
    3. purge 窗口 = forward_days（消除标签泄漏）

    Returns
    -------
    tuple[model, feature_importance]
    """
`

超参数默认值：

| 参数 | 值 | 说明 |
|------|-----|------|
| max_depth | 6 | 树深度，控制非线性 |
| learning_rate | 0.05 | 学习率 |
| n_estimators | 500 | 树数量，early stopping 控制 |
| subsample | 0.8 | 行采样 |
| colsample_bytree | 0.5 | 列采样（高维特征防止过拟合） |
| reg_lambda | 1.0 | L2 正则 |
| objective | 'rank:ndcg' or 'reg:squarederror' | 排序任务或回归任务 |

### 4.4 predictor.py

`python
def predict_alpha_signals(
    model: xgboost.Booster,
    panel: dict[str, pd.DataFrame],
    registry: Registry,
    target_date: pd.Timestamp | None = None,
    top_k: int = 0,
) -> dict[str, list[Signal]]:
    """用训练好的 XGBoost 模型生成股票 Signal。

    当 top_k > 0 时，只对预测值最高的 top_k 只股票生成信号，
    避免给所有股票打低分信号（信号稀释）。

    Signal 生成规则：
    - 预测值 ≥ 分位数(0.8): Signal("ml_alpha_strong", ..., +4)
    - 预测值 ≥ 分位数(0.65): Signal("ml_alpha", ..., +2)
    - 预测值 ≤ 分位数(0.2): Signal("ml_alpha_bear", ..., -3)
    """
`

### 4.5 模型版本与持久化

`
.aimoon_cache/ml/
├── model_v1.json          # XGBoost 模型文件
├── feature_names.json     # 特征名列表（确保一致性）
├── meta.json              # 训练时间/数据量/IC 等信息
└── importance.png         # 特征重要性图
`

模型过期策略：
- 每次 imoon 运行时检查 .aimoon_cache/ml/meta.json
- 如果模型不存在或创建时间 > 7 天，自动触发训练
- 训练成功后在 output/ 生成特征重要性报告

## 5. 集成方案

### 5.1 screener.py 修改

在 _inject_alpha_signals 中添加 ML 分支：

`python
def _inject_alpha_signals(results, all_klines):
    panel = build_panel(all_klines)
    if panel is None:
        return results

    registry = get_default_registry()

    # 尝试 ML 路径
    ml_signals = _try_ml_prediction(panel, registry)
    if ml_signals is not None:
        # ML 路径成功：用 ML 信号替换原始 alpha 信号
        return _merge_ml_signals(results, ml_signals)

    # 回退：原始百分位路径
    alpha_signals = compute_alpha_signals(registry, panel)
    return _merge_alpha_signals(results, alpha_signals)
`

### 5.2 cli.py 修改

新增 --train-model 子命令：

`ash
aimoon train-model          # 手动触发模型训练
aimoon train-model --force  # 强制重新训练
`

main() 中自动检查模型新鲜度：

`python
if cfg.use_alpha and not cfg.demo:
    _ensure_ml_model_fresh(cfg, cache)
`

## 6. 测试策略

| 测试文件 | 覆盖 |
|---------|------|
| tests/test_ml_features.py | 特征提取形状/NaN 处理/列名一致性 |
| tests/test_ml_labels.py | 标签生成/forward_days/边缘情况 |
| tests/test_ml_trainer.py | 交叉验证/early stopping/过拟合检测 |
| tests/test_ml_predictor.py | Signal 生成规则/空输入 |
| tests/test_ml_integration.py | 端到端：特征→训练→预测→screener 注入 |

## 7. IC 提升预期

根据现有系统 IC 分析：
- 当前 Alpha Zoo 百分位聚合：IC ≈ 0.03-0.05
- ML 合成因子（WorldQuant/Two Sigma 级别）：IC 目标 0.08-0.12

ML 提升来源：
1. 非线性交互（如：高 alpha 值 + 低波动率 → 更强信号）
2. IC 加权（当前系统每个因子等权，ML 自动学习最优权重）
3. 多周期特征（因子趋势比单点值更有预测力）

## 8. 性能考虑

- **训练**：~2300 特征 × 200 股票 × 500 树 ≈ 2-5 秒（单次训练）
- **推理**：~10ms/次（模型已加载到内存）
- **缓存**：特征矩阵可缓存到 .aimoon_cache/ml/features.pkl

## 9. 降级策略

ML 路径失败时自动降级到现有百分位路径：

| 失败原因 | 降级行为 |
|---------|---------|
| XGBoost 未安装 | 静默降级，百分位路径 |
| 训练数据不足（<50 stocks） | 静默降级 |
| 模型文件损坏 | 静默降级，触发重训练 |
| 推理异常 | 静默降级，跳过 ML 信号 |

