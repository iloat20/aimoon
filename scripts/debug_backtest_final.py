"""带调试日志的回测脚本"""

import time

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.data.spot import get_spot_for_codes
from aimoon.enhanced_backtest import EnhancedBacktestEngine
from aimoon.scoring.rps import compute_rps
from aimoon.screener import screen_universe


def run_debug_backtest():
    """运行带调试日志的回测"""

    print("=" * 70)
    print("带调试日志的回测")
    print("=" * 70)

    # 配置
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # Step 1: 获取数据
    print("\n1️⃣  获取数据...")
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

    # Step 3: 获取 K 线数据并调试
    print("\n3️⃣  获取 K 线数据并调试...")
    klines = {}

    for code in codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            kline = r.unwrap()
            klines[code] = kline

            # 调试信息
            print(f"\n   {code} ({names[code]}):")
            print(f"     日期类型: {type(kline.index[0])}")
            print(f"     日期范围: {kline.index.min()} - {kline.index.max()}")
            print(f"     数据量: {len(kline)} 天")

            if 'close' in kline.columns:
                close = pd.to_numeric(kline['close'], errors='coerce')
                print(f"     最新价格: {close.iloc[-1]:.2f}")
                print(f"     最新涨跌: {close.pct_change().iloc[-1]:.2%}")

    print(f"\n   ✓ K 线数据: {len(klines)} 只股票")

    # Step 4: 运行回测（修改起始日期）
    print("\n4️⃣  运行回测（修改起始日期）...")
    t0 = time.time()

    # 降低入场阈值
    entry_threshold = 40.0

    engine = EnhancedBacktestEngine(
        hold_days=cfg.hold_days,
        max_positions=5,
        commission=0.0003,
        slippage=0.001,
        stamp_tax=0.0005,
        stop_loss_pct=cfg.stop_loss_pct,
        take_profit_pct=cfg.take_profit_pct,
        benchmark_code=cfg.benchmark_code,
        entry_threshold=entry_threshold,
        max_sector_pct=cfg.max_sector_pct,
        use_reversal=False,
        use_alpha=True,
        use_kelly=True,
        backtest_start_date=None,  # 不限制起始日期
    )

    print(f"   入场阈值: {entry_threshold}")
    print(f"   止损: {cfg.stop_loss_pct:.2%}")
    print(f"   止盈: {cfg.take_profit_pct:.2%}")
    print(f"   持仓天数: {cfg.hold_days}")
    print("   起始日期: None（不限制）")

    # 运行回测
    print("\n   运行回测...")
    result = engine.run_portfolio(klines, names)
    t1 = time.time()

    print(f"   ✓ 回测完成 (耗时 {t1 - t0:.2f}s)")

    # Step 5: 显示结果
    print("\n" + "=" * 70)
    print("回测结果（带调试）")
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

    if result.trade_count > 0:
        print("\n✓ 成功产生交易！")
        print("\n📋 交易明细:")
        for i, trade in enumerate(result.trades[:5], 1):
            print(f"   {i}. {trade.code} ({trade.name})")
            print(f"      买入: {trade.entry_date} @ ¥{trade.entry_price:.2f}")
            print(f"      卖出: {trade.exit_date} @ ¥{trade.exit_price:.2f}")
            print(f"      收益: {trade.return_pct:.2%}")
            print(f"      退出: {trade.exit_reason}")
            print()
    else:
        print("\n⚠️  未产生交易")
        print("\n可能原因:")
        print(f"   1. 入场阈值过高（当前: {entry_threshold}）")
        print("   2. 数据覆盖范围不足")
        print("   3. 回测引擎内部条件不满足")

        # 调试：检查入场条件
        print("\n调试信息:")
        print(f"   最高分数: {max(s.hybrid_score for s in top_stocks)}")
        print(f"   最低分数: {min(s.hybrid_score for s in top_stocks)}")
        print(f"   入场阈值: {entry_threshold}")

        if klines:
            sample_code = list(klines.keys())[0]
            sample_kline = klines[sample_code]
            print("\n   数据范围:")
            print(f"     {sample_code}: {sample_kline.index.min()} - {sample_kline.index.max()}")

    # 保存结果
    print("\n5️⃣  保存结果...")
    from aimoon.output import OutputFormatter
    fmt = OutputFormatter(cfg)

    report_path = fmt.export_backtest_report(result, top_stocks, cfg)
    print(f"   ✓ 回测报告: {report_path}")

    print("\n" + "=" * 70)
    print("✓ 调试回测完成")
    print("=" * 70)

    return result


if __name__ == "__main__":
    run_debug_backtest()
