"""评分参数调优脚本

测试不同的权重组合，找到最优参数
"""

import time

from aimoon.cache import DataCache
from aimoon.config import Config
from aimoon.data.filters import get_holdings_pool
from aimoon.data.spot import get_spot_for_codes
from aimoon.scoring import hybrid_score
from aimoon.scoring.hybrid_scorer import HybridScoreConfig
from aimoon.screener import screen_universe


def test_weight_combinations():
    """测试不同的权重组合"""

    print("=" * 70)
    print("评分参数调优 - 测试不同权重组合")
    print("=" * 70)

    # 配置
    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # 获取数据
    print("\n1️⃣  获取数据...")
    pool = get_holdings_pool(cfg)
    print(f"   持仓池: {len(pool)} 只股票")

    spot_result = get_spot_for_codes(pool, cfg)
    if spot_result.is_err():
        print(f"   ✗ 获取行情失败: {spot_result.error}")
        return

    spot = spot_result.unwrap()
    print(f"   行情数据: {len(spot)} 只股票")

    # 测试不同的权重组合
    weight_combinations = [
        # (ML权重, Alpha权重, 动量权重)
        (0.40, 0.40, 0.20),  # 默认
        (0.50, 0.30, 0.20),  # 重视 ML
        (0.30, 0.50, 0.20),  # 重视 Alpha
        (0.40, 0.30, 0.30),  # 重视动量
        (0.45, 0.35, 0.20),  # 平衡型
        (0.35, 0.45, 0.20),  # Alpha 优先
    ]

    print("\n2️⃣  测试权重组合...")
    results = []

    for ml_w, alpha_w, mom_w in weight_combinations:
        print(f"\n   测试: ML={ml_w:.2f}, Alpha={alpha_w:.2f}, Mom={mom_w:.2f}")

        # 创建配置
        config = HybridScoreConfig(
            ml_weight=ml_w,
            alpha_weight=alpha_w,
            momentum_weight=mom_w,
        )

        # 筛选股票
        t0 = time.time()
        stocks, tails = screen_universe(spot, cfg, cache, use_alpha=True)
        t1 = time.time()

        if stocks:
            # 计算前 10 只股票的分数
            top_stocks = sorted(stocks, key=lambda s: hybrid_score(list(s.signals), config), reverse=True)[:10]
            avg_score = sum(hybrid_score(list(s.signals), config) for s in top_stocks) / len(top_stocks)
            max_score = max(hybrid_score(list(s.signals), config) for s in top_stocks)
            min_score = min(hybrid_score(list(s.signals), config) for s in top_stocks)

            results.append({
                'weights': (ml_w, alpha_w, mom_w),
                'count': len(stocks),
                'avg_score': avg_score,
                'max_score': max_score,
                'min_score': min_score,
                'time': t1 - t0,
            })

            print(f"      ✓ 筛选: {len(stocks)} 只, 耗时: {t1 - t0:.2f}s")
            print(f"      ✓ 平均分: {avg_score:.1f}, 最高: {max_score}, 最低: {min_score}")
        else:
            print("      ✗ 未筛选到股票")

    # 显示结果对比
    print("\n" + "=" * 70)
    print("结果对比")
    print("=" * 70)

    print("\n权重组合对比:")
    print("-" * 70)
    print(f"{'ML权重':<10} {'Alpha权重':<12} {'动量权重':<10} {'平均分':<10} {'最高分':<10} {'最低分':<10}")
    print("-" * 70)

    for r in results:
        ml_w, alpha_w, mom_w = r['weights']
        print(f"{ml_w:<10.2f} {alpha_w:<12.2f} {mom_w:<10.2f} {r['avg_score']:<10.1f} {r['max_score']:<10} {r['min_score']:<10}")

    # 推荐最优组合
    if results:
        best = max(results, key=lambda x: x['avg_score'])
        ml_w, alpha_w, mom_w = best['weights']
        print("\n" + "=" * 70)
        print("推荐权重组合")
        print("=" * 70)
        print(f"   ML 权重: {ml_w:.2f}")
        print(f"   Alpha 权重: {alpha_w:.2f}")
        print(f"   动量权重: {mom_w:.2f}")
        print(f"   平均分: {best['avg_score']:.1f}")
        print(f"   最高分: {best['max_score']}")
        print(f"   最低分: {best['min_score']}")

    return results


def test_threshold_sensitivity():
    """测试阈值敏感性"""

    print("\n" + "=" * 70)
    print("阈值敏感性测试")
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
    print(f"   行情数据: {len(spot)} 只股票")

    # 筛选股票
    print("\n2️⃣  筛选股票...")
    stocks, tails = screen_universe(spot, cfg, cache, use_alpha=True)
    print(f"   ✓ 筛选完成: {len(stocks)} 只股票")

    if not stocks:
        print("   ✗ 未筛选到股票")
        return

    # 测试不同的入场阈值
    thresholds = [50, 55, 60, 65, 70, 75, 80]

    print("\n3️⃣  测试入场阈值...")
    print("-" * 70)
    print(f"{'阈值':<10} {'通过数量':<12} {'通过率':<10} {'平均分':<10}")
    print("-" * 70)

    for threshold in thresholds:
        passed = [s for s in stocks if hybrid_score(list(s.signals)) >= threshold]
        pass_rate = len(passed) / len(stocks) * 100 if stocks else 0
        avg_score = sum(hybrid_score(list(s.signals)) for s in passed) / len(passed) if passed else 0

        print(f"{threshold:<10} {len(passed):<12} {pass_rate:<10.1f}% {avg_score:<10.1f}")

    # 推荐阈值
    print("\n" + "=" * 70)
    print("推荐阈值")
    print("=" * 70)
    print("   建议入场阈值: 55-65（平衡风险和收益）")
    print("   保守策略: 65-75（高分股票）")
    print("   激进策略: 50-55（更多机会）")


def main():
    """主函数"""

    print("评分参数调优工具")
    print("=" * 70)

    # 测试权重组合
    results = test_weight_combinations()

    # 测试阈值敏感性
    test_threshold_sensitivity()

    print("\n" + "=" * 70)
    print("调优完成！")
    print("=" * 70)
    print("\n建议:")
    print("  1. 根据权重组合测试结果选择最优权重")
    print("  2. 根据阈值敏感性测试调整入场阈值")
    print("  3. 运行回测验证参数效果")
    print("  4. 根据回测结果进一步调优")


if __name__ == "__main__":
    main()
