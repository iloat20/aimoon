"""数据质量诊断脚本

检查 K 线数据、行情数据、持仓池等数据质量
"""

import pandas as pd

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.history import get_kline
from aimoon.data.spot import get_spot_for_codes


def diagnose_data_quality():
    """诊断数据质量"""

    print("=" * 70)
    print("数据质量诊断")
    print("=" * 70)

    # 配置
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # Step 1: 检查持仓池
    print("\n1️⃣  检查持仓池...")
    pool = get_holdings_pool(cfg)
    print(f"   ✓ 持仓池: {len(pool)} 只股票")
    print(f"   示例: {list(pool)[:5]}")

    # Step 2: 检查行情数据
    print("\n2️⃣  检查行情数据...")
    spot_result = get_spot_for_codes(pool, cfg)

    if spot_result.is_err():
        print(f"   ✗ 获取行情失败: {spot_result.error}")
        return

    spot = spot_result.unwrap()
    print(f"   ✓ 行情数据: {len(spot)} 只股票")

    # 检查行情数据列
    print(f"   列名: {list(spot.columns)[:10]}...")

    # 检查价格数据
    if 'price' in spot.columns:
        prices = pd.to_numeric(spot['price'], errors='coerce')
        print(f"   价格范围: {prices.min():.2f} - {prices.max():.2f}")
        print(f"   价格均值: {prices.mean():.2f}")

    # Step 3: 检查 K 线数据
    print("\n3️⃣  检查 K 线数据...")
    test_codes = list(pool)[:5]

    for code in test_codes:
        r = get_kline(code, cfg.history_days, cache)

        if r.is_ok():
            kline = r.unwrap()
            print(f"\n   {code}:")
            print(f"     数据量: {len(kline)} 天")
            print(f"     日期范围: {kline.index.min()} - {kline.index.max()}")

            if 'close' in kline.columns:
                close = pd.to_numeric(kline['close'], errors='coerce')
                print(f"     最新价格: {close.iloc[-1]:.2f}")

                # 计算涨跌
                if len(close) > 1:
                    pct_change = (close.iloc[-1] / close.iloc[-2] - 1) * 100
                    print(f"     最新涨跌: {pct_change:.2f}%")

                    # 检查是否有历史数据
                    if len(close) > 60:
                        pct_60d = (close.iloc[-1] / close.iloc[-60] - 1) * 100
                        print(f"     60日涨跌: {pct_60d:.2f}%")
                    else:
                        print("     ⚠ 历史数据不足 60 天")
            else:
                print("     ⚠ 缺少 close 列")
        else:
            print(f"\n   {code}: ✗ 获取失败 - {r.error}")

    # Step 4: 检查数据一致性
    print("\n4️⃣  检查数据一致性...")
    print(f"   持仓池大小: {len(pool)}")
    print(f"   行情数据大小: {len(spot)}")

    # 检查持仓池和行情数据的交集
    if 'stock_code' in spot.columns:
        spot_codes = set(spot['stock_code'].astype(str))
        pool_set = set(pool)
        common = pool_set & spot_codes
        print(f"   交集大小: {len(common)}")
        print(f"   持仓池不在行情中: {len(pool_set - spot_codes)}")
        print(f"   行情不在持仓池中: {len(spot_codes - pool_set)}")

    # Step 5: 诊断问题
    print("\n5️⃣  诊断问题...")
    print("-" * 70)

    problems = []

    # 检查 1: K 线数据长度
    for code in test_codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            kline = r.unwrap()
            if len(kline) < 60:
                problems.append(f"K 线数据不足 60 天: {code} ({len(kline)} 天)")

    # 检查 2: 价格数据异常
    for code in test_codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            kline = r.unwrap()
            if 'close' in kline.columns:
                close = pd.to_numeric(kline['close'], errors='coerce')
                if close.isna().all():
                    problems.append(f"价格数据全为空: {code}")
                elif close.std() == 0:
                    problems.append(f"价格数据无波动: {code}")

    # 检查 3: 涨跌数据异常
    for code in test_codes:
        r = get_kline(code, cfg.history_days, cache)
        if r.is_ok():
            kline = r.unwrap()
            if 'close' in kline.columns and len(kline) > 1:
                close = pd.to_numeric(kline['close'], errors='coerce')
                pct_changes = close.pct_change().dropna()
                if (pct_changes == 0).all():
                    problems.append(f"涨跌数据全为 0: {code}")

    if problems:
        print("   发现问题:")
        for i, problem in enumerate(problems, 1):
            print(f"   {i}. {problem}")
    else:
        print("   ✓ 未发现明显问题")

    # Step 6: 建议
    print("\n6️⃣  建议...")
    print("-" * 70)

    if problems:
        print("   建议操作:")
        print("   1. 清除缓存: python -m aimoon cache clear")
        print("   2. 重新获取数据: python -m aimoon update")
        print("   3. 检查网络连接")
        print("   4. 尝试其他数据源")
    else:
        print("   数据质量正常，问题可能在回测引擎")

    return problems


def fix_data_issues():
    """修复数据问题"""

    print("\n" + "=" * 70)
    print("修复数据问题")
    print("=" * 70)

    # 配置
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # Step 1: 清除缓存
    print("\n1️⃣  清除缓存...")
    try:
        cleared = cache.clear()
        print(f"   ✓ 清除了 {cleared} 个缓存文件")
    except Exception as e:
        print(f"   ✗ 清除缓存失败: {e}")

    # Step 2: 重新获取持仓池
    print("\n2️⃣  重新获取持仓池...")
    try:
        pool = get_holdings_pool(cfg, force=True)
        print(f"   ✓ 持仓池: {len(pool)} 只股票")
    except Exception as e:
        print(f"   ✗ 获取持仓池失败: {e}")
        return

    # Step 3: 重新获取行情数据
    print("\n3️⃣  重新获取行情数据...")
    try:
        spot_result = get_spot_for_codes(pool, cfg)
        if spot_result.is_ok():
            spot = spot_result.unwrap()
            print(f"   ✓ 行情数据: {len(spot)} 只股票")
        else:
            print(f"   ✗ 获取行情失败: {spot_result.error}")
            return
    except Exception as e:
        print(f"   ✗ 获取行情失败: {e}")
        return

    # Step 4: 测试 K 线数据
    print("\n4️⃣  测试 K 线数据...")
    test_codes = list(pool)[:5]

    for code in test_codes:
        try:
            r = get_kline(code, cfg.history_days, cache)
            if r.is_ok():
                kline = r.unwrap()
                print(f"   ✓ {code}: {len(kline)} 天数据")
            else:
                print(f"   ✗ {code}: {r.error}")
        except Exception as e:
            print(f"   ✗ {code}: {e}")

    print("\n" + "=" * 70)
    print("✓ 数据修复完成")
    print("=" * 70)


def main():
    """主函数"""

    print("数据质量诊断工具")
    print("=" * 70)

    # 诊断问题
    problems = diagnose_data_quality()

    # 如果有问题，尝试修复
    if problems:
        print("\n" + "=" * 70)
        print("发现问题，尝试修复...")
        print("=" * 70)
        fix_data_issues()

        # 再次诊断
        print("\n" + "=" * 70)
        print("再次诊断...")
        print("=" * 70)
        diagnose_data_quality()

    print("\n" + "=" * 70)
    print("诊断完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
