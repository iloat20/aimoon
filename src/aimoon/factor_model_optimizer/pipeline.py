"""流水线入口 — 串联因子筛选、联合优化、回测、报告全流程。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from aimoon.factor_model_optimizer.backtest import BacktestEngine
from aimoon.factor_model_optimizer.config import OptimizerConfig
from aimoon.factor_model_optimizer.data_loader import load_ohlcv_csv
from aimoon.factor_model_optimizer.factor_engine import (
    compute_all_factors,
)
from aimoon.factor_model_optimizer.factor_selector import (
    compute_ic_stats,
    generate_factor_report,
    remove_correlated_factors,
)
from aimoon.factor_model_optimizer.joint_optimizer import JointOptimizer
from aimoon.factor_model_optimizer.reporter import Reporter

logger = logging.getLogger(__name__)


def run_pipeline(
    csv_path: str | Path,
    config: OptimizerConfig | None = None,
) -> dict[str, object]:
    """运行完整的因子-模型联合优化流水线。

    Parameters
    ----------
    csv_path : str | Path
        OHLCV CSV 文件路径。
    config : OptimizerConfig, optional
        配置对象。如果为 None 使用默认值。

    Returns
    -------
    dict
        包含全部结果的字典。
    """
    cfg = config or OptimizerConfig()
    t_start = time.time()

    # ── 1. 加载数据 ────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 1: Loading data")
    panel = load_ohlcv_csv(csv_path)

    close = panel["close"]
    all_dates = sorted(close.index.unique())
    n_dates = len(all_dates)
    train_end = int(n_dates * cfg.train_ratio)
    val_end = int(n_dates * (cfg.train_ratio + cfg.val_ratio))

    train_dates = all_dates[:train_end]
    val_dates = all_dates[train_end:val_end]
    test_dates = all_dates[val_end:]

    logger.info(
        "Split: train=%d, val=%d, test=%d dates",
        len(train_dates),
        len(val_dates),
        len(test_dates),
    )

    # 面板按时间切分
    train_panel = {k: v.loc[v.index.isin(train_dates)] for k, v in panel.items()}
    val_panel = {k: v.loc[v.index.isin(val_dates)] for k, v in panel.items()}
    test_panel = {k: v.loc[v.index.isin(test_dates)] for k, v in panel.items()}

    # ── 2. 因子生成 ────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 2: Computing factors on training set")
    factor_df, factor_defs = compute_all_factors(train_panel, cfg)

    if factor_df.empty:
        raise ValueError("No factors computed. Check input data.")

    factor_cols = [c for c in factor_df.columns if c not in ("date", "symbol")]
    logger.info("Generated %d factor columns", len(factor_cols))

    # ── 3. 因子筛选 ────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 3: Factor selection (IC/ICIR filter + correlation dedup)")

    # 计算训练集前向收益率
    train_fwd_ret = close.loc[close.index.isin(train_dates)].shift(-cfg.forward_days_options[0])
    train_fwd_ret = train_fwd_ret / close.loc[close.index.isin(train_dates)] - 1.0

    # 计算 IC
    from aimoon.factor_model_optimizer.factor_selector import compute_rank_ic

    ic_df = compute_rank_ic(
        factor_df,
        train_fwd_ret,
        factor_cols,
    )

    if ic_df.empty:
        raise ValueError("IC computation returned empty. Check data alignment.")

    # IC/ICIR 筛选
    stats_df, filtered_ic_df = compute_ic_stats(
        ic_df,
        min_abs_ic=cfg.min_abs_ic,
        min_icir=cfg.min_icir,
    )

    # 相关性去重
    selected_factors = remove_correlated_factors(
        filtered_ic_df,
        max_corr=cfg.max_factor_corr,
    )

    logger.info("Selected %d factors after filtering", len(selected_factors))

    # 因子报告
    report_dir = cfg.output_path
    report_dir.mkdir(parents=True, exist_ok=True)
    generate_factor_report(stats_df, ic_df, selected_factors, str(report_dir))

    # ── 4. 联合优化 ────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 4: Joint optimization with Optuna")

    optimizer = JointOptimizer(cfg)
    opt_result = optimizer.optimize(train_panel)

    logger.info(
        "Optimization result: val_sharpe=%.4f, forward_days=%d, trials=%d",
        opt_result.best_val_sharpe,
        opt_result.forward_days,
        opt_result.n_trials,
    )

    # ── 5. 测试集回测 ──────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 5: Backtest on test set")

    # 在测试集上计算因子
    test_factor_df, _ = compute_all_factors(test_panel, cfg)
    test_fwd_ret = close.loc[close.index.isin(test_dates)].shift(-opt_result.forward_days)
    test_fwd_ret = test_fwd_ret / close.loc[close.index.isin(test_dates)] - 1.0

    # 用最优参数训练最终模型（训练+验证集合并）
    combined_panel = {
        k: pd.concat([train_panel[k], val_panel[k]]).sort_index() for k in train_panel
    }
    combined_factor_df, _ = compute_all_factors(combined_panel, cfg)

    from aimoon.factor_model_optimizer.joint_optimizer import _build_lgbm

    # 准备训练数据
    combined_fwd = close.shift(-opt_result.forward_days) / close - 1.0
    ret_long = combined_fwd.stack(dropna=False).reset_index()
    ret_long.columns = ["date", "symbol", "target"]
    merged = combined_factor_df.merge(ret_long, on=["date", "symbol"], how="inner")
    merged = merged.dropna(subset=["target"])

    feature_cols = [c for c in merged.columns if c not in ("date", "symbol", "target")]
    X_train = merged[feature_cols].replace([np.inf, -np.inf], np.nan)
    y_train = merged["target"]

    final_model = _build_lgbm(opt_result.best_params)
    final_model.fit(
        X_train,
        y_train,
        callbacks=[lgb.early_stopping(20, verbose=False)],
    )

    # 在测试集上预测
    test_ret_long = test_fwd_ret.stack(dropna=False).reset_index()
    test_ret_long.columns = ["date", "symbol", "target"]
    test_merged = test_factor_df.merge(test_ret_long, on=["date", "symbol"], how="inner")
    test_merged = test_merged.dropna(subset=["target"])

    test_feature_cols = [c for c in feature_cols if c in test_merged.columns]
    X_test = test_merged[test_feature_cols].replace([np.inf, -np.inf], np.nan)
    test_pred = final_model.predict(X_test)

    # 构建预测 DataFrame (date x symbol)
    test_merged["prediction"] = test_pred
    predictions = test_merged.pivot_table(
        index="date",
        columns="symbol",
        values="prediction",
        aggfunc="first",
    )

    # 回测
    bt_engine = BacktestEngine(
        top_quantile=cfg.top_quantile,
        bottom_quantile=cfg.bottom_quantile,
        transaction_cost_bps=cfg.transaction_cost_bps,
        rebalance_freq=cfg.rebalance_freq,
    )

    # 对齐预测和前向收益率
    aligned_fwd = test_fwd_ret.loc[predictions.index]
    for col in predictions.columns:
        if col not in aligned_fwd.columns:
            aligned_fwd[col] = np.nan

    bt_result = bt_engine.run(predictions, aligned_fwd, close)

    logger.info(
        "Backtest: ann_return=%.2f%%, sharpe=%.4f, max_dd=%.2f%%, calmar=%.4f",
        bt_result.metrics.annual_return * 100,
        bt_result.metrics.sharpe_ratio,
        bt_result.metrics.max_drawdown * 100,
        bt_result.metrics.calmar_ratio,
    )

    # ── 6. 生成报告 ──────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 6: Generating reports")

    # 因子重要性
    importance = final_model.feature_importances_
    total = importance.sum()
    factor_importance = {}
    if total > 0:
        for name, imp in zip(test_feature_cols, importance):
            factor_importance[name] = float(imp) / total

    reporter = Reporter(str(report_dir))
    reporter.generate_all(
        backtest_result=bt_result,
        ic_df=ic_df,
        factor_importance=factor_importance,
        optimization_result=opt_result,
        factor_df=factor_df,
        forward_returns=train_fwd_ret,
    )

    elapsed = time.time() - t_start
    logger.info("=" * 60)
    logger.info("Pipeline complete in %.1fs", elapsed)
    logger.info("Results saved to %s", report_dir)

    return {
        "config": cfg,
        "panel": panel,
        "factor_df": factor_df,
        "selected_factors": selected_factors,
        "optimization_result": opt_result,
        "backtest_result": bt_result,
        "factor_importance": factor_importance,
        "final_model": final_model,
    }


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Factor-Model Joint Optimizer")
    parser.add_argument("--csv", required=True, help="Path to OHLCV CSV file")
    parser.add_argument("--output-dir", default="output", help="Output directory")
    parser.add_argument("--trials", type=int, default=100, help="Number of Optuna trials")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    cfg = OptimizerConfig(
        output_dir=args.output_dir,
        n_optuna_trials=args.trials,
        random_seed=args.seed,
    )

    results = run_pipeline(args.csv, cfg)
    print(f"Sharpe: {results.backtest_result.metrics.sharpe_ratio:.4f}")
