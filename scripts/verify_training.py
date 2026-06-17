"""训练效果验证脚本 - 检查过拟合和模型质量

使用方法：
    python scripts/verify_training.py
    python scripts/verify_training.py --detailed
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def verify_training_quality(
    model_dir: str = ".aimoon_cache/ml",
    detailed: bool = False,
) -> dict[str, any]:
    """验证训练质量并输出诊断报告。"""

    meta_dir = Path(model_dir)

    if not meta_dir.exists():
        logger.error(f"模型目录不存在: {meta_dir}")
        return {"status": "error", "message": "模型目录不存在"}

    report = {
        "status": "ok",
        "warnings": [],
        "recommendations": [],
    }

    # 检查XGBoost元数据
    xgb_meta_path = meta_dir / "meta.json"
    if xgb_meta_path.exists():
        with open(xgb_meta_path) as f:
            xgb_meta = json.load(f)

        report["xgb"] = {
            "ic_val": xgb_meta.get("ic", 0),
            "ic_train": xgb_meta.get("ic_train", 0),
            "overfit_ratio": xgb_meta.get("overfit_ratio", 0),
            "best_iteration": xgb_meta.get("best_iteration", 0),
            "n_samples": xgb_meta.get("n_stocks", 0),
            "n_features": xgb_meta.get("n_features", 0),
        }

        # 过拟合检查
        overfit_ratio = xgb_meta.get("overfit_ratio", 0)
        if overfit_ratio > 3.0:
            msg = f"XGBoost过拟合严重 (train_IC/val_IC={overfit_ratio:.2f} >> 3.0)"
            report["warnings"].append(msg)
            logger.warning(f"⚠️  {msg}")
        elif overfit_ratio > 2.0:
            msg = f"XGBoost可能轻微过拟合 (train_IC/val_IC={overfit_ratio:.2f})"
            report["warnings"].append(msg)
            logger.info(f"ℹ️  {msg}")

        # IC质量检查
        ic_val = xgb_meta.get("ic", 0)
        if ic_val < 0.1:
            msg = f"XGBoost验证集IC太低 ({ic_val:.4f} < 0.1)，模型可能无效果"
            report["warnings"].append(msg)
            report["recommendations"].append("增加n_dates或调整特征")
            logger.warning(f"⚠️  {msg}")
        elif ic_val < 0.2:
            msg = f"XGBoost验证集IC较低 ({ic_val:.4f} < 0.2)"
            logger.info(f"ℹ️  {msg}")
        else:
            logger.info(f"✓ XGBoost验证集IC: {ic_val:.4f}")

        # 数据量检查
        n_samples = xgb_meta.get("n_stocks", 0)
        n_features = xgb_meta.get("n_features", 0)
        if n_samples < n_features * 10:
            msg = f"样本数({n_samples}) < 特征数({n_features})*10，数据量不足"
            report["warnings"].append(msg)
            report["recommendations"].append("增加n_dates或减少特征数")
            logger.warning(f"⚠️  {msg}")

        # CV分数检查
        cv_scores = xgb_meta.get("cv_scores", [])
        if cv_scores:
            avg_cv = sum(cv_scores) / len(cv_scores)
            report["xgb"]["avg_cv_score"] = avg_cv
            logger.info(f"  CV平均分: {avg_cv:.4f}")
            if avg_cv < 0.1:
                report["warnings"].append("XGBoost CV分数太低，模型泛化能力差")

        if detailed:
            logger.info("\nXGBoost详细信息:")
            logger.info(f"  训练集IC: {xgb_meta.get('ic_train', 0):.4f}")
            logger.info(f"  验证集IC: {xgb_meta.get('ic', 0):.4f}")
            logger.info(f"  过拟合比率: {overfit_ratio:.2f}")
            logger.info(f"  最佳迭代: {xgb_meta.get('best_iteration', 0)}")
            logger.info(f"  训练时间: {xgb_meta.get('train_duration', 0):.1f}s")
            logger.info(f"  样本数: {n_samples}")
            logger.info(f"  特征数: {n_features}")

    # 检查LightGBM元数据
    lgbm_meta_path = meta_dir / "lgbm_meta.json"
    if lgbm_meta_path.exists():
        with open(lgbm_meta_path) as f:
            lgbm_meta = json.load(f)

        report["lgbm"] = {
            "ic_val": lgbm_meta.get("ic", 0),
            "ic_train": lgbm_meta.get("ic_train", 0),
            "overfit_ratio": lgbm_meta.get("overfit_ratio", 0),
            "best_iteration": lgbm_meta.get("best_iteration", 0),
            "n_samples": lgbm_meta.get("n_stocks", 0),
            "n_features": lgbm_meta.get("n_features", 0),
        }

        # 过拟合检查
        overfit_ratio = lgbm_meta.get("overfit_ratio", 0)
        if overfit_ratio > 3.0:
            msg = f"LightGBM过拟合严重 (train_IC/val_IC={overfit_ratio:.2f} >> 3.0)"
            report["warnings"].append(msg)
            logger.warning(f"⚠️  {msg}")
        elif overfit_ratio > 2.0:
            msg = f"LightGBM可能轻微过拟合 (train_IC/val_IC={overfit_ratio:.2f})"
            report["warnings"].append(msg)
            logger.info(f"ℹ️  {msg}")

        # IC质量检查
        ic_val = lgbm_meta.get("ic", 0)
        if ic_val < 0.1:
            msg = f"LightGBM验证集IC太低 ({ic_val:.4f} < 0.1)，模型可能无效果"
            report["warnings"].append(msg)
            report["recommendations"].append("增加n_dates或调整特征")
            logger.warning(f"⚠️  {msg}")
        elif ic_val < 0.2:
            msg = f"LightGBM验证集IC较低 ({ic_val:.4f} < 0.2)"
            logger.info(f"ℹ️  {msg}")
        else:
            logger.info(f"✓ LightGBM验证集IC: {ic_val:.4f}")

        # 数据量检查
        n_samples = lgbm_meta.get("n_stocks", 0)
        n_features = lgbm_meta.get("n_features", 0)
        if n_samples < n_features * 10:
            msg = f"LightGBM样本数({n_samples}) < 特征数({n_features})*10，数据量不足"
            report["warnings"].append(msg)

        # CV分数检查
        cv_scores = lgbm_meta.get("cv_scores", [])
        if cv_scores:
            avg_cv = sum(cv_scores) / len(cv_scores)
            report["lgbm"]["avg_cv_score"] = avg_cv
            logger.info(f"  CV平均分: {avg_cv:.4f}")
            if avg_cv < 0.1:
                report["warnings"].append("LightGBM CV分数太低，模型泛化能力差")

        if detailed:
            logger.info("\nLightGBM详细信息:")
            logger.info(f"  训练集IC: {lgbm_meta.get('ic_train', 0):.4f}")
            logger.info(f"  验证集IC: {lgbm_meta.get('ic', 0):.4f}")
            logger.info(f"  过拟合比率: {overfit_ratio:.2f}")
            logger.info(f"  最佳迭代: {lgbm_meta.get('best_iteration', 0)}")
            logger.info(f"  训练时间: {lgbm_meta.get('train_duration', 0):.1f}s")
            logger.info(f"  样本数: {n_samples}")
            logger.info(f"  特征数: {n_features}")

    # 检查集成元数据
    ensemble_meta_path = meta_dir / "ensemble_meta.json"
    if ensemble_meta_path.exists():
        with open(ensemble_meta_path) as f:
            ensemble_meta = json.load(f)

        report["ensemble"] = {
            "xgb_weight": ensemble_meta.get("xgb_weight", 0.5),
            "lgbm_weight": ensemble_meta.get("lgbm_weight", 0.5),
            "xgb_ic": ensemble_meta.get("xgb_ic", 0),
            "lgbm_ic": ensemble_meta.get("lgbm_ic", 0),
        }

        logger.info("\n集成模型:")
        logger.info(f"  XGBoost权重: {ensemble_meta.get('xgb_weight', 0.5):.2f}")
        logger.info(f"  LightGBM权重: {ensemble_meta.get('lgbm_weight', 0.5):.2f}")

    # 总体评估
    logger.info("\n=== 总体评估 ===")

    if not report["warnings"]:
        logger.info("✓ 模型质量良好，未检测到严重问题")
        report["status"] = "ok"
    elif len(report["warnings"]) <= 2:
        logger.info(f"⚠️  有 {len(report['warnings'])} 个警告，建议查看")
        report["status"] = "warning"
    else:
        logger.error(f"❌ 有 {len(report['warnings'])} 个警告，模型可能有问题")
        report["status"] = "error"

    # 输出建议
    if report["recommendations"]:
        logger.info("\n改进建议:")
        for i, rec in enumerate(report["recommendations"], 1):
            logger.info(f"  {i}. {rec}")

    # 关键指标汇总
    logger.info("\n关键指标汇总:")
    if "xgb" in report:
        logger.info(f"  XGBoost val_IC: {report['xgb']['ic_val']:.4f}")
        logger.info(f"  XGBoost 过拟合比率: {report['xgb']['overfit_ratio']:.2f}")
    if "lgbm" in report:
        logger.info(f"  LightGBM val_IC: {report['lgbm']['ic_val']:.4f}")
        logger.info(f"  LightGBM 过拟合比率: {report['lgbm']['overfit_ratio']:.2f}")

    logger.info(f"\n状态: {report['status']}")

    return report


def main():
    parser = argparse.ArgumentParser(description="验证训练效果")
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="输出详细的诊断信息"
    )
    parser.add_argument(
        "--model-dir",
        type=str,
        default=".aimoon_cache/ml",
        help="模型缓存目录（默认：.aimoon_cache/ml）"
    )

    args = parser.parse_args()

    report = verify_training_quality(
        model_dir=args.model_dir,
        detailed=args.detailed,
    )

    # 如果有问题，返回非零退出码
    if report["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
