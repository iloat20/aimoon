"""优化后的训练脚本 - 解决过拟合问题

使用方法：
    python scripts/optimized_train.py
    python scripts/optimized_train.py --n-dates 150
    python scripts/optimized_train.py --force-retrain
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from aimoon.ml.optimized_config import get_lgbm_params, get_training_config, get_xgb_params

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def train_optimized(
    n_dates: int = 120,
    forward_days: int = 5,
    force_retrain: bool = False,
    warm_start: bool = True,
):
    """执行优化的训练流程。"""

    config = get_training_config({
        "n_dates": n_dates,
        "forward_days": forward_days,
    })

    logger.info("=== 优化训练配置 ===")
    logger.info(f"  n_dates: {config['n_dates']}")
    logger.info(f"  forward_days: {config['forward_days']}")
    logger.info(f"  validation_split: {config['validation_split']}")
    logger.info(f"  feature_selection_min_ic: {config['feature_selection']['min_ic']}")

    # 加载数据 - 使用demo数据或真实数据
    logger.info("\n=== 加载数据 ===")
    from aimoon.demo import generate_demo
    from aimoon.factors.panel import build_panel
    from aimoon.factors.registry import get_default_registry

    # 使用demo数据（包含50只股票，120天历史）
    spot_df, klines = generate_demo(n_stocks=50)

    if not klines:
        logger.error("无法加载K线数据")
        return None

    logger.info(f"  加载了 {len(klines)} 只股票的K线数据")

    panel = build_panel(klines, min_rows=60)
    if panel is None:
        logger.error("无法构建数据面板")
        return None

    registry = get_default_registry()

    logger.info(f"  面板大小: {panel['close'].shape[0]} 天 x {panel['close'].shape[1]} 只股票")

    # 训练XGBoost
    logger.info("\n=== 训练XGBoost ===")
    from aimoon.ml.trainer import train_model

    xgb_params = get_xgb_params()
    logger.info(f"XGBoost参数: {xgb_params}")

    xgb_result = train_model(
        panel=panel,
        klines=klines,
        registry=registry,
        params=xgb_params,
        n_dates=config["n_dates"],
        forward_days=config["forward_days"],
        save_dir=config.get("save_dir", ".aimoon_cache/ml"),
        warm_start=warm_start,
    )

    logger.info("XGBoost训练完成:")
    logger.info(f"  验证集IC: {xgb_result.ic:.4f}")
    logger.info(f"  样本数: {xgb_result.n_stocks}")
    logger.info(f"  特征数: {len(xgb_result.feature_names)}")
    logger.info(f"  训练时间: {xgb_result.train_duration:.1f}s")

    # 训练LightGBM
    logger.info("\n=== 训练LightGBM ===")
    from aimoon.ml.lgbm_trainer import train_lgbm_model

    lgbm_params = get_lgbm_params()
    logger.info(f"LightGBM参数: {lgbm_params}")

    lgbm_result = train_lgbm_model(
        panel=panel,
        klines=klines,
        registry=registry,
        params=lgbm_params,
        n_dates=config["n_dates"],
        forward_days=config["forward_days"],
        save_dir=config.get("save_dir", ".aimoon_cache/ml"),
        warm_start=warm_start,
    )

    logger.info("LightGBM训练完成:")
    logger.info(f"  验证集IC: {lgbm_result.ic:.4f}")
    logger.info(f"  样本数: {lgbm_result.n_stocks}")
    logger.info(f"  特征数: {len(lgbm_result.feature_names)}")
    logger.info(f"  训练时间: {lgbm_result.train_duration:.1f}s")

    # 计算集成权重
    logger.info("\n=== 计算集成权重 ===")
    from aimoon.ml.trainer import train_ensemble

    ensemble_result = train_ensemble(
        panel=panel,
        klines=klines,
        registry=registry,
        n_dates=config["n_dates"],
        forward_days=config["forward_days"],
        save_dir=config.get("save_dir", ".aimoon_cache/ml"),
        warm_start=warm_start,
        use_early_stop=True,
    )

    logger.info("集成模型训练完成:")
    logger.info(f"  XGBoost IC: {ensemble_result['xgb_result'].ic:.4f}")
    logger.info(f"  LightGBM IC: {ensemble_result['lgbm_result'].ic:.4f}")
    logger.info(f"  XGBoost权重: {ensemble_result['xgb_weight']:.2f}")
    logger.info(f"  LightGBM权重: {ensemble_result['lgbm_weight']:.2f}")

    # 验证结果
    logger.info("\n=== 验证结果 ===")

    # 读取meta信息
    import json
    meta_dir = Path(config.get("save_dir", ".aimoon_cache/ml"))

    xgb_meta_path = meta_dir / "meta.json"
    if xgb_meta_path.exists():
        with open(xgb_meta_path) as f:
            xgb_meta = json.load(f)

        logger.info("XGBoost训练详情:")
        logger.info(f"  训练集IC: {xgb_meta.get('ic_train', 'N/A')}")
        logger.info(f"  验证集IC: {xgb_meta.get('ic', 'N/A')}")
        logger.info(f"  过拟合比率: {xgb_meta.get('overfit_ratio', 'N/A')}")
        logger.info(f"  最佳迭代: {xgb_meta.get('best_iteration', 'N/A')}")

        if xgb_meta.get('overfit_ratio', 0) > config.get('overfit_threshold', 3.0):
            logger.warning("⚠️  XGBoost可能过拟合：训练IC远高于验证IC")

    lgbm_meta_path = meta_dir / "lgbm_meta.json"
    if lgbm_meta_path.exists():
        with open(lgbm_meta_path) as f:
            lgbm_meta = json.load(f)

        logger.info("\nLightGBM训练详情:")
        logger.info(f"  训练集IC: {lgbm_meta.get('ic_train', 'N/A')}")
        logger.info(f"  验证集IC: {lgbm_meta.get('ic', 'N/A')}")
        logger.info(f"  过拟合比率: {lgbm_meta.get('overfit_ratio', 'N/A')}")
        logger.info(f"  最佳迭代: {lgbm_meta.get('best_iteration', 'N/A')}")

        if lgbm_meta.get('overfit_ratio', 0) > config.get('overfit_threshold', 3.0):
            logger.warning("⚠️  LightGBM可能过拟合：训练IC远高于验证IC")

    logger.info("\n=== 训练完成 ===")
    logger.info("模型已保存到: " + str(meta_dir.resolve()))
    logger.info("下一步：运行回测评估模型效果")

    return ensemble_result


def main():
    parser = argparse.ArgumentParser(description="优化的ML训练脚本")
    parser.add_argument(
        "--n-dates",
        type=int,
        default=120,
        help="训练使用的日期快照数量（默认：120）"
    )
    parser.add_argument(
        "--forward-days",
        type=int,
        default=5,
        help="预测未来N天的收益（默认：5）"
    )
    parser.add_argument(
        "--force-retrain",
        action="store_true",
        help="强制重新训练（忽略缓存）"
    )
    parser.add_argument(
        "--no-warm-start",
        action="store_true",
        help="禁用增量学习（从头训练）"
    )

    args = parser.parse_args()

    train_optimized(
        n_dates=args.n_dates,
        forward_days=args.forward_days,
        force_retrain=args.force_retrain,
        warm_start=not args.no_warm_start,
    )


if __name__ == "__main__":
    main()
