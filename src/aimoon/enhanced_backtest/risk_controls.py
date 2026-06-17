"""Risk control parameters and utilities.

Trailing stop tiers, regime-aware take-profit levels, and ATR calculations.
"""

from __future__ import annotations

import pandas as pd

from aimoon.indicators.technical import TechInd

# ── Trailing stop 参数 ──
TRAILING_STOP_TIERS: tuple[tuple[float, float], ...] = (
    (0.05, 0.00),  # +5% PnL: 保本保护（止损归零）
    (0.10, 0.60),  # +10% PnL: 锁定峰值利润的 60%（从50%提高，让利润奔跑）
    (0.15, 0.50),  # +15% PnL: 锁定峰值利润的 50%（从40%提高）
    (0.20, 0.40),  # +20% PnL: 锁定峰值利润的 40%（从30%提高）
)

# ── 硬止损上限 ──
HARD_LOSS_CAP: float = 0.05  # 单笔最大亏损 5%（进一步收紧止损，小亏）

# ── 利润保护参数 ──
PROFIT_PROTECTION_PEAK_THRESHOLD: float = (
    0.12  # 峰值利润 >= 12% 时启用（从8%提高，进一步避免过早保护）
)
PROFIT_PROTECTION_FLOOR: float = 0.05  # 当前利润 <= 5% 时触发（从3%提高，更宽松）

# ── 时间衰减参数 ──
TIME_DECAY_IDLE_DAYS: int = 25  # 持仓超过 25 天且利润 < 1% 视为"死钱"
TIME_DECAY_LOSS_DAYS: int = 12  # 持仓超过 12 天仍在亏损时收紧止损（从15天收紧）
TIME_DECAY_TIGHTEN_RATIO: float = 0.80  # 收紧后的止损为原始止损的 80%

# ── Chandelier Exit 参数 ──
CHANDELIER_ATR_MULTIPLIER: float = 2.0  # ATR 倍数（初始止损=买入价-2×ATR，移动止损=峰值-2×ATR）

# ── ATR 动态止损止盈参数 ──
STOP_LOSS_ATR_MULTIPLIER: float = 1.0  # 止损 ATR 倍数
TAKE_PROFIT_ATR_MULTIPLIER: float = 4.0  # 止盈 ATR 倍数
_MIN_STOP_LOSS_PCT: float = 0.03  # 最小止损（3%，低波动保护）
_MAX_STOP_LOSS_PCT: float = 0.07  # 最大止损（7%，高波动保护）
_MIN_TAKE_PROFIT_PCT: float = 0.12  # 最小止盈（12%，低波动保底收益）
_MAX_TAKE_PROFIT_PCT: float = 0.30  # 最大止盈（30%，高波动利润保护）

# ── 回撤控制阈值 ──
DD_THRESHOLDS: tuple[tuple[float, float], ...] = (
    (0.05, 0.75),  # DD > 5%: 75% 仓位
    (0.07, 0.50),  # DD > 7%: 50% 仓位
    (0.10, 0.25),  # DD > 10%: 25% 仓位
)

# ── Regime-based take-profit levels ──
REGIME_TAKE_PROFIT: dict[str, float] = {
    "bull": 0.20,  # 20%: let winners run in bull markets
    "sideways": 0.15,  # 15%: moderate target in range-bound markets
    "bear": 0.08,  # 8%: tight target in bear markets
    "high_volatility": 0.18,  # 18%: balanced in volatile conditions
    "crisis": 0.05,  # 5%: 危机模式快速止盈
}


def get_atr_value(kline: pd.DataFrame) -> float:
    """Get absolute ATR(14) value from kline data."""
    try:
        if len(kline) < 20:
            return 0.0
        ti = TechInd(kline)
        atr = ti.atr(14)
        return float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
    except Exception:
        return 0.0


def compute_atr_stop_loss(atr_pct: float, multiplier: float = STOP_LOSS_ATR_MULTIPLIER) -> float:
    """Compute ATR-based stop-loss percentage.

    Args:
        atr_pct: ATR as percentage of entry price (e.g., 4.0 means 4%).
        multiplier: ATR multiplier.

    Returns:
        Stop-loss percentage clamped to [_MIN_STOP_LOSS_PCT, _MAX_STOP_LOSS_PCT].
    """
    raw = atr_pct * multiplier / 100.0  # Convert percentage to decimal
    return max(_MIN_STOP_LOSS_PCT, min(_MAX_STOP_LOSS_PCT, raw))


def compute_atr_take_profit(
    atr_pct: float, multiplier: float = TAKE_PROFIT_ATR_MULTIPLIER
) -> float:
    """Compute ATR-based take-profit percentage.

    Args:
        atr_pct: ATR as percentage of entry price (e.g., 4.0 means 4%).
        multiplier: ATR multiplier.

    Returns:
        Take-profit percentage clamped to [_MIN_TAKE_PROFIT_PCT, _MAX_TAKE_PROFIT_PCT].
    """
    raw = atr_pct * multiplier / 100.0  # Convert percentage to decimal
    return max(_MIN_TAKE_PROFIT_PCT, min(_MAX_TAKE_PROFIT_PCT, raw))


def compute_trailing_stop(pnl: float, peak_pnl: float) -> float | None:
    """Compute trailing stop level based on PnL tiers.

    Uses peak_pnl (not current pnl) to select the appropriate tier,
    and iterates in reverse so the tightest matching tier is applied.

    Returns the trailing stop lock ratio, or None if no tier is triggered.
    """
    for pnl_threshold, lock_ratio in reversed(TRAILING_STOP_TIERS):
        if peak_pnl >= pnl_threshold:
            if lock_ratio == 0.0:
                return 0.0  # breakeven protection
            return max(0.0, lock_ratio * peak_pnl)
    return None


# ── 分批止盈参数 ──
PARTIAL_PROFIT_TAKE_PNL: float = 0.15  # 盈利 15% 时止盈 50%
PARTIAL_PROFIT_TAKE_RATIO: float = 0.50  # 卖出 50% 仓位
PARTIAL_PROFIT_SECONDARY_PNL: float = 0.25  # 剩余仓位在 25% 时全部止盈

# ── "死钱"退出参数 ──
DEAD_MONEY_DAYS: int = 25  # 持仓超过 30 天视为潜在"死钱"
DEAD_MONEY_MIN_PNL: float = 0.015  # 利润 < 2% 且超过 DEAD_MONEY_DAYS 时强制退出
