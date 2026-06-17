"""自适应策略使用示例

展示如何使用：
1. 自适应策略（基于 EnhancedMarketRegime）
2. 市场状态检测
3. 自适应止损止盈
"""

from __future__ import annotations

import pandas as pd

from aimoon.adaptive_strategy import (
    apply_adaptive_strategy,
    create_adaptive_strategy,
    log_adaptive_strategy,
)
from aimoon.regime_enhanced import EnhancedMarketRegime, RegimeScore, detect_enhanced_regime


def example_adaptive_strategy():
    """示例 1: 基于 regime 的自适应策略"""

    print("=" * 70)
    print("示例 1: 自适应策略")
    print("=" * 70)

    # 模拟一个 EnhancedMarketRegime
    regime = EnhancedMarketRegime(
        state="bull",
        confidence=0.85,
        scores=RegimeScore(
            volatility=0.3,
            trend=0.7,
            momentum=0.6,
            sentiment=0.5,
            structure=0.8,
        ),
        details={"atr_pct": 0.02, "ma_alignment": 2},
        transition_prob={"bull": 0.7, "sideways": 0.2, "bear": 0.1},
    )

    # 创建自适应策略
    strategy = create_adaptive_strategy(
        regime,
        base_stop_loss=0.04,
        base_take_profit=0.15,
        base_rebalance_freq=3,
    )

    # 记录策略
    log_adaptive_strategy(strategy)

    print(f"\n  市场状态: {regime.state} (置信度: {regime.confidence:.0%})")
    print(f"  止损: {strategy.stop_loss_pct:.2%}")
    print(f"  止盈: {strategy.take_profit_pct:.2%}")
    print(f"  调仓频率: {strategy.rebalance_freq} 天")
    print(f"  最小持仓: {strategy.min_hold_days} 天")
    print(f"  仓位比例: {strategy.position_scale:.0%}")

    return strategy


def example_regime_detection():
    """示例 2: 市场状态检测"""

    print("\n" + "=" * 70)
    print("示例 2: 市场状态检测")
    print("=" * 70)

    # 生成模拟 K 线数据
    n = 120
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(
        100.0 + (pd.Series(range(n)).astype(float) * 0.1).values
        + pd.Series([0] * n).values,
        index=dates,
    )
    high = close * 1.02
    low = close * 0.98
    volume = pd.Series([1e6] * n, index=dates)

    kline = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )

    # 检测市场状态
    regime = detect_enhanced_regime(kline, lookback=120)

    print(f"\n  市场状态: {regime.state}")
    print(f"  置信度: {regime.confidence:.0%}")
    print(f"  波动率得分: {regime.scores.volatility:.2f}")
    print(f"  趋势得分: {regime.scores.trend:.2f}")
    print(f"  动量得分: {regime.scores.momentum:.2f}")
    print(f"  仓位比例: {regime.position_scale:.0%}")
    print(f"  是否趋势: {regime.is_trending}")
    print(f"  是否高风险: {regime.is_risky}")

    return regime


def example_apply_strategy():
    """示例 3: 应用自适应策略到持仓"""

    print("\n" + "=" * 70)
    print("示例 3: 应用自适应策略到持仓")
    print("=" * 70)

    regime = EnhancedMarketRegime(
        state="high_volatility",
        confidence=0.75,
        scores=RegimeScore(
            volatility=0.8,
            trend=0.2,
            momentum=0.3,
            sentiment=0.6,
            structure=0.4,
        ),
        details={"atr_pct": 0.04},
        transition_prob={"high_volatility": 0.5, "bear": 0.3, "sideways": 0.2},
    )

    strategy = create_adaptive_strategy(regime, base_stop_loss=0.04)

    # 模拟持仓
    positions = {
        "600519": {
            "entry_price": 1800.0,
            "quantity": 100,
            "entry_date": pd.Timestamp("2024-01-01"),
        },
        "000858": {
            "entry_price": 150.0,
            "quantity": 500,
            "entry_date": pd.Timestamp("2024-01-01"),
        },
    }

    # 模拟 K 线数据
    klines = {
        "600519": pd.DataFrame(
            {"close": [1800.0, 1850.0, 1900.0]},
            index=pd.date_range("2024-01-01", periods=3, freq="B"),
        ),
        "000858": pd.DataFrame(
            {"close": [150.0, 145.0, 140.0]},
            index=pd.date_range("2024-01-01", periods=3, freq="B"),
        ),
    }

    bar_date = pd.Timestamp("2024-01-03")
    updated = apply_adaptive_strategy(strategy, positions, klines, bar_date)

    print(f"\n  市场状态: {regime.state}")
    print(f"  止损比例: {strategy.stop_loss_pct:.2%}")
    print(f"  止盈比例: {strategy.take_profit_pct:.2%}")
    for code, pos in updated.items():
        print(f"  {code}: stop_loss={pos['stop_loss']:.2%}, take_profit={pos['take_profit']:.2%}")


def main():
    """主函数"""

    print("自适应策略使用示例")
    print("=" * 70)

    try:
        example_adaptive_strategy()
        example_regime_detection()
        example_apply_strategy()

        print("\n" + "=" * 70)
        print("所有示例运行完成")
        print("=" * 70)

    except Exception as e:
        print(f"\n运行失败: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
