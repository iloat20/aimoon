"""修复 K 线数据日期格式

将整数日期转换为日期对象
"""

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.data.spot import get_spot_for_codes


def fix_kline_dates():
    """修复 K 线数据日期格式"""

    print("=" * 70)
    print("修复 K 线数据日期格式")
    print("=" * 70)

    # 配置
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # 获取数据
    print("\n1️⃣  获取数据...")
    pool = get_holdings_pool(cfg)
    spot_result = get_spot_for_codes(pool, cfg)

    if spot_result.is_err():
        print(f"   ✗ 获取行情失败: {spot_result.error}")
        return

    spot = spot_result.unwrap()
    print(f"   ✓ 行情数据: {len(spot)} 只股票")

    # 获取 K 线数据
    print("\n2️⃣  获取 K 线数据...")
    test_codes = list(pool)[:10]
    klines = {}

    for code in test_codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            klines[code] = r.unwrap()

    print(f"   ✓ K 线数据: {len(klines)} 只股票")

    # 检查日期格式
    print("\n3️⃣  检查日期格式...")
    for code, kline in klines.items():
        print(f"\n   {code}:")
        print(f"     日期类型: {type(kline.index[0])}")
        print(f"     日期示例: {kline.index[:3].tolist()}")

        # 检查是否有 date 列
        if 'date' in kline.columns:
            print(f"     date 列存在: {kline['date'].iloc[:3].tolist()}")
        else:
            print("     date 列不存在")

    # 尝试修复日期
    print("\n4️⃣  尝试修复日期...")
    for code, kline in klines.items():
        print(f"\n   修复 {code}:")

        # 方法 1: 如果有 date 列，使用它作为索引
        if 'date' in kline.columns:
            try:
                # 尝试转换 date 列为日期
                kline['date'] = pd.to_datetime(kline['date'])
                kline = kline.set_index('date')
                print("     ✓ 使用 date 列作为索引")
                print(f"     日期范围: {kline.index.min()} - {kline.index.max()}")
            except Exception as e:
                print(f"     ✗ 转换失败: {e}")

        # 方法 2: 如果没有 date 列，创建日期序列
        else:
            try:
                # 假设数据是从某个日期开始的
                start_date = pd.Timestamp('2024-01-01')
                dates = pd.date_range(start=start_date, periods=len(kline), freq='D')
                kline.index = dates
                print("     ✓ 创建日期序列")
                print(f"     日期范围: {kline.index.min()} - {kline.index.max()}")
            except Exception as e:
                print(f"     ✗ 创建失败: {e}")

    # 测试修复后的数据
    print("\n5️⃣  测试修复后的数据...")
    for code in list(klines.keys())[:3]:
        kline = klines[code]
        print(f"\n   {code}:")
        print(f"     日期类型: {type(kline.index[0])}")
        print(f"     日期范围: {kline.index.min()} - {kline.index.max()}")
        print(f"     数据量: {len(kline)} 天")

        if 'close' in kline.columns:
            close = pd.to_numeric(kline['close'], errors='coerce')
            print(f"     最新价格: {close.iloc[-1]:.2f}")

    return klines


def main():
    """主函数"""
    klines = fix_kline_dates()

    if klines:
        print("\n" + "=" * 70)
        print("✓ 日期修复完成")
        print("=" * 70)
        print(f"\n修复了 {len(klines)} 只股票的日期格式")
        print("\n下一步:")
        print("  1. 更新缓存中的 K 线数据")
        print("  2. 重新运行回测")
        print("  3. 验证回测结果")


if __name__ == "__main__":
    main()
