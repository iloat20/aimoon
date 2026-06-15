"""股票筛选器 — 组合评分函数"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.history import get_kline
from aimoon.indicators.technical import TechInd
from aimoon.models import ScoredStock
from aimoon.scoring import collect_signals

logger = logging.getLogger(__name__)


def screen_stock(
    code: str, name: str, kline: pd.DataFrame,
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
    return ScoredStock(
        code=code, name=name, price=price,
        pct_change=pct, turnover=turnover,
        pe=pe, pb=pb, market_cap_yi=cap,
        signals=tuple(signals),
    )


def screen_universe(
    universe: pd.DataFrame, cfg: Config,
    cache: DataCache,
    klines: dict[str, pd.DataFrame] | None = None,
) -> tuple[list[ScoredStock], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """并发评分候选池。返回 (results, kline_tails, all_klines)。"""
    results: list[ScoredStock] = []
    tails: dict[str, pd.DataFrame] = {}
    all_klines: dict[str, pd.DataFrame] = {}

    def _process(row: pd.Series) -> tuple[ScoredStock | None, str, pd.DataFrame | None, pd.DataFrame | None]:
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
        results = _inject_alpha_signals(results, all_klines)
        results = _inject_ml_signals(results, all_klines, cfg.cache_dir)

    return results, tails, all_klines


def _inject_alpha_signals(
    results: list[ScoredStock],
    all_klines: dict[str, pd.DataFrame],
) -> list[ScoredStock]:
    """构建宽表，运行 Alpha Zoo 因子，注入信号到每只股票。"""
    from aimoon.factors.panel import build_panel
    from aimoon.factors.registry import get_default_registry
    from aimoon.factors.scorer import compute_alpha_signals

    panel = build_panel(all_klines)
    if panel is None:
        return results

    # Batch compute technical indicators on the wide panel (M5)
    try:
        from aimoon.indicators.technical import add_all_indicators_batch
        panel = add_all_indicators_batch(panel)
    except Exception as e:
        logger.debug("Batch TechInd failed: %s", e)

    registry = get_default_registry()
    alpha_signals = compute_alpha_signals(registry, panel)
    if not alpha_signals:
        return results

    enhanced: list[ScoredStock] = []
    for scored in results:
        extra = alpha_signals.get(scored.code, [])
        if extra:
            new_signals = tuple(list(scored.signals) + extra)
            scored = ScoredStock(
                code=scored.code, name=scored.name, price=scored.price,
                pct_change=scored.pct_change, turnover=scored.turnover,
                pe=scored.pe, pb=scored.pb, market_cap_yi=scored.market_cap_yi,
                signals=new_signals, rps=scored.rps,
            )
        enhanced.append(scored)

    n_with = sum(1 for s in enhanced if any(sig.name.startswith("alpha_") for sig in s.signals))
    logger.info("Alpha Zoo: %d stocks with alpha signals", n_with)
    return enhanced


def _inject_ml_signals(
    results: list[ScoredStock],
    all_klines: dict[str, pd.DataFrame],
    cache_dir: str | None = None,
) -> list[ScoredStock]:
    """注入 ML 集成模型预测信号到评分结果。"""
    try:
        from aimoon.factors.panel import build_panel
        from aimoon.factors.registry import get_default_registry
        from aimoon.ml.ensemble import EnsemblePredictor, ensemble_predict_signals
    except ImportError:
        return results

    predictor = EnsemblePredictor.from_cache(cache_dir)
    if not predictor.has_any:
        return results

    panel = build_panel(all_klines)
    if panel is None:
        return results

    # Batch compute technical indicators on the wide panel (M5)
    try:
        from aimoon.indicators.technical import add_all_indicators_batch
        panel = add_all_indicators_batch(panel)
    except Exception as e:
        logger.debug("Batch TechInd failed (ML path): %s", e)

    registry = get_default_registry()
    ml_signals = ensemble_predict_signals(predictor, panel, registry)
    if not ml_signals:
        return results

    enhanced: list[ScoredStock] = []
    for scored in results:
        extra = ml_signals.get(scored.code, [])
        if extra:
            new_signals = tuple(list(scored.signals) + list(extra))
            scored = ScoredStock(
                code=scored.code, name=scored.name, price=scored.price,
                pct_change=scored.pct_change, turnover=scored.turnover,
                pe=scored.pe, pb=scored.pb, market_cap_yi=scored.market_cap_yi,
                signals=new_signals, rps=scored.rps,
            )
        enhanced.append(scored)

    n_with = sum(1 for s in enhanced if any(sig.category == "ml" for sig in s.signals))
    logger.info("ML signals: %d stocks received ML signals", n_with)
    return enhanced


def _safe_float(row: pd.Series | None, key: str) -> float:
    if row is not None and key in row.index and pd.notna(row[key]):
        return float(row[key])
    return 0.0
