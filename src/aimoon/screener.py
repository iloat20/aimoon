"""股票筛选器 — ML-only 评分。

简化设计：删除了 old hybrid_scorer / collect_signals / _inject_alpha_signals /
_inject_ml_signals。筛选流程：
1. 并发获取 K 线数据
2. build_panel → compute_ashare_factors → MLPredictor 预测
3. total_score = ml_score (0-100)
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.history import get_kline
from aimoon.factors.ashare import build_panel
from aimoon.models import ScoredStock

if TYPE_CHECKING:
    from aimoon.ml.predictor import MLPredictor

logger = logging.getLogger(__name__)


def screen_stock(
    code: str,
    name: str,
    kline: pd.DataFrame,
) -> ScoredStock | None:
    """对单只股票计算基础信息（无 ML 评分时使用）。"""
    if kline is None or len(kline) < 60:
        return None
    price = float(kline["close"].iloc[-1])
    pct = float(kline["pct_change"].iloc[-1]) if "pct_change" in kline.columns else 0.0
    turnover = float(kline["turnover"].iloc[-1]) if "turnover" in kline.columns else None
    pe = None
    pb = None
    cap = None
    return ScoredStock(
        code=code,
        name=name,
        price=price,
        pct_change=pct,
        turnover=turnover,
        pe=pe,
        pb=pb,
        market_cap_yi=cap,
        signals=(),
        total_score=0,
    )


def screen_universe(
    universe: pd.DataFrame,
    cfg: Config,
    cache: DataCache,
    klines: dict[str, pd.DataFrame] | None = None,
    predictor: MLPredictor | None = None,
) -> tuple[list[ScoredStock], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """并发获取 K 线 + ML 评分。返回 (results, kline_tails, all_klines)。

    无 ML 模型时：ml_score=None, total_score=0，提示需训练。
    """
    results: list[ScoredStock] = []
    tails: dict[str, pd.DataFrame] = {}
    all_klines: dict[str, pd.DataFrame] = {}

    def _process(
        row: pd.Series,
    ) -> tuple[ScoredStock | None, str, pd.DataFrame | None, pd.DataFrame | None]:
        code = row["stock_code"]
        name = row["stock_name"]
        kdf = (klines or {}).get(code)
        if kdf is None:
            r = get_kline(code, cfg.history_days, cache)
            if r.is_err():
                return None, code, None, None
            kdf = r.unwrap()
        scored = screen_stock(code, name, kdf)
        if scored:
            return scored, code, kdf.tail(25).copy(), kdf
        return None, code, None, None

    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        futures = {ex.submit(_process, row): row["stock_code"] for _, row in universe.iterrows()}
        for fut in as_completed(futures):
            try:
                scored, code, tail, full_kdf = fut.result()
                if scored:
                    results.append(scored)
                    if tail is not None:
                        tails[code] = tail
                    if full_kdf is not None:
                        all_klines[code] = full_kdf
            except Exception as e:
                logger.warning("Screen failed: %s", e)

    # ML 预测：用 predictor 对所有股票打分
    if predictor is not None and all_klines:
        _apply_ml_scores(results, all_klines, predictor)
    else:
        logger.info("无 ML 模型可用，使用基础评分（total_score=0）")

    return results, tails, all_klines


def _apply_ml_scores(
    results: list[ScoredStock],
    all_klines: dict[str, pd.DataFrame],
    predictor: MLPredictor,
) -> None:
    """用 MLPredictor 对结果打分。"""
    panel = build_panel(all_klines, min_rows=60)
    if panel is None:
        logger.warning("ML scoring: 无法构建 panel")
        return

    ml_scores = predictor.predict(panel)
    if not ml_scores:
        logger.warning("ML scoring: 无预测结果")
        return

    updated: list[ScoredStock] = []
    for scored in results:
        ml = ml_scores.get(scored.code)
        if ml is not None:
            updated.append(
                scored.replace(
                    ml_score=ml,
                    total_score=ml,
                )
            )
        else:
            updated.append(scored)
    results[:] = updated
    logger.info("ML 评分完成：%d/%d 股票获得分数", len(ml_scores), len(results))
