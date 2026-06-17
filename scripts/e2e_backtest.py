from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('e2e_backtest.log', encoding='utf-8'),
    ],
)
logger = logging.getLogger(__name__)


def run_e2e_backtest(
    n_dates: int = 200,
    history_days: int = 360,
    forward_days: int = 5,
    force_retrain: bool = False,
) -> None:
    from aimoon.cache import DataCache
    from aimoon.config import Config
    from aimoon.data.filters import get_holdings_pool
    from aimoon.data.history import get_kline
    from aimoon.enhanced_backtest import EnhancedBacktestEngine
    from aimoon.factors.panel import build_panel
    from aimoon.factors.registry import get_default_registry
    from aimoon.ml.trainer import train_ensemble

    cfg = Config(history_days=history_days)
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    logger.info('=' * 60)
    logger.info('Step 1: 获取持仓池')
    logger.info('=' * 60)
    t0 = time.time()
    pool = get_holdings_pool(cfg)
    logger.info('持仓池: %d 只股票 (%.1fs)', len(pool), time.time() - t0)

    logger.info('=' * 60)
    logger.info('Step 2: 拉取 K 线数据 (history_days=%d)', history_days)
    logger.info('=' * 60)
    t0 = time.time()
    klines: dict = {}
    for code in pool:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            kdf = r.unwrap()
            if len(kdf) >= 60:
                klines[code] = kdf
    logger.info('K 线数据: %d 只股票 (%.1fs)', len(klines), time.time() - t0)
    if len(klines) < 10:
        logger.error('数据不足 (<10 只股票)，无法训练')
        return

    logger.info('=' * 60)
    logger.info('Step 3: 构建 Alpha Zoo 面板')
    logger.info('=' * 60)
    t0 = time.time()
    panel = build_panel(klines, min_rows=60)
    if panel is None:
        logger.error('面板构建失败')
        return
    logger.info(
        '面板: %d 天 x %d 只股票 (%.1fs)',
        panel['close'].shape[0],
        panel['close'].shape[1],
        time.time() - t0,
    )
    registry = get_default_registry()
    logger.info('因子注册表: %d 个因子', len(registry.list()))

    logger.info('=' * 60)
    logger.info('Step 4: 训练 ML 集成模型 (n_dates=%d)', n_dates)
    logger.info('=' * 60)
    t0 = time.time()
    ensemble_result = train_ensemble(
        panel=panel,
        klines=klines,
        registry=registry,
        n_dates=n_dates,
        forward_days=forward_days,
        save_dir='.aimoon_cache/ml',
        warm_start=not force_retrain,
    )
    logger.info(
        '集成模型训练完成 (%.1fs): EN IC=%.4f, XGB IC=%.4f, LGBM IC=%.4f',
        time.time() - t0,
        ensemble_result['en_result'].ic,
        ensemble_result['xgb_result'].ic,
        ensemble_result['lgbm_result'].ic,
    )
    logger.info(
        '集成权重: EN=%.2f, XGB=%.2f, LGBM=%.2f',
        ensemble_result['en_weight'],
        ensemble_result['xgb_weight'],
        ensemble_result['lgbm_weight'],
    )

    logger.info('=' * 60)
    logger.info('Step 5: 运行 EnhancedBacktestEngine (use_ml=True, use_alpha=True)')
    logger.info('=' * 60)
    t0 = time.time()
    engine = EnhancedBacktestEngine(
        hold_days=cfg.hold_days,
        max_positions=5,
        commission=0.0003,
        slippage=0.001,
        stamp_tax=0.0005,
        stop_loss_pct=cfg.stop_loss_pct,
        take_profit_pct=cfg.take_profit_pct,
        benchmark_code=cfg.benchmark_code,
        entry_threshold=cfg.entry_threshold,
        max_sector_pct=cfg.max_sector_pct,
        use_reversal=False,
        use_alpha=True,
        use_ml=True,
        use_kelly=True,
        backtest_start_date=cfg.backtest_start_date,
    )
    names = {code: code for code in klines}
    result = engine.run_portfolio(klines, names)
    logger.info('回测完成 (%.1fs)', time.time() - t0)

    logger.info('=' * 60)
    logger.info('回测结果')
    logger.info('=' * 60)
    logger.info('总收益:        %+.2f%%', result.total_return)
    logger.info('年化收益:      %+.2f%%', result.annual_return)
    logger.info('Sharpe:        %.2f', result.sharpe_ratio)
    logger.info('Sortino:       %.2f', result.sortino_ratio)
    logger.info('最大回撤:      %.2f%%', result.max_drawdown)
    logger.info('Calmar:        %.2f', result.calmar_ratio)
    logger.info('胜率:          %.2f%%', result.win_rate * 100)
    logger.info('交易次数:      %d', result.trade_count)
    logger.info('平均持仓天数:  %.1f', result.avg_hold_days)
    logger.info('盈亏比:        %.2f', result.profit_loss_ratio)
    logger.info('Profit Factor: %.2f', result.profit_factor)
    logger.info('平均盈利:      %+.2f%%', result.avg_win)
    logger.info('平均亏损:      %+.2f%%', result.avg_loss)
    logger.info('基准收益:      %+.2f%%', result.benchmark_return)
    logger.info('超额收益:      %+.2f%%', result.excess_return)
    logger.info('信息比率:      %.4f', result.information_ratio)
    logger.info('最大连续亏损:  %d', result.max_consecutive_loss)

    if result.ic_series:
        import numpy as np
        ic_arr = np.array(result.ic_series)
        logger.info('Rank IC 均值:  %.4f', np.mean(ic_arr))
        logger.info(
            'Rank ICIR:     %.4f',
            np.mean(ic_arr) / np.std(ic_arr) if np.std(ic_arr) > 0 else 0.0,
        )

    exit_stats: dict = {}
    for t in result.trades:
        r = t.exit_reason
        if r not in exit_stats:
            exit_stats[r] = {'n': 0, 'pnl': 0.0}
        exit_stats[r]['n'] += 1
        exit_stats[r]['pnl'] += t.return_pct
    logger.info('-' * 60)
    logger.info('退出原因统计:')
    for r, s in sorted(exit_stats.items(), key=lambda x: -x[1]['n']):
        logger.info('  %-20s  %3d 次, 平均 %+.2f%%', r, s['n'], s['pnl'] / s['n'])
    logger.info('=' * 60)
    logger.info('端到端回测完成')


def main() -> None:
    parser = argparse.ArgumentParser(description='端到端回测: ML训练 + EnhancedBacktest')
    parser.add_argument('--n-dates', type=int, default=200, help='训练日期快照数')
    parser.add_argument('--history-days', type=int, default=360, help='历史K线天数')
    parser.add_argument('--forward-days', type=int, default=5, help='预测未来N天收益')
    parser.add_argument('--force-retrain', action='store_true', help='强制重新训练')
    args = parser.parse_args()
    run_e2e_backtest(
        n_dates=args.n_dates,
        history_days=args.history_days,
        forward_days=args.forward_days,
        force_retrain=args.force_retrain,
    )


if __name__ == '__main__':
    main()
