"""回测引擎调试脚本

检查为什么没有产生交易
"""

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.data.spot import get_spot_for_codes
from aimoon.enhanced_backtest import EnhancedBacktestEngine
from aimoon.scoring.rps import compute_rps
from aimoon.screener import screen_universe


def debug_backtest():
    """调试回测引擎"""

    print("=" * 70)
    print("回测引擎调试")
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

    # Step 3: 获取 K 线数据
    print("\n3️⃣  获取 K 线数据...")
    klines = {}

    for code in codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()

    print(f"   ✓ K 线数据: {len(klines)} 只股票")

    # Step 4: 检查 K 线数据详情
    print("\n4️⃣  检查 K 线数据详情...")
    for code in codes[:3]:
        if code in klines:
            kline = klines[code]
            print(f"\n   {code} ({names[code]}):")
            print(f"     数据量: {len(kline)} 行")
            print(f"     列名: {list(kline.columns)}")
            print(f"     日期类型: {type(kline.index[0])}")
            print(f"     日期示例: {kline.index[:3].tolist()}")

            if 'close' in kline.columns:
                close = pd.to_numeric(kline['close'], errors='coerce')
                print(f"     最新价格: {close.iloc[-1]:.2f}")
                print(f"     最新涨跌: {close.pct_change().iloc[-1]:.2%}")

    # Step 5: 测试回测引擎
    print("\n5️⃣  测试回测引擎...")

    # 创建引擎（降低所有阈值）
    engine = EnhancedBacktestEngine(
        hold_days=15,
        max_positions=5,
        commission=0.0003,
        slippage=0.001,
        stamp_tax=0.0005,
        stop_loss_pct=0.05,
        take_profit_pct=0.30,
        benchmark_code=cfg.benchmark_code,
        entry_threshold=30,  # 大幅降低阈值
        max_sector_pct=0.30,
        use_reversal=False,
        use_alpha=True,
        use_kelly=True,
        backtest_start_date=None,  # 不限制起始日期
    )

    print(f"   入场阈值: {engine.entry_threshold}")
    print(f"   止损: {engine.stop_loss_pct:.2%}")
    print(f"   止盈: {engine.take_profit_pct:.2%}")

    # 运行回测
    print("\n   运行回测...")
    result = engine.run_portfolio(klines, names)

    print("\n   回测结果:")
    print(f"     总收益: {result.total_return:.2%}")
    print(f"     交易次数: {result.trade_count}")

    if result.trade_count > 0:
        print("\n   ✓ 成功产生交易！")
        for i, trade in enumerate(result.trades[:3], 1):
            print(f"     {i}. {trade.code} ({trade.name})")
            print(f"        买入: {trade.entry_date} @ ¥{trade.entry_price:.2f}")
            print(f"        卖出: {trade.exit_date} @ ¥{trade.exit_price:.2f}")
            print(f"        收益: {trade.return_pct:.2%}")
    else:
        print("\n   ✗ 未产生交易")
        print("\n   可能原因:")
        print("   1. 入场条件不满足")
        print("   2. 数据覆盖范围不足")
        print("   3. 回测时间范围问题")

        # 检查入场条件
        print("\n   检查入场条件:")
        print(f"     入场阈值: {engine.entry_threshold}")
        print(f"     最高分数: {max(s.hybrid_score for s in top_stocks)}")
        print(f"     最低分数: {min(s.hybrid_score for s in top_stocks)}")

        # 检查数据覆盖范围
        if klines:
            sample_code = list(klines.keys())[0]
            sample_kline = klines[sample_code]
            print("\n   数据覆盖范围:")
            print(f"     {sample_code}: {len(sample_kline)} 天")
            print(f"     日期范围: {sample_kline.index.min()} - {sample_kline.index.max()}")


def main():
    """主函数"""
    debug_backtest()


if __name__ == "__main__":
    main()
