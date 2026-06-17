# aimoon 项目记忆

## 项目概述
A股量化筛选与交易建议系统 — 452 Alpha Zoo 因子 + ML 集成 + 自学习权重 + 交易策略引擎

## 技术栈
- **Python**: 3.12+（使用现代 union 类型语法 `X | None`）
- **包管理**: uv (uv run, uv python pin 3.12)
- **构建**: setuptools, src layout
- **依赖**: pandas, numpy, xgboost, lightgbm, scikit-learn, scipy, akshare, mootdx, rich, loguru

## 项目结构
```
src/aimoon/
├── cli.py          # CLI 入口 (argparse, 10+ 子命令)
├── config.py       # frozen dataclass 配置
├── models.py       # Signal, ScoredStock
├── screener.py     # 并发评分引擎
├── data/           # 三级数据源兜底 (mootdx→Tencent→AKShare)
├── factors/        # 452 Alpha Zoo 因子 + 16 基础算子
├── ml/             # XGBoost/LightGBM 集成 + ICIR + 衰减检测
├── scoring/        # 12 类技术指标信号
├── enhanced_backtest/ # 事件驱动回测引擎
└── risk.py         # 风控模型
```

## 代码规范
- `from __future__ import annotations` 在所有文件顶部
- frozen dataclass 用于配置和数据模型
- Result 类型 (Ok/Err) 用于数据获取层
- 日志级别: WARNING(默认) / DEBUG(详细)
- `except Exception` 而非 `except (Exception)`
- 使用 `warnings.catch_warnings()` 抑制已知第三方库警告

## 常见运行时警告
- `scipy.stats.ConstantInputWarning`: spearmanr 常量输入 → 需用 catch_warnings 抑制
- `pandas dateutil fallback`: pd.read_json 无格式参数 → 需抑制或添加 dtype
- `pkg_resources DeprecationWarning`: py_mini_racer 依赖 → 已在 cli.py 顶部 suppress

## 回测关键参数
- backtest_start_date: "2026-05-13"
- 默认: hold_days=10, max_positions=5, stop_loss=5%, take_profit=20%
- 禁止 lookahead: 信号在 T-1 生成，在 T open 执行

## 回测股票池约束（重要）
- **回测必须使用机构持仓池（北向+基金+ROE）的股票**
- `_load_screening_data()` 强制持仓池模式，不回退全市场
- 持仓池为空或过滤后 < 5 只 → 直接退出，不降级
- ML 模型訓練可使用更广数据源，不受此约束
- Demo 模式 (--demo) 可绕过网络/持仓池要求

## 评分排序
- 不使用 `sorted(key=lambda s: hybrid_score(...))` — 已预计算为 `s.total_score`

## 因子计算优化（452 → ~120）
- 筛查: `_inject_alpha_signals` 集成质量过滤 + 并行计算
- 回测: `ml_integration.py` 使用 `get_or_compute_filtered_ids` 结果，非全量 registry
- 质量过滤三网关: ICIR(信息系数比) + Turnover(周转率) + Correlation(去冗余)
- 质量白名单缓存: `.aimoon_cache/factor_quality/filtered_factor_ids.json`, 30天 TTL
- 因子上限: 回测 120, 筛查 100(保底)
