"""股票筛选器 — 组合评分函数"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.history import get_kline
from aimoon.indicators.technical import TechInd
from aimoon.models import ScoredStock
from aimoon.scoring import collect_signals, hybrid_score

if TYPE_CHECKING:
    from aimoon.factors.incremental import IncrementalFactorEngine
    from aimoon.factors.registry import Registry

logger = logging.getLogger(__name__)


def screen_stock(
    code: str,
    name: str,
    kline: pd.DataFrame,
    use_reversal: bool = False,
) -> ScoredStock | None:
    """对单只股票评分。数据不足返回 None。"""
    if kline is None or len(kline) < 60:
        return None
    try:
        ti = TechInd(kline)
    except (KeyError, ValueError, TypeError, IndexError):
        return None
    signals = collect_signals(ti, code=code, use_reversal=use_reversal)
    if not signals:
        return None
    price = float(kline["close"].iloc[-1])
    pct = float(kline["pct_change"].iloc[-1]) if "pct_change" in kline.columns else 0.0
    turnover = float(kline["turnover"].iloc[-1]) if "turnover" in kline.columns else None
    pe = None
    pb = None
    cap = None
    total = hybrid_score(signals)
    return ScoredStock(
        code=code,
        name=name,
        price=price,
        pct_change=pct,
        turnover=turnover,
        pe=pe,
        pb=pb,
        market_cap_yi=cap,
        signals=tuple(signals),
        total_score=total,
    )


def screen_universe(
    universe: pd.DataFrame,
    cfg: Config,
    cache: DataCache,
    klines: dict[str, pd.DataFrame] | None = None,
) -> tuple[list[ScoredStock], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """并发评分候选池。返回 (results, kline_tails, all_klines)。"""
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
        scored = screen_stock(code, name, kdf, use_reversal=cfg.use_reversal)
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

    if cfg.use_alpha and all_klines:
        panel = _build_shared_panel(all_klines)
        if panel is not None:
            results = _inject_alpha_signals(results, panel, all_klines, cfg.cache_dir)
            results = _inject_ml_signals(results, panel, cfg.cache_dir)

    return results, tails, all_klines


def _build_shared_panel(all_klines: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame] | None:
    """Build a shared wide panel from all klines, with batch technical indicators."""
    from aimoon.factors.panel import build_panel

    panel = build_panel(all_klines)
    if panel is None:
        return None

    try:
        from aimoon.indicators.technical import add_all_indicators_batch

        panel = add_all_indicators_batch(panel)
    except Exception as e:
        logger.debug("Batch TechInd failed: %s", e)

    return panel


def _inject_alpha_signals(
    results: list[ScoredStock],
    panel: dict[str, pd.DataFrame],
    all_klines: dict[str, pd.DataFrame] | None = None,
    cache_dir: str | None = None,
) -> list[ScoredStock]:
    """Run Alpha Zoo factors on the shared panel and inject signals.

    优化策略（相比全量串行计算）：
    1. 因子质量预过滤 — 通过 ICIR/Turnover/Correlation 剔除弱因子，缓存 30 天
    2. 并行计算 — 使用 ThreadPoolExecutor 并行计算因子（Numpy 释放 GIL）
    3. 因子缓存 — 已计算因子结果在内存中复用
    """
    from aimoon.enhanced_backtest.helpers import parallel_compute_factors
    from aimoon.factors.registry import get_default_registry
    from aimoon.factors.scorer import compute_alpha_signals

    registry = get_default_registry()

    # 1. 获取质量过滤后的因子列表（优先从磁盘缓存加载）
    factor_ids: list[str] = []
    if all_klines and cache_dir:
        from pathlib import Path

        try:
            from aimoon.ml.factor_quality import get_or_compute_filtered_ids

            factor_ids = get_or_compute_filtered_ids(
                panel, all_klines, registry, cache_dir=Path(cache_dir)
            )
        except Exception as e:
            logger.debug("Factor quality filter skipped: %s", e)

    if not factor_ids:
        # 保底：使用分组抽样子集（避免计算全部 452 个因子）
        from aimoon.ml.feature_pipeline import _select_factor_subset

        factor_ids = _select_factor_subset(registry, 100)

    # 2. 并行计算选中的因子
    factor_cache: dict[str, pd.DataFrame] = {}
    parallel_compute_factors(registry, panel, factor_ids, factor_cache)

    logger.info(
        "Alpha screening: computed %d/%d factors in parallel",
        len(factor_cache),
        len(factor_ids),
    )

    # 3. 生成截面 Signal
    alpha_signals = compute_alpha_signals(
        registry,
        panel,
        filter_to_ids=list(factor_cache.keys()),
        factor_cache=factor_cache,
    )
    if not alpha_signals:
        return results

    enhanced: list[ScoredStock] = []
    for scored in results:
        extra = alpha_signals.get(scored.code, [])
        if extra:
            new_signals = tuple(list(scored.signals) + extra)
            new_total = hybrid_score(list(new_signals))
            scored = ScoredStock(
                code=scored.code,
                name=scored.name,
                price=scored.price,
                pct_change=scored.pct_change,
                turnover=scored.turnover,
                pe=scored.pe,
                pb=scored.pb,
                market_cap_yi=scored.market_cap_yi,
                signals=new_signals,
                rps=scored.rps,
                total_score=new_total,
            )
        enhanced.append(scored)

    n_with = sum(1 for s in enhanced if any(sig.name.startswith("alpha_") for sig in s.signals))
    logger.info("Alpha Zoo: %d stocks with alpha signals", n_with)
    return enhanced


def _inject_ml_signals(
    results: list[ScoredStock],
    panel: dict[str, pd.DataFrame],
    cache_dir: str | None = None,
) -> list[ScoredStock]:
    """Inject ML ensemble model prediction signals into scored results."""
    try:
        from aimoon.factors.registry import get_default_registry
        from aimoon.ml.ensemble import EnsemblePredictor, ensemble_predict_signals
    except ImportError:
        return results

    predictor = EnsemblePredictor.from_cache(cache_dir)
    if not predictor.has_any:
        return results

    registry = get_default_registry()
    ml_signals = ensemble_predict_signals(predictor, panel, registry)
    if not ml_signals:
        return results

    enhanced: list[ScoredStock] = []
    for scored in results:
        extra = ml_signals.get(scored.code, [])
        if extra:
            new_signals = tuple(list(scored.signals) + list(extra))
            new_total = hybrid_score(list(new_signals))
            scored = ScoredStock(
                code=scored.code,
                name=scored.name,
                price=scored.price,
                pct_change=scored.pct_change,
                turnover=scored.turnover,
                pe=scored.pe,
                pb=scored.pb,
                market_cap_yi=scored.market_cap_yi,
                signals=new_signals,
                rps=scored.rps,
                total_score=new_total,
            )
        enhanced.append(scored)

    n_with = sum(1 for s in enhanced if any(sig.category == "ml" for sig in s.signals))
    logger.info("ML signals: %d stocks received ML signals", n_with)
    return enhanced


def screen_universe_incremental(
    universe: pd.DataFrame,
    cfg: Config,
    cache: DataCache,
    klines: dict[str, pd.DataFrame] | None = None,
) -> tuple[list[ScoredStock], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """增量版 screen_universe: 仅更新变化的因子。

    与 screen_universe 相同的接口，但内部使用 IncrementalFactorEngine
    进行增量因子计算。适合盘中实时更新场景。

    Returns
    -------
    tuple[list[ScoredStock], dict, dict]
        (results, kline_tails, all_klines)
    """
    from aimoon.factors.incremental import create_incremental_engine
    from aimoon.factors.registry import get_default_registry

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
        scored = screen_stock(code, name, kdf, use_reversal=cfg.use_reversal)
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

    if cfg.use_alpha and all_klines:
        # 尝试加载增量引擎状态
        registry = get_default_registry()
        engine = create_incremental_engine(registry, all_klines)

        if engine is not None:
            # 尝试从缓存恢复状态
            engine.load(cfg.cache_dir)

            # 增量更新: 对每只股票的新数据执行 on_new_bar
            for code, kdf in all_klines.items():
                if len(kdf) > 0:
                    last_bar = kdf.iloc[-1]
                    bar_date = kdf.index[-1] if len(kdf.index) > 0 else pd.Timestamp.now()
                    new_bar = {
                        "open": float(last_bar.get("open", 0)),
                        "high": float(last_bar.get("high", 0)),
                        "low": float(last_bar.get("low", 0)),
                        "close": float(last_bar.get("close", 0)),
                        "volume": float(last_bar.get("volume", 0)),
                    }
                    engine.on_new_bar(code, new_bar, bar_date)

            # 批量截面更新
            cs_results = engine.batch_update_cross_sectional()

            # 将增量结果转换为 signals
            alpha_signals = _convert_incremental_to_signals(engine, cs_results, registry)

            if alpha_signals:
                results = _inject_alpha_from_dict(results, alpha_signals)

            # 持久化引擎状态
            engine.save(cfg.cache_dir)

            logger.info(
                "Incremental alpha: %d factors updated",
                len(cs_results) + sum(1 for v in engine._states.values() if v.last_output),
            )
        else:
            # 回退到全量计算
            panel = _build_shared_panel(all_klines)
            if panel is not None:
                results = _inject_alpha_signals(results, panel)
                results = _inject_ml_signals(results, panel, cfg.cache_dir)

    return results, tails, all_klines


def _convert_incremental_to_signals(
    engine: IncrementalFactorEngine,
    cs_results: dict[str, pd.Series],
    registry: Registry,
) -> dict[str, list]:
    """将增量引擎结果转换为 alpha signals 字典。"""
    from aimoon.models import Signal

    signals_by_code: dict[str, list] = {}

    # 合并非截面因子和截面因子的结果
    all_factors: dict[str, pd.Series] = {}

    # 非截面因子: 从 engine 状态提取
    for alpha_id, state in engine._states.items():
        if state.is_cross_sectional:
            continue
        if state.last_output:
            all_factors[alpha_id] = pd.Series(state.last_output)

    # 截面因子: 直接使用
    all_factors.update(cs_results)

    for alpha_id, snapshot in all_factors.items():
        try:
            alpha = registry.get(alpha_id)
        except KeyError:
            continue

        meta = alpha.meta
        nickname = meta.get("nickname") or alpha_id
        themes = meta.get("theme", [])

        # 截面排名
        ranked = snapshot.rank(pct=True, na_option="keep")

        for code in ranked.index:
            pct_val = ranked.loc[code]
            if isinstance(pct_val, pd.Series):
                pct_val = pct_val.iloc[0]
            if pd.isna(pct_val):
                continue

            score = _pct_to_score_incr(float(pct_val), themes)
            if score == 0:
                continue

            signal = Signal(
                name=f"alpha_{alpha_id}",
                label=f"\u03b1:{nickname}({pct_val:.0%})",
                score=score,
                category="alpha",
            )
            signals_by_code.setdefault(code, []).append(signal)

    return signals_by_code


def _pct_to_score_incr(pct: float, themes: list[str]) -> int:
    """Convert percentile rank to signal score (incremental version)."""
    is_reversal = "reversal" in themes

    if pct >= 0.85:
        score = +3
    elif pct >= 0.65:
        score = +2
    elif pct <= 0.15:
        score = -3
    elif pct <= 0.35:
        score = -2
    else:
        return 0

    return -score if is_reversal else score


def _inject_alpha_from_dict(
    results: list[ScoredStock],
    alpha_signals: dict[str, list],
) -> list[ScoredStock]:
    """将 alpha signals 字典注入 scored results。"""
    enhanced: list[ScoredStock] = []
    for scored in results:
        extra = alpha_signals.get(scored.code, [])
        if extra:
            new_signals = tuple(list(scored.signals) + extra)
            new_total = hybrid_score(list(new_signals))
            scored = ScoredStock(
                code=scored.code,
                name=scored.name,
                price=scored.price,
                pct_change=scored.pct_change,
                turnover=scored.turnover,
                pe=scored.pe,
                pb=scored.pb,
                market_cap_yi=scored.market_cap_yi,
                signals=new_signals,
                rps=scored.rps,
                total_score=new_total,
            )
        enhanced.append(scored)
    return enhanced


def _safe_float(row: pd.Series | None, key: str) -> float:
    if row is not None and key in row.index and pd.notna(row[key]):
        return float(row[key])
    return 0.0
