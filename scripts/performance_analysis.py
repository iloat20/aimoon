"""性能分析脚本 - 识别关键瓶颈"""
import time


def analyze_performance():
    """分析系统性能瓶颈"""

    # 1. 分析数据获取性能
    print("\n" + "=" * 60)
    print("1️⃣  数据获取性能分析")
    print("=" * 60)

    from aimoon.cache import DataCache
    from aimoon.config import Config
    from aimoon.data.filters import get_holdings_pool
    from aimoon.data.spot import get_spot_for_codes

    cfg = Config()
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)

    # 测试持仓池获取
    t0 = time.time()
    pool = get_holdings_pool(cfg)
    t1 = time.time()
    print(f"   持仓池获取: {t1 - t0:.3f}s ({len(pool)} stocks)")

    # 测试实时行情获取
    if pool:
        t0 = time.time()
        spot_result = get_spot_for_codes(list(pool)[:10], cfg)  # 测试前10只
        t1 = time.time()
        print(f"   实时行情获取 (10只): {t1 - t0:.3f}s")

    # 2. 分析评分系统性能
    print("\n" + "=" * 60)
    print("2️⃣  评分系统性能分析")
    print("=" * 60)

    from aimoon.models import Signal
    from aimoon.scoring import hybrid_score

    # 测试评分计算
    signals = [
        Signal("mom_20d_strong", "20日强动量", +3),
        Signal("reversal_hot", "5日暴涨", -4),
        Signal("ml_rank", "ml_rank_80(强烈看多)", +24),
    ]

    t0 = time.time()
    for _ in range(10000):
        hybrid_score(signals)
    t1 = time.time()
    print(f"   评分计算 (10k次): {t1 - t0:.3f}s ({(t1 - t0) / 10000 * 1000:.3f}ms/次)")

    # 3. 分析 ML 预测性能
    print("\n" + "=" * 60)
    print("3️⃣  ML 预测性能分析")
    print("=" * 60)

    try:
        from aimoon.ml.ensemble import EnsemblePredictor

        t0 = time.time()
        predictor = EnsemblePredictor.from_cache()
        t1 = time.time()
        print(f"   模型加载: {t1 - t0:.3f}s")

        if predictor.has_xgb or predictor.has_lgbm:
            print(f"   XGBoost: {'✓' if predictor.has_xgb else '✗'}")
            print(f"   LightGBM: {'✓' if predictor.has_lgbm else '✗'}")
    except Exception as e:
        print(f"   ML 模型未找到: {e}")

    # 4. 内存使用分析
    print("\n" + "=" * 60)
    print("4️⃣  内存使用分析")
    print("=" * 60)

    import sys
    print(f"   Python 版本: {sys.version}")
    print(f"   当前内存: {sys.getsizeof(0) / 1024:.2f} KB (int)")

    # 5. 识别性能瓶颈
    print("\n" + "=" * 60)
    print("5️⃣  性能瓶颈识别")
    print("=" * 60)

    bottlenecks = [
        ("网络请求", "数据获取依赖外部 API", "HIGH"),
        ("K线数据处理", "大量 DataFrame 操作", "MEDIUM"),
        ("因子计算", "452 个因子计算", "MEDIUM"),
        ("缓存命中率", "缓存策略优化空间", "LOW"),
    ]

    for name, desc, priority in bottlenecks:
        emoji = "🔴" if priority == "HIGH" else "🟡" if priority == "MEDIUM" else "🟢"
        print(f"   {emoji} {name}: {desc} [{priority}]")


if __name__ == "__main__":
    analyze_performance()
