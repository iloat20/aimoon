"""修复数据获取逻辑

将 K 线数据的 date 列设置为 index
"""

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.data.spot import get_spot_for_codes


def fix_data_fetching():
    """修复数据获取逻辑"""

    print("=" * 70)
    print("修复数据获取逻辑")
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
            kline = r.unwrap()

            # 修复：将 date 列设置为 index
            if 'date' in kline.columns:
                try:
                    kline['date'] = pd.to_datetime(kline['date'])
                    kline = kline.set_index('date')
                    print(f"   ✓ {code}: 修复日期格式")
                except Exception as e:
                    print(f"   ✗ {code}: 修复失败 - {e}")
            else:
                print(f"   ⚠ {code}: 没有 date 列")

            klines[code] = kline

    print(f"   ✓ K 线数据: {len(klines)} 只股票")

    # 测试修复后的数据
    print("\n3️⃣  测试修复后的数据...")
    for code in list(klines.keys())[:3]:
        kline = klines[code]
        print(f"\n   {code}:")
        print(f"     日期类型: {type(kline.index[0])}")
        print(f"     日期范围: {kline.index.min()} - {kline.index.max()}")
        print(f"     数据量: {len(kline)} 天")

        if 'close' in kline.columns:
            close = pd.to_numeric(kline['close'], errors='coerce')
            print(f"     最新价格: {close.iloc[-1]:.2f}")

            # 计算涨跌
            if len(close) > 1:
                pct_change = (close.iloc[-1] / close.iloc[-2] - 1) * 100
                print(f"     最新涨跌: {pct_change:.2f}%")

    # 保存修复后的数据到缓存
    print("\n4️⃣  保存修复后的数据到缓存...")
    for code, kline in klines.items():
        try:
            cache.put(code, kline)
            print(f"   ✓ {code}: 已保存到缓存")
        except Exception as e:
            print(f"   ✗ {code}: 保存失败 - {e}")

    return klines


def main():
    """主函数"""
    klines = fix_data_fetching()

    if klines:
        print("\n" + "=" * 70)
        print("✓ 数据修复完成")
        print("=" * 70)
        print(f"\n修复了 {len(klines)} 只股票的日期格式")
        print("\n下一步:")
        print("  1. 重新运行回测")
        print("  2. 验证回测结果")


if __name__ == "__main__":
    main()
