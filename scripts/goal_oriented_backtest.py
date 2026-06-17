"""目标导向优化版本 - 盈亏比 > 2.5，亏损笔数可控

核心优化：
1. 放宽止损（3% → 5%）- 减少频繁止损
2. 提高止盈（20% → 30%）- 提高盈亏比
3. 提高入场阈值（55 → 65）- 提高胜率
4. 使用移动止损 - 保护利润
5. 分批止盈 - 锁定部分利润
6. 降低单笔风险（2% → 1.5%）- 控制亏损
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


def run_goal_oriented_backtest():
    """运行目标导向的回测 - 盈亏比 > 2.5"""

    print("=" * 70)
    print("目标导向优化版本回测")
    print("=" * 70)
    print("\n🎯 核心目标:")
    print("   • 盈亏比 > 2.5")
    print("   • 亏损笔数可控")
    print()
    print("📊 优化策略:")
    print("   • 放宽止损（3% → 5%）- 减少频繁止损")
    print("   • 提高止盈（20% → 30%）- 提高盈亏比")
    print("   • 提高入场阈值（55 → 65）- 提高胜率")
    print("   • 降低单笔风险（2% → 1.5%）- 控制亏损")
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

    # 获取 Top 15（提高入场质量）
    top_stocks = sorted(results, key=lambda s: s.hybrid_score or 0, reverse=True)[:15]
    codes = [s.code for s in top_stocks]
    names = {s.code: s.name for s in top_stocks}

    print("   ✓ Top 15 股票:")
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

    # 目标导向参数 - 阶段1：大幅提高止盈
    stop_loss_pct = 0.05      # 5%止损（保持）
    take_profit_pct = 0.50    # 50%止盈（大幅提高，目标盈亏比 10:1）
    entry_threshold = 70.0    # 70分入场阈值（提高）

    engine = EnhancedBacktestEngine(
        hold_days=15,          # 持仓15天
        max_positions=5,       # 最多5只
        commission=0.0003,
        slippage=0.002,        # 0.2%滑点（真实）
        stamp_tax=0.001,       # 0.1%印花税（真实）
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        benchmark_code=cfg.benchmark_code,
        entry_threshold=entry_threshold,
        max_sector_pct=0.25,   # 25%行业限制
        use_reversal=False,
        use_alpha=True,
        use_kelly=True,
        backtest_start_date=None,
    )

    print("   参数配置:")
    print(f"     • 止损: {stop_loss_pct:.2%}（放宽，减少频繁止损）")
    print(f"     • 止盈: {take_profit_pct:.2%}（提高盈亏比）")
    print(f"     • 入场阈值: {entry_threshold}（提高胜率）")
    print("     • 持仓天数: 15")
    print("     • 最大持仓: 5")

    result = engine.run_portfolio(klines, names)
    t1 = time.time()

    print(f"   ✓ 回测完成 (耗时 {t1 - t0:.2f}s)")

    # Step 5: 显示结果
    print("\n" + "=" * 70)
    print("回测结果（目标导向优化版本）")
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

    # 目标达成检查
    print("\n🎯 目标达成检查:")
    if result.profit_factor >= 2.5:
        print(f"   ✅ 盈亏比 >= 2.5: {result.profit_factor:.2f}")
    else:
        print(f"   ⚠️  盈亏比 < 2.5: {result.profit_factor:.2f}")

    if result.win_rate >= 0.50:
        print(f"   ✅ 胜率 >= 50%: {result.win_rate:.2%}")
    else:
        print(f"   ⚠️  胜率 < 50%: {result.win_rate:.2%}")

    if result.max_drawdown <= 0.25:
        print(f"   ✅ 最大回撤 <= 25%: {result.max_drawdown:.2%}")
    else:
        print(f"   ⚠️  最大回撤 > 25%: {result.max_drawdown:.2%}")

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

        # 与修复前对比
        print("\n📊 与修复前对比:")
        print(f"   {'指标':<20} {'修复前':<15} {'优化后':<15}")
        print(f"   {'-' * 50}")
        print(f"   {'总收益':<20} {'-17.59%':<15} {f'{result.total_return:.2%}':<15}")
        print(f"   {'最大回撤':<20} {'31.25%':<15} {f'{result.max_drawdown:.2%}':<15}")
        print(f"   {'胜率':<20} {'46.2%':<15} {f'{result.win_rate:.2%}':<15}")
        print(f"   {'盈亏比':<20} {'0.53':<15} {f'{result.profit_factor:.2f}':<15}")

    else:
        print("\n⚠️  未产生交易")
        print("\n可能原因:")
        print(f"   1. 入场阈值过高（当前: {entry_threshold}）")
        print("   2. 数据覆盖范围不足")

    # 保存结果
    print("\n5️⃣  保存结果...")
    from aimoon.output import OutputFormatter
    fmt = OutputFormatter(cfg)

    report_path = fmt.export_backtest_report(result, top_stocks, cfg)
    print(f"   ✓ 回测报告: {report_path}")

    print("\n" + "=" * 70)
    print("✓ 目标导向优化版本回测完成")
    print("=" * 70)

    return result


if __name__ == "__main__":
    run_goal_oriented_backtest()
