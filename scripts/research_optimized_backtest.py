"""研究报告优化版本 - 按照 SMALL_LOSS_BIG_PROFIT_RESEARCH.md 优化

关键优化：
1. 收紧止损（4% → 3%）
2. 降低止盈（25% → 20%）
3. 添加移动止损
4. 添加分批止盈
5. 提高入场阈值（50 → 55）
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


def run_research_optimized_backtest():
    """运行研究报告优化版本的回测"""

    print("=" * 70)
    print("研究报告优化版本回测")
    print("=" * 70)
    print("\n📊 优化参数（按研究报告）:")
    print("   • 止损: 3%（从 4% 收紧）")
    print("   • 止盈: 20%（从 25% 降低）")
    print("   • 入场阈值: 55（从 50 提高）")
    print("   • 目标盈亏比: 2:1")
    print("   • 目标胜率: 50-55%")
    print()

    # 配置
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # Step 1: 获取数据
    print("1️⃣  获取数据...")
    pool = get_holdings_pool(cfg)
    spot_result = get_spot_for_codes(pool, cfg)

    if spot_result.is_err():
        print(f"   ✗ 获取行情失败: {spot_result.error}")
        return

    spot = spot_result.unwrap()
    print(f"   ✓ 行情数据: {len(spot)} 只股票")

    # Step 2: 筛选股票
    print("\n2️⃣  筛选股票...")
    results, tails, _ = screen_universe(spot, cfg, cache, use_alpha=True)
    results = compute_rps(results, tails)

    # 获取 Top 10
    top_stocks = sorted(results, key=lambda s: s.hybrid_score or 0, reverse=True)[:10]
    codes = [s.code for s in top_stocks]
    names = {s.code: s.name for s in top_stocks}

    print("   ✓ Top 10 股票:")
    for i, stock in enumerate(top_stocks, 1):
        print(f"      {i}. {stock.code} ({stock.name}) - 分数: {stock.hybrid_score}")

    # Step 3: 获取 K 线数据
    print("\n3️⃣  获取 K 线数据...")
    klines = {}

    for code in codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()

    print(f"   ✓ K 线数据: {len(klines)} 只股票")

    # Step 4: 运行优化后的回测
    print("\n4️⃣  运行优化后的回测...")
    t0 = time.time()

    # 研究报告推荐参数
    stop_loss_pct = 0.03      # 3%止损（收紧）
    take_profit_pct = 0.20    # 20%止盈（降低，提高盈亏比）
    entry_threshold = 55.0    # 55分入场阈值（提高）

    engine = EnhancedBacktestEngine(
        hold_days=15,          # 持仓15天
        max_positions=5,       # 最多5只
        commission=0.0003,
        slippage=0.001,
        stamp_tax=0.0005,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        benchmark_code=cfg.benchmark_code,
        entry_threshold=entry_threshold,
        max_sector_pct=0.30,
        use_reversal=False,
        use_alpha=True,
        use_kelly=True,
        backtest_start_date=None,
    )

    print("   参数配置:")
    print(f"     • 止损: {stop_loss_pct:.2%}")
    print(f"     • 止盈: {take_profit_pct:.2%}")
    print(f"     • 入场阈值: {entry_threshold}")
    print("     • 持仓天数: 15")
    print("     • 最大持仓: 5")

    result = engine.run_portfolio(klines, names)
    t1 = time.time()

    print(f"   ✓ 回测完成 (耗时 {t1 - t0:.2f}s)")

    # Step 5: 显示结果
    print("\n" + "=" * 70)
    print("回测结果（研究报告优化版本）")
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

    # 计算盈亏比
    if result.avg_loss != 0:
        profit_loss_ratio = abs(result.avg_win / result.avg_loss)
        print(f"   • 实际盈亏比: {profit_loss_ratio:.2f}")

    if result.trade_count > 0:
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

        # 显示交易明细
        if result.trades:
            print("\n📋 交易明细（前 5 笔）:")
            for i, trade in enumerate(result.trades[:5], 1):
                print(f"   {i}. {trade.code} ({trade.name})")
                print(f"      买入: {trade.entry_date} @ ¥{trade.entry_price:.2f}")
                print(f"      卖出: {trade.exit_date} @ ¥{trade.exit_price:.2f}")
                print(f"      收益: {trade.return_pct:.2%}")
                print(f"      退出: {trade.exit_reason}")
                print()

        # 与优化前对比
        print("\n📊 与优化前对比:")
        print(f"   {'指标':<20} {'优化前':<15} {'优化后':<15} {'改进':<15}")
        print(f"   {'-' * 65}")
        print(f"   {'总收益':<20} {'+55.65%':<15} {f'{result.total_return:.2%}':<15}")
        print(f"   {'最大回撤':<20} {'22.54%':<15} {f'{result.max_drawdown:.2%}':<15}")
        print(f"   {'胜率':<20} {'50.0%':<15} {f'{result.win_rate:.2%}':<15}")
        print(f"   {'盈亏比':<20} {'1.06':<15} {f'{result.profit_factor:.2f}':<15}")

    else:
        print("\n⚠️  未产生交易")
        print("\n可能原因:")
        print(f"   1. 入场阈值过高（当前: {entry_threshold}）")
        print(f"   2. 止损过紧（当前: {stop_loss_pct:.2%}）")
        print("   3. 数据覆盖范围不足")

    # 保存结果
    print("\n5️⃣  保存结果...")
    from aimoon.output import OutputFormatter
    fmt = OutputFormatter(cfg)

    report_path = fmt.export_backtest_report(result, top_stocks, cfg)
    print(f"   ✓ 回测报告: {report_path}")

    print("\n" + "=" * 70)
    print("✓ 研究报告优化版本回测完成")
    print("=" * 70)

    return result


if __name__ == "__main__":
    run_research_optimized_backtest()
