"""联合优化配置 — 所有超参数与路径集中管理。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OptimizerConfig:
    """联合优化器的全部配置。"""

    # ── 数据 ──────────────────────────────────────────────────────────
    data_dir: str = "data"
    output_dir: str = "output"
    cache_dir: str = ".aimoon_cache"

    # ── 时间序列划分 ──────────────────────────────────────────────────
    train_ratio: float = 0.6
    val_ratio: float = 0.2
    # test_ratio = 1 - train_ratio - val_ratio

    # ── 因子参数搜索范围 ──────────────────────────────────────────────
    momentum_windows: tuple[int, ...] = (5, 10, 20, 30, 60)
    ma_windows: tuple[int, ...] = (5, 10, 20, 30, 60, 120)
    atr_windows: tuple[int, ...] = (5, 10, 14, 20, 40)
    rsi_windows: tuple[int, ...] = (6, 10, 14, 20)
    boll_windows: tuple[int, ...] = (10, 20, 30)
    vol_windows: tuple[int, ...] = (5, 10, 20, 40)
    obv_windows: tuple[int, ...] = (10, 20, 30)

    # ── 因子筛选阈值 ──────────────────────────────────────────────────
    min_abs_ic: float = 0.02
    min_icir: float = 0.3
    max_factor_corr: float = 0.6

    # ── 模型超参数搜索范围 ────────────────────────────────────────────
    lgbm_n_estimators_range: tuple[int, int] = (100, 1000)
    lgbm_max_depth_range: tuple[int, int] = (2, 6)
    lgbm_num_leaves_range: tuple[int, int] = (7, 127)
    lgbm_learning_rate_range: tuple[float, float] = (0.005, 0.05)
    lgbm_min_child_samples_range: tuple[int, int] = (5, 50)
    lgbm_subsample_range: tuple[float, float] = (0.6, 1.0)
    lgbm_colsample_range: tuple[float, float] = (0.6, 1.0)
    lgbm_reg_alpha_range: tuple[float, float] = (0.0, 10.0)
    lgbm_reg_lambda_range: tuple[float, float] = (0.0, 10.0)

    # ── 预测周期 ──────────────────────────────────────────────────────
    forward_days_options: tuple[int, ...] = (1, 5, 10, 22)

    # ── 优化设置 ──────────────────────────────────────────────────────
    n_optuna_trials: int = 100
    optuna_timeout: int | None = None
    n_cv_splits: int = 5
    random_seed: int = 42

    # ── 交易成本 ──────────────────────────────────────────────────────
    transaction_cost_bps: float = 10.0  # 0.1% 单边

    # ── 回测参数 ──────────────────────────────────────────────────────
    top_quantile: float = 0.20  # 做多 top 20%
    bottom_quantile: float = 0.20  # 做空 bottom 20%
    rebalance_freq: int = 5  # 每 N 日再平衡

    # ── 过拟合惩罚 ────────────────────────────────────────────────────
    overfit_penalty_weight: float = 0.5

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)

    @property
    def cache_path(self) -> Path:
        return Path(self.cache_dir)
