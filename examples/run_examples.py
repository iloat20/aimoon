#!/usr/bin/env python3
"""
Aimoon 项目运行示例

展示如何使用 Aimoon 进行股票筛选、回测和优化
"""

import time

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import filter_universe, get_holdings_pool
from aimoon.data.spot import get_spot
from aimoon.screener import screen_universe


def example_basic_screening():
    """示例 1: 基础股票筛选"""
    print("=" * 60)
    print("📊 示例 1: 基础股票筛选")
    print("=" * 60)

    # 1. 初始化配置
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # 2. 获取持仓池
    print("\n1️⃣  获取机构持仓池...")
    t0 = time.time()
    pool = get_holdings_pool(cfg)
    t1 = time.time()
    print(f"   持仓池: {len(pool)} 只股票 (耗时 {t1 - t0:.2f}s)")

    # 3. 获取实时行情
    print("\n2️⃣  获取实时行情...")
    t0 = time.time()
    spot_result = get_spot(cfg)
    t1 = time.time()

    if spot_result.is_err():
        print(f"   ❌ 获取行情失败: {spot_result.error}")
        return

    spot = spot_result.unwrap()
    print(f"   全市场: {len(spot)} 只股票 (耗时 {t1 - t0:.2f}s)")

    # 4. 过滤股票
    print("\n3️⃣  过滤股票...")
    universe = filter_universe(spot, cfg)
    print(f"   过滤后: {len(universe)} 只股票")

    # 5. 筛选（使用前 50 只进行演示）
    test_universe = universe.head(50)
    print(f"   测试集合: {len(test_universe)} 只股票")

    print("\n4️⃣  开始筛选...")
    t0 = time.time()
    results, tails, _ = screen_universe(test_universe, cfg, cache, use_alpha=False)
    t1 = time.time()
    print(f"   筛选完成: {len(results)} 只股票 (耗时 {t1 - t0:.2f}s)")

    # 6. 显示结果
    if results:
        print("\n5️⃣  筛选结果（前 10 只）:")
        print("-" * 60)
        for i, stock in enumerate(results[:10], 1):
            suggestion, confidence = stock.suggestion
            print(f"{i:2d}. {stock.code} ({stock.name})")
            print(f"    价格: ¥{stock.price:.2f}")
            print(f"    涨跌: {stock.pct_change:+.2f}%")
            print(f"    分数: {stock.total_score}/100")
            print(f"    建议: {suggestion} ({confidence})")
            print()

    return results


def example_advanced_screening():
    """示例 2: 高级筛选（使用 ML 模型）"""
    print("\n" + "=" * 60)
    print("🤖 示例 2: 高级筛选（使用 ML 模型）")
    print("=" * 60)

    cfg = Config()
    cfg.use_alpha = True  # 启用 Alpha Zoo 因子
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    print("\n1️⃣  获取数据...")
    spot_result = get_spot(cfg)
    if spot_result.is_err():
        print(f"   ❌ 获取行情失败: {spot_result.error}")
        return

    spot = spot_result.unwrap()
    universe = filter_universe(spot, cfg)
    test_universe = universe.head(30)

    print(f"   测试集合: {len(test_universe)} 只股票")

    print("\n2️⃣  使用 ML 模型筛选...")
    t0 = time.time()
    results, tails, _ = screen_universe(test_universe, cfg, cache, use_alpha=True)
    t1 = time.time()
    print(f"   筛选完成: {len(results)} 只股票 (耗时 {t1 - t0:.2f}s)")

    if results:
        print("\n3️⃣  ML 筛选结果（前 5 只）:")
        print("-" * 60)
        for i, stock in enumerate(results[:5], 1):
            print(f"{i}. {stock.code} ({stock.name})")
            print(f"   ML 分数: {stock.ml_score}")
            print(f"   综合分数: {stock.total_score}")
            print(f"   信号数: {len(stock.signals)}")
            print()

    return results


def example_score_analysis():
    """示例 3: 评分分析"""
    print("\n" + "=" * 60)
    print("📈 示例 3: 评分分析")
    print("=" * 60)

    from aimoon.models import Signal
    from aimoon.scoring import hybrid_score

    # 创建示例信号
    signals = [
        Signal("ml_rank", "ml_rank_80(强烈看多)", 24),
        Signal("mom_20d_strong", "20日强动量(+15%)", 3),
        Signal("reversal_oversold", "5日暴跌(-8%)(反弹信号)", 4),
        Signal("alpha_zoo_1", "Alpha Zoo 因子 1", 5),
        Signal("alpha_zoo_2", "Alpha Zoo 因子 2", 3),
    ]

    print("\n1️⃣  信号列表:")
    for signal in signals:
        print(f"   • {signal.name}: {signal.label} ({signal.score:+d}分)")

    # 计算总分
    total_score = hybrid_score(signals)
    print(f"\n2️⃣  综合评分: {total_score}/100")

    # 解释评分
    print("\n3️⃣  评分说明:")
    print("   • 80-100: 强烈买入（高置信度）")
    print("   • 65-79:  买入（中高置信度）")
    print("   • 50-64:  建议买入（中置信度）")
    print("   • 35-49:  观望（低置信度）")
    print("   • 20-34:  谨慎（中置信度）")
    print("   • 10-19:  建议卖出（中高置信度）")
    print("   • 0-9:    强烈卖出（高置信度）")


def example_cache_demo():
    """示例 4: 缓存演示"""
    print("\n" + "=" * 60)
    print("💾 示例 4: 缓存演示")
    print("=" * 60)

    from aimoon.cache_manager import cache_get, cache_set, get_cache

    print("\n1️⃣  使用默认缓存（分层缓存）:")
    cache = get_cache()

    # 设置缓存
    cache.set("demo_key", {"stocks": ["000001", "000002"], "score": 85}, ttl=3600)
    print("   ✓ 设置缓存: demo_key")

    # 获取缓存
    value = cache.get("demo_key")
    if value:
        print(f"   ✓ 获取缓存: {value}")
    else:
        print("   ✗ 缓存未命中")

    print("\n2️⃣  使用内存缓存:")
    cache_set("memory_key", "快速数据", backend="memory")
    value = cache_get("memory_key", backend="memory")
    print(f"   ✓ 内存缓存: {value}")

    print("\n3️⃣  使用文件缓存:")
    cache_set("file_key", "持久数据", backend="file", ttl=7200)
    value = cache_get("file_key", backend="file")
    print(f"   ✓ 文件缓存: {value}")


def example_dependency_injection():
    """示例 5: 依赖注入演示"""
    print("\n" + "=" * 60)
    print("🔧 示例 5: 依赖注入演示")
    print("=" * 60)

    from aimoon.dependency_injection import (
        Services,
        get_service,
        register_service,
    )

    print("\n1️⃣  获取预定义服务:")
    try:
        cache = get_service(Services.CACHE)
        config = get_service(Services.CONFIG)
        print(f"   ✓ 缓存服务: {type(cache).__name__}")
        print(f"   ✓ 配置服务: {type(config).__name__}")
    except KeyError as e:
        print(f"   ✗ 服务未找到: {e}")

    print("\n2️⃣  注册自定义服务:")

    class MyService:
        def process(self, data):
            return f"处理: {data}"

    register_service("my_service", MyService())
    my_service = get_service("my_service")
    result = my_service.process("测试数据")
    print(f"   ✓ 自定义服务: {result}")


def main():
    """主函数 - 运行所有示例"""
    print("🚀 Aimoon 项目运行示例")
    print("=" * 60)

    try:
        # 示例 1: 基础筛选
        results = example_basic_screening()

        # 示例 2: 高级筛选（可选，需要 ML 模型）
        # results = example_advanced_screening()

        # 示例 3: 评分分析
        example_score_analysis()

        # 示例 4: 缓存演示
        example_cache_demo()

        # 示例 5: 依赖注入演示
        example_dependency_injection()

        print("\n" + "=" * 60)
        print("✅ 所有示例运行完成!")
        print("=" * 60)

        # 显示总结
        if results:
            print("\n📊 总结:")
            print(f"   • 筛选股票数: {len(results)}")
            print(f"   • 平均分数: {sum(s.total_score for s in results) / len(results):.1f}")
            print(f"   • 最高分数: {max(s.total_score for s in results)}")
            print(f"   • 最低分数: {min(s.total_score for s in results)}")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
