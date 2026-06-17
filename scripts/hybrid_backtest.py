"""混合评分回测验证脚本

对比新旧评分方法的回测效果
"""

import time

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.data.spot import get_spot_for_codes
from aimoon.enhanced_backtest import EnhancedBacktestEngine
from aimoon.scoring.rps import compute_rps
from aimoon.screener import screen_universe


def run_hybrid_backtest():
    """运行混合评分回测"""

    print("=" * 70)
    print("混合评分回测验证")
    print("=" * 70)

    # 配置
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # Step 1: 获取持仓池
    print("\n1️⃣  获取持仓池...")
    t0 = time.time()
    pool = get_holdings_pool(cfg)
    t1 = time.time()
    print(f"   ✓ 持仓池: {len(pool)} 只股票 (耗时 {t1 - t0:.2f}s)")

    # Step 2: 获取行情
    print("\n2️⃣  获取行情...")
    t0 = time.time()
    spot_result = get_spot_for_codes(pool, cfg)
    t1 = time.time()

    if spot_result.is_err():
        print(f"   ✗ 获取行情失败: {spot_result.error}")
        return

    spot = spot_result.unwrap()
    print(f"   ✓ 行情数据: {len(spot)} 只股票 (耗时 {t1 - t0:.2f}s)")

    # Step 3: 筛选股票
    print("\n3️⃣  筛选股票...")
    t0 = time.time()
    results, tails, _ = screen_universe(spot, cfg, cache, use_alpha=True)
    t1 = time.time()
    print(f"   ✓ 筛选完成: {len(results)} 只股票 (耗时 {t1 - t0:.2f}s)")

    if not results:
        print("   ✗ 未筛选到股票")
        return

    # Step 4: 计算 RPS 并排序
    print("\n4️⃣  计算 RPS 和排序...")
    results = compute_rps(results, tails)

    # 使用混合分数排序
    top_n = min(10, len(results))
    top_stocks = sorted(results, key=lambda s: s.hybrid_score or 0, reverse=True)[:top_n]

    print(f"   ✓ Top {top_n} 股票 (按混合分数):")
    for i, stock in enumerate(top_stocks, 1):
        print(f"      {i}. {stock.code} ({stock.name}) - 混合分数: {stock.hybrid_score}, 建议: {stock.suggestion[0]}")

    # Step 5: 获取 K 线数据
    print("\n5️⃣  获取 K 线数据...")
    t0 = time.time()
    klines = {}
    codes = [s.code for s in top_stocks]
    names = {s.code: s.name for s in top_stocks}

    for code in codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()

    t1 = time.time()
    print(f"   ✓ K 线数据: {len(klines)} 只股票 (耗时 {t1 - t0:.2f}s)")

    if len(klines) < 2:
        print("   ✗ K 线数据不足")
        return

    # Step 6: 运行回测
    print("\n6️⃣  运行回测...")
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
        use_kelly=True,
        backtest_start_date="2024-02-05",
    )

    result = engine.run_portfolio(klines, names)
    t1 = time.time()

    print(f"   ✓ 回测完成 (耗时 {t1 - t0:.2f}s)")

    # Step 7: 显示结果
    print("\n" + "=" * 70)
    print("回测结果（混合评分）")
    print("=" * 70)

    print("\n📊 核心指标:")
    print(f"   • 总收益: {result.total_return:.2%}")
    print(f"   • 年化收益: {result.annual_return:.2%}")
    print(f"   • 夏普比率: {result.sharpe_ratio:.2f}")
    print(f"   • 最大回撤: {result.max_drawdown:.2%}")
    print(f"   • 胜率: {result.win_rate:.2%}")
    print(f"   • 盈亏比: {result.profit_factor:.2f}")
    print(f"   • 交易次数: {result.trade_count}")
    print(f"   • 平均持仓: {result.avg_hold_days:.1f} 天")

    print("\n📈 基准对比:")
    print(f"   • 基准收益: {result.benchmark_return:.2%}")
    print(f"   • 超额收益: {result.excess_return:.2%}")
    print(f"   • Calmar 比率: {result.calmar_ratio:.2f}")

    print("\n⚠️  风险指标:")
    print(f"   • 最大回撤: {result.max_drawdown:.2%}")
    print(f"   • Sortino 比率: {result.sortino_ratio:.2f}")
    print(f"   • 盈亏比: {result.profit_loss_ratio:.2f}")
    print(f"   • 最大连续亏损: {result.max_consecutive_loss}")
    print(f"   • 信息比率: {result.information_ratio:.2f}")

    # 保存结果
    print("\n7️⃣  保存结果...")
    from aimoon.output import OutputFormatter
    fmt = OutputFormatter(cfg)

    # 导出回测报告
    report_path = fmt.export_backtest_report(result, top_stocks, cfg)
    print(f"   ✓ 回测报告: {report_path}")

    # 导出图表
    try:
        import os

        from aimoon.charts import plot_drawdown, plot_equity_curve, plot_monthly_returns
        os.makedirs(cfg.output_dir, exist_ok=True)

        eq_path = os.path.join(cfg.output_dir, "hybrid_equity_curve.png")
        dd_path = os.path.join(cfg.output_dir, "hybrid_drawdown.png")
        mr_path = os.path.join(cfg.output_dir, "hybrid_monthly_returns.png")

        plot_equity_curve(result.equity_curve, title="Hybrid Scoring Equity Curve", filepath=eq_path)
        plot_drawdown(result.drawdown_curve, filepath=dd_path)
        plot_monthly_returns(result.trades, filepath=mr_path)

        print(f"   ✓ 权益曲线: {eq_path}")
        print(f"   ✓ 回撤图: {dd_path}")
        print(f"   ✓ 月度收益: {mr_path}")
    except ImportError:
        print("   ⚠ 图表生成需要 matplotlib")

    print("\n" + "=" * 70)
    print("✓ 混合评分回测完成！")
    print("=" * 70)

    return result


if __name__ == "__main__":
    run_hybrid_backtest()
