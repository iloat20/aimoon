"""强制重新获取所有数据并修复日期格式"""


import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.data.spot import get_spot_for_codes


def force_refetch_all_data():
    """强制重新获取所有数据"""

    print("=" * 70)
    print("强制重新获取所有数据")
    print("=" * 70)

    # 配置
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # Step 1: 清除所有缓存
    print("\n1️⃣  清除所有缓存...")
    cleared = cache.clear()
    print(f"   ✓ 清除了 {cleared} 个缓存文件")

    # Step 2: 获取持仓池
    print("\n2️⃣  获取持仓池...")
    pool = get_holdings_pool(cfg, force=True)
    print(f"   ✓ 持仓池: {len(pool)} 只股票")

    # Step 3: 获取行情数据
    print("\n3️⃣  获取行情数据...")
    spot_result = get_spot_for_codes(pool, cfg)

    if spot_result.is_err():
        print(f"   ✗ 获取行情失败: {spot_result.error}")
        return

    spot = spot_result.unwrap()
    print(f"   ✓ 行情数据: {len(spot)} 只股票")

    # Step 4: 强制重新获取 K 线数据
    print("\n4️⃣  强制重新获取 K 线数据...")
    test_codes = list(pool)[:20]  # 获取前 20 只
    klines = {}
    success_count = 0
    fail_count = 0

    for i, code in enumerate(test_codes, 1):
        print(f"\n   [{i}/{len(test_codes)}] 获取 {code}...")

        # 强制重新获取（不使用缓存）
        try:
            r = get_kline(code, cfg.history_days, cache)
            if r.is_ok():
                kline = r.unwrap()

                # 检查日期格式
                if isinstance(kline.index[0], int):
                    print(f"     ⚠ 日期是整数: {kline.index[0]}")
                    print("     尝试修复...")

                    # 修复日期格式
                    if 'date' in kline.columns:
                        try:
                            kline['date'] = pd.to_datetime(kline['date'])
                            kline = kline.set_index('date')
                            print("     ✓ 修复成功")
                        except Exception as e:
                            print(f"     ✗ 修复失败: {e}")
                    else:
                        print("     ✗ 没有 date 列")
                else:
                    print(f"     ✓ 日期格式正确: {type(kline.index[0])}")

                # 保存到缓存
                cache.put(code, kline)
                klines[code] = kline
                success_count += 1

                print(f"     数据量: {len(kline)} 天")
                print(f"     日期范围: {kline.index.min()} - {kline.index.max()}")

                if 'close' in kline.columns:
                    close = pd.to_numeric(kline['close'], errors='coerce')
                    print(f"     最新价格: {close.iloc[-1]:.2f}")
                    print(f"     最新涨跌: {close.pct_change().iloc[-1]:.2%}")
            else:
                print(f"     ✗ 获取失败: {r.error}")
                fail_count += 1

        except Exception as e:
            print(f"     ✗ 异常: {e}")
            fail_count += 1

    # Step 5: 验证结果
    print("\n5️⃣  验证结果...")
    print(f"   ✓ 成功: {success_count} 只股票")
    print(f"   ✗ 失败: {fail_count} 只股票")

    if klines:
        print("\n   验证日期格式:")
        for code, kline in list(klines.items())[:5]:
            print(f"     {code}: {type(kline.index[0])} - {kline.index.min()} - {kline.index.max()}")

    print("\n" + "=" * 70)
    print("✓ 数据重新获取完成")
    print("=" * 70)

    return klines


def main():
    """主函数"""
    klines = force_refetch_all_data()

    if klines:
        print(f"\n成功获取 {len(klines)} 只股票的数据")
        print("\n下一步:")
        print("  1. 运行回测验证")
        print("  2. python scripts/debug_backtest_final.py")


if __name__ == "__main__":
    main()
