"""Enhanced backtest engine with event-driven simulation and risk management.

集成 Rumi 策略和 KRange 自适应离场机制。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from aimoon.backtest import _detect_regime_safe
from aimoon.indicators.technical import TechInd
from aimoon.risk import RiskLimits, kelly_criterion
from aimoon.scoring import category_capped_score, hybrid_score
from aimoon.models import Signal
from aimoon.rumi_strategy import (
    generate_rumi_signals,
    check_krange_exit,
    compute_adaptive_trailing_stop,
    compute_rumi_score,
    compute_krange,
    RumiSignal,
    KRangeExit,
    RumiPosition,
)

logger = logging.getLogger(__name__)


# ── Rumi 策略参数（暂时禁用） ──
_RUMI_LOOKBACK: int = 10  # Rumi 回看周期
_RUMI_MIN_SCORE: float = 100.0  # 最小 Rumi 得分阈值（设置为 100 禁用策略）
_RUMI_MOMENTUM_WEIGHT: float = 0.3  # 动量权重
_RUMI_RELATIVE_STRENGTH_WEIGHT: float = 0.4  # 相对强度权重
_RUMI_VOLATILITY_WEIGHT: float = 0.3  # 波动率权重

# ── KRange 参数（暂时禁用） ──
_KRANGE_ATR_PERIOD: int = 20  # ATR 周期
_KRANGE_MULTIPLIER: float = 2.5  # KRange 乘数
_KRANGE_EXIT_THRESHOLD: float = 0.3  # 离场阈值

# ── Trailing stop 参数 ──
_TRAILING_STOP_TIERS: tuple[tuple[float, float], ...] = (
    (0.03, 0.00),  # +3% PnL: 保本保护（止损归零）
    (0.06, 0.55),  # +6% PnL: 锁定峰值利润的 55%
    (0.10, 0.45),  # +10% PnL: 锁定峰值利润的 45%
    (0.15, 0.35),  # +15% PnL: 锁定峰值利润的 35%
)

# ── 硬止损上限 ──
_HARD_LOSS_CAP: float = 0.08  # 单笔最大亏损 8%

# ── 利润保护参数 ──
_PROFIT_PROTECTION_PEAK_THRESHOLD: float = 0.03  # 峰值利润 >= 3% 时启用
_PROFIT_PROTECTION_FLOOR: float = 0.01  # 当前利润 <= 1% 时触发

# ── 时间衰减参数 ──
_TIME_DECAY_IDLE_DAYS: int = 15  # 持仓超过 15 天且利润 < 1% 视为"死钱"
_TIME_DECAY_LOSS_DAYS: int = 10  # 持仓超过 10 天仍在亏损时收紧止损
_TIME_DECAY_TIGHTEN_RATIO: float = 0.80  # 收紧后的止损为原始止损的 80%

# ── Chandelier Exit 参数 ──
_CHANDELIER_ATR_MULTIPLIER: float = 2.5  # ATR 倍数

# ── 回撤控制阈值 ──
_DD_THRESHOLDS: tuple[tuple[float, float], ...] = (
    (0.05, 0.75),  # DD > 5%: 75% 仓位
    (0.07, 0.50),  # DD > 7%: 50% 仓位
    (0.10, 0.25),  # DD > 10%: 25% 仓位
)


@dataclass
class EnhancedPosition:
    """回测引擎中的持仓记录。"""

    name: str
    entry_price: float
    entry_date: pd.Timestamp
    weight: float
    sector: str
    stop_loss: float
    entry_score: int
    peak_pnl: float = 0.0
    highest_price: float = 0.0
    atr_at_entry: float = 0.0

    def with_update(self, **kwargs: object) -> EnhancedPosition:
        """返回更新后的新实例（不可变模式）。"""
        from dataclasses import replace

        return replace(self, **kwargs)


@dataclass(frozen=True)
class EnhancedTrade:
    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    cost_pct: float
    exit_reason: str
    hold_days: int


@dataclass(frozen=True)
class EnhancedPortfolioResult:
    total_return: float
    annual_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    avg_hold_days: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    benchmark_return: float
    excess_return: float
    calmar_ratio: float
    trades: tuple
    equity_curve: tuple
    drawdown_curve: tuple
    # Vibe-Trading 移植指标
    profit_loss_ratio: float = 0.0
    max_consecutive_loss: int = 0
    information_ratio: float = 0.0


@dataclass
class PhaseState:
    """每个 bar 回测阶段的中间状态。"""

    positions: dict[str, dict]
    pending_entries: dict[str, dict]
    trades: list[EnhancedTrade]
    weak_streak: dict[str, int]
    recent_exits: dict[str, int]
    stop_loss_count: dict[str, int]
    closed_return: float
    bar_count: int


class EnhancedBacktestEngine:
    """Event-driven backtest with stop-loss, take-profit, position sizing."""

    def __init__(
        self,
        hold_days: int = 12,  # 优化：从 10 增加到 12，给更多时间让利润增长
        max_positions: int = 4,  # 优化：从 5 减少到 4，降低集中度风险
        commission: float = 0.0003,  # 0.03% 佣金（万三，最低5元）
        slippage: float = 0.002,  # 0.2% 滑点（更现实）
        stamp_tax: float = 0.001,  # 0.1% 印花税（2023年后）
        entry_threshold: int = 60,  # 优化：从 55 提高到 60，提高入场质量
        stop_loss_pct: float = 0.04,  # 优化：从 5% 降低到 4%
        take_profit_pct: float = 0.15,  # 优化：从 30% 降低到 15%
        risk_limits: RiskLimits | None = None,
        rebalance_freq: int = 3,
        benchmark_code: str | None = None,
        max_sector_pct: float = 0.25,
        use_reversal: bool = False,
        use_alpha: bool = False,
        use_ml: bool = True,
        use_kelly: bool = True,
        ic_weights: dict[str, float] | None = None,
        backtest_start_date: str | None = None,
        exit_ratio: float = 0.65,  # 优化：从 0.60 提高到 0.65，更积极止盈
        stop_loss_cooldown: int = 20,  # 优化：从 15 增加到 20，减少频繁交易
    ) -> None:
        self.hold_days = hold_days
        self.max_positions = max_positions
        self.commission = commission
        self.slippage = slippage
        self.stamp_tax = stamp_tax
        self.entry_threshold = entry_threshold
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.risk_limits = risk_limits or RiskLimits()
        self.rebalance_freq = rebalance_freq
        self.benchmark_code = benchmark_code
        self.max_sector_pct = max_sector_pct
        self.use_reversal = use_reversal
        self.use_alpha = use_alpha
        self.use_ml = use_ml
        self.use_kelly = use_kelly
        self.ic_weights = ic_weights
        self.backtest_start_date = backtest_start_date
        self.exit_threshold = int(entry_threshold * exit_ratio)
        self.stop_loss_cooldown = stop_loss_cooldown

        # 初始化智能滑点模型
        from aimoon.ml.slippage_model import SlippageModel

        self.slippage_model = SlippageModel()

        # ML 集成模型（回测开始时初始化）
        self._ml_predictor = None
        self._ml_panel = None
        self._ml_registry = None
        self._ml_score_cache: dict[str, dict[str, int]] = {}

        # ICIR 动态权重 + 因子衰减
        self._icir_weights = None
        self._decay_factors = None

        # 初始化性能监控器
        from aimoon.performance import PerformanceMonitor

        self._perf = PerformanceMonitor()

    def _buy_cost(
        self,
        price: float = 0.0,
        shares: int = 0,
        daily_volume: float = 0.0,
        volatility: float = 0.0,
    ) -> float:
        """计算买入成本（使用智能滑点）"""
        # 佣金
        commission = self.commission

        # 智能滑点（如果提供了参数）
        if price > 0 and shares > 0 and daily_volume > 0:
            order_amount = price * shares
            slippage = self.slippage_model.calculate_slippage(
                order_amount=order_amount,
                daily_volume=daily_volume,
                volatility=volatility,
            )
        else:
            slippage = self.slippage

        return commission + slippage

    def _sell_cost(
        self,
        price: float = 0.0,
        shares: int = 0,
        daily_volume: float = 0.0,
        volatility: float = 0.0,
    ) -> float:
        """计算卖出成本（使用智能滑点）"""
        # 佣金
        commission = self.commission

        # 印花税
        stamp_tax = self.stamp_tax

        # 智能滑点（如果提供了参数）
        if price > 0 and shares > 0 and daily_volume > 0:
            order_amount = price * shares
            slippage = self.slippage_model.calculate_slippage(
                order_amount=order_amount,
                daily_volume=daily_volume,
                volatility=volatility,
            )
        else:
            slippage = self.slippage

        return commission + stamp_tax + slippage

    def _empty_result(self):
        return EnhancedPortfolioResult(
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            (),
            (100.0,),
            (0.0,),
            profit_loss_ratio=0.0,
            max_consecutive_loss=0,
            information_ratio=0.0,
        )

    def _score_stock(
        self,
        code: str,
        name: str,
        kline: pd.DataFrame,
        ctx: dict | None = None,
        alpha_signals: dict[str, list] | None = None,
        ic_weights: dict[str, float] | None = None,
        ml_scores: dict[str, int] | None = None,
    ) -> int | None:
        """组合技术指标 + alpha 信号 + ML 评分（与 screener.screen_stock 一致）。"""
        if len(kline) < 60:
            return None

        # Step 1: always collect technical indicator signals
        try:
            from aimoon.indicators.technical import TechInd
            from aimoon.scoring import collect_signals

            ti = TechInd(kline)
            signals = collect_signals(ti, code=code, ctx=ctx)
        except Exception:
            signals = []

        # Step 2: add pre-computed alpha signals (from full panel)
        if alpha_signals and code in alpha_signals:
            signals.extend(alpha_signals[code])

        # Step 3: add ML ensemble signal (与 screener.screen_stock 一致)
        if ml_scores and code in ml_scores:
            ml_score = ml_scores[code]
            alpha_score = int((ml_score - 50) * 0.8)
            alpha_score = max(-40, min(40, alpha_score))

            if ml_score >= 80:
                desc = f"ml_rank_{ml_score}(强烈看多)"
            elif ml_score >= 60:
                desc = f"ml_rank_{ml_score}(看多)"
            elif ml_score <= 20:
                desc = f"ml_rank_{ml_score}(强烈看空)"
            elif ml_score <= 40:
                desc = f"ml_rank_{ml_score}(看空)"
            else:
                desc = f"ml_rank_{ml_score}(中性)"

            signals.append(Signal("ml_rank", desc, alpha_score))

        if not signals:
            return None

        # 使用 hybrid_score（与 screener 一致），而非 category_capped_score
        return hybrid_score(signals)

    def _compute_alpha_signals(self, klines: dict[str, pd.DataFrame]) -> dict | None:
        """预计算 Alpha Zoo 面板 + ML 模型 + ICIR 权重。"""
        try:
            from aimoon.factors.panel import build_panel
            from aimoon.factors.registry import get_default_registry

            panel = build_panel(klines)
            if panel is None:
                return None
            registry = get_default_registry()
            logger.info(
                "Alpha Zoo 面板构建完成: %d 只股票, %d 因子",
                len(panel["close"].columns),
                len(registry.list()),
            )

            # ICIR 动态权重 + 因子衰减
            try:
                from aimoon.ml.icir_weighter import load_or_compute_icir

                self._icir_weights = load_or_compute_icir(panel, klines, registry)
                logger.info("ICIR 动态权重加载完成")
            except Exception as e:
                logger.debug("ICIR 权重加载失败: %s", e)

            try:
                from aimoon.ml.factor_decay import get_decayed_factors

                self._decay_factors = get_decayed_factors()
                logger.info("因子衰减检测完成")
            except Exception as e:
                logger.debug("因子衰减检测失败: %s", e)

            # ML 集成模型
            if self.use_ml:
                self._init_ml_model(panel, klines, registry)

            return {"panel": panel, "registry": registry}
        except Exception as e:
            logger.warning("Alpha Zoo 面板构建失败: %s", e)
            return None

    def _init_ml_model(self, panel: dict, klines: dict, registry: object) -> None:
        """加载 ML 集成模型并自适应权重。"""
        try:
            from aimoon.ml.ensemble import EnsemblePredictor

            self._ml_predictor = EnsemblePredictor.from_cache()
            if not (self._ml_predictor.has_xgb or self._ml_predictor.has_lgbm):
                logger.info("ML 模型不可用，跳过 ML 评分")
                self._ml_predictor = None
                return

            self._ml_panel = panel
            self._ml_registry = registry

            # 自适应权重：按滚动 IC 动态调整 XGB/LGBM 权重
            try:
                self._ml_predictor.adapt_weights(panel, klines)
            except Exception as e:
                logger.debug("ML 权重自适应失败: %s", e)

            logger.info(
                "ML 模型加载完成: xgb_w=%.2f, lgbm_w=%.2f",
                self._ml_predictor._xgb_weight,
                self._ml_predictor._lgbm_weight,
            )
        except Exception as e:
            logger.warning("ML 模型加载失败: %s", e)
            self._ml_predictor = None

    def _get_ml_scores_for_date(self, target_date) -> dict[str, int] | None:
        """获取指定日期的 ML 集成预测百分位分数 (0-100)。"""
        if self._ml_predictor is None or self._ml_panel is None:
            return None

        cache_key = str(target_date)
        if cache_key in self._ml_score_cache:
            return self._ml_score_cache[cache_key]

        try:
            from aimoon.ml.feature_pipeline import extract_features

            features = extract_features(self._ml_panel, self._ml_registry, target_date=target_date)
            if features.empty:
                return None

            # Reindex to trained feature set
            fn = self._ml_predictor._feature_names
            if fn:
                features = features.reindex(columns=fn, fill_value=0.0)

            predictions: dict[str, np.ndarray] = {}

            if self._ml_predictor._xgb is not None:
                try:
                    import xgboost as xgb

                    predictions["xgb"] = self._ml_predictor._xgb.predict(xgb.DMatrix(features))
                except Exception:
                    pass

            if self._ml_predictor._lgbm is not None:
                try:
                    predictions["lgbm"] = self._ml_predictor._lgbm.predict(features)
                except Exception:
                    pass

            if not predictions:
                return None

            # Weighted average
            if len(predictions) == 2:
                combined = (
                    self._ml_predictor._xgb_weight * predictions["xgb"]
                    + self._ml_predictor._lgbm_weight * predictions["lgbm"]
                )
            elif "xgb" in predictions:
                combined = predictions["xgb"]
            else:
                combined = predictions["lgbm"]

            pred_series = pd.Series(combined, index=features.index).dropna()
            if len(pred_series) < 5:
                return None

            ranked = pred_series.rank(pct=True)
            scores = (ranked * 100).round().astype(int).to_dict()

            self._ml_score_cache[cache_key] = scores
            logger.debug("ML @ %s: %d stocks scored", target_date, len(scores))
            return scores
        except Exception as e:
            logger.debug("ML prediction failed @ %s: %s", target_date, e)
            return None

    def _get_alpha_signals_for_date(self, alpha_ctx, target_date):
        """获取指定日期的 Alpha Zoo 截面信号（含 ICIR 权重和因子衰减）。"""
        try:
            from aimoon.factors.scorer import compute_alpha_signals

            panel = alpha_ctx["panel"]
            registry = alpha_ctx["registry"]
            signals = compute_alpha_signals(
                registry,
                panel,
                target_date=target_date,
                icir_weights=self._icir_weights,
                decay_factors=self._decay_factors,
            )
            n = sum(1 for v in signals.values() if v)
            if n > 0:
                logger.debug("Alpha Zoo @ %s: %d 只股票获得信号", target_date, n)
            return signals if signals else None
        except Exception as e:
            logger.debug("Alpha Zoo @ %s 失败: %s", target_date, e)
            return None

    def _generate_rumi_signals(
        self,
        klines: dict[str, pd.DataFrame],
        names: dict[str, str],
        bar_date: pd.Timestamp,
    ) -> dict[str, RumiSignal]:
        """生成 Rumi 信号。

        Args:
            klines: K 线数据字典
            names: 股票名称字典
            bar_date: 当前日期

        Returns:
            dict[str, RumiSignal]: 股票代码 -> Rumi 信号
        """
        rumi_signals = {}

        for code, kline in klines.items():
            if bar_date not in kline.index:
                continue

            # 获取到当前日期为止的数据
            loc = kline.index.get_loc(bar_date)
            if loc < _RUMI_LOOKBACK:
                continue

            window = kline.iloc[: loc + 1]
            rumi_score, momentum_score, relative_strength, volatility = compute_rumi_score(
                window,
                lookback=_RUMI_LOOKBACK,
                momentum_weight=_RUMI_MOMENTUM_WEIGHT,
                relative_strength_weight=_RUMI_RELATIVE_STRENGTH_WEIGHT,
                volatility_weight=_RUMI_VOLATILITY_WEIGHT,
            )

            # 确定信号类型
            if rumi_score >= _RUMI_MIN_SCORE:
                signal_type = "buy"
            elif rumi_score <= 20:
                signal_type = "sell"
            else:
                signal_type = "hold"

            rumi_signals[code] = RumiSignal(
                code=code,
                name=names.get(code, code),
                rumi_score=rumi_score,
                momentum_score=momentum_score,
                relative_strength=relative_strength,
                volatility=volatility,
                signal_type=signal_type,
            )

        return rumi_signals

    def _check_rumi_exit(
        self,
        code: str,
        position: EnhancedPosition,
        klines: dict[str, pd.DataFrame],
        bar_date: pd.Timestamp,
        rumi_score: float,
        regime: str,
    ) -> KRangeExit | None:
        """检查 Rumi/KRange 离场信号。

        Args:
            code: 股票代码
            position: 当前持仓
            klines: K 线数据
            bar_date: 当前日期
            rumi_score: Rumi 得分
            regime: 市场状态

        Returns:
            KRangeExit: 离场信号（如果没有离场信号则返回 None）
        """
        if code not in klines or bar_date not in klines[code].index:
            return None

        # 创建 RumiPosition 对象
        rumi_position = RumiPosition(
            code=code,
            name=position.name,
            entry_price=position.entry_price,
            entry_date=position.entry_date,
            current_price=float(klines[code].loc[bar_date, "close"]),
            highest_price=position.highest_price,
            lowest_price=position.entry_price * 0.92,  # 最大亏损 8%
            rumi_score=rumi_score,
            atr_at_entry=position.atr_at_entry,
            krange_upper=0.0,  # 将在 check_krange_exit 中计算
            krange_lower=0.0,
            trailing_stop=position.stop_loss,
            pnl=(float(klines[code].loc[bar_date, "close"]) - position.entry_price)
            / position.entry_price,
            hold_days=(pd.Timestamp(bar_date) - position.entry_date).days,
        )

        # 检查 KRange 离场信号
        exit_signal = check_krange_exit(
            position=rumi_position,
            kline=klines[code],
            current_date=bar_date,
            rumi_score=rumi_score,
            regime=regime,
            exit_threshold=_KRANGE_EXIT_THRESHOLD,
        )

        return exit_signal

    def _phase0_execute_pending(
        self,
        bar_date: pd.Timestamp,
        positions: dict[str, EnhancedPosition],
        pending_entries: dict[str, dict],
        klines: dict[str, pd.DataFrame],
        effective_positions: int,
    ) -> None:
        """执行上一轮的待入场订单（T+1 开盘价）。

        解决前瞻偏差：信号在 T 日生成，交易在 T+1 日开盘执行。
        """
        for code, pending in list(pending_entries.items()):
            if code not in klines:
                pending_entries.pop(code, None)
                continue
            df = klines[code]
            if bar_date not in df.index:
                continue

            # 使用 T+1 开盘价入场（而非 T 日收盘价）
            if "open" in df.columns:
                entry_price = float(df.loc[bar_date, "open"])
            else:
                entry_price = float(df.loc[bar_date, "close"])  # fallback

            entry_loc = df.index.get_loc(bar_date)
            entry_window = df.iloc[:entry_loc]  # 使用 T+1 之前的数据计算止损
            dynamic_sl = _compute_dynamic_stop_loss(entry_window, self.stop_loss_pct)

            positions[code] = EnhancedPosition(
                name=pending.get("name", code),
                entry_price=entry_price,
                entry_date=bar_date,  # 实际入场日期是 T+1
                weight=pending.get("weight", 1.0 / effective_positions),
                sector=pending.get("sector", ""),
                stop_loss=dynamic_sl,
                entry_score=pending.get("score", 0),
                peak_pnl=0.0,
                highest_price=entry_price,
                atr_at_entry=_get_atr_value(entry_window),
            )
            pending_entries.pop(code, None)

    def _phase1_stop_loss_take_profit(
        self,
        bar_date: pd.Timestamp,
        positions: dict[str, EnhancedPosition],
        klines: dict[str, pd.DataFrame],
        trades: list[EnhancedTrade],
        current_regime: str,
        max_hold_bars: int,
        sector_ctx: dict[str, object] | None,
        alpha_signals: dict[str, object] | None,
        weak_streak: dict[str, int],
        recent_exits: dict[str, int],
        stop_loss_count: dict[str, int],
        bar_count: int,
    ) -> tuple[list[tuple[str, float, str, int]], float]:
        """止损/止盈/最大持仓期检查。

        返回: (to_close, closed_return)
        - to_close: 待平仓列表 [(code, exit_price, reason, hold_days), ...]
        - closed_return: 本轮已实现收益
        """
        to_close = []
        for code, pos in list(positions.items()):
            if code not in klines:
                continue
            df = klines[code]
            effective_sl = pos.stop_loss if pos.stop_loss > 0 else self.stop_loss_pct
            if bar_date not in df.index:
                last_price = float(df["close"].iloc[-1])
                # 确保 entry_date 是 datetime 类型
                entry_date = (
                    pos.entry_date
                    if isinstance(pos.entry_date, pd.Timestamp)
                    else pd.Timestamp(pos.entry_date)
                )
                elapsed = (pd.Timestamp(bar_date) - entry_date).days
                to_close.append((code, last_price, "data_gap", elapsed))
                continue

            # 修复前瞻偏差：使用开盘价而不是收盘价
            # 在实际交易中，我们无法知道当天的收盘价
            # 我们只能在开盘时下单，所以使用开盘价
            if "open" in df.columns:
                current_price = float(df.loc[bar_date, "open"])
            else:
                # 如果没有开盘价，使用前一日收盘价
                prev_idx = df.index.get_loc(bar_date) - 1
                if prev_idx >= 0:
                    current_price = float(df.iloc[prev_idx]["close"])
                else:
                    current_price = float(df.loc[bar_date, "close"])

            pnl = (current_price - pos.entry_price) / pos.entry_price
            # 确保 entry_date 是 datetime 类型
            entry_date = (
                pos.entry_date
                if isinstance(pos.entry_date, pd.Timestamp)
                else pd.Timestamp(pos.entry_date)
            )
            # 计算交易日天数（而非日历天数）
            # 使用 K 线数据中的交易日计算
            elapsed_days = 0
            try:
                entry_loc = df.index.get_loc(entry_date)
                current_loc = df.index.get_loc(bar_date)
                elapsed_days = current_loc - entry_loc
            except (KeyError, TypeError):
                # 回退到日历天数
                elapsed_days = (pd.Timestamp(bar_date) - entry_date).days

            # Update peak_pnl tracking
            if pos.peak_pnl == 0.0:
                pos = pos.with_update(peak_pnl=pnl)
            else:
                pos = pos.with_update(peak_pnl=max(pos.peak_pnl, pnl))

            # Track highest price for Chandelier Exit
            if pos.highest_price == 0.0:
                pos = pos.with_update(highest_price=current_price)
            else:
                pos = pos.with_update(highest_price=max(pos.highest_price, current_price))

            # 更新 positions 字典中的实例
            positions[code] = pos

            # ── Redesigned trailing stop: percentage-based ──
            # 使用预定义的常量，避免魔法数字
            for pnl_threshold, lock_ratio in _TRAILING_STOP_TIERS:
                if pnl >= pnl_threshold:
                    if lock_ratio == 0.0:
                        # 保本保护：止损设置为 0（保本）
                        effective_sl = 0.0
                    else:
                        # 利润锁定：锁定峰值利润的指定比例
                        pos_peak = pos.peak_pnl if pos.peak_pnl > 0 else pnl
                        effective_sl = max(effective_sl, pos_peak * lock_ratio)

            # ── Chandelier Exit: ATR-based adaptive trailing stop ──
            # Uses highest price since entry minus 2.5x ATR as trailing floor.
            # This adapts to volatility: tight in calm markets, loose in volatile ones.
            atr_val = pos.atr_at_entry if pos.atr_at_entry > 0 else 0
            if atr_val > 0 and pnl > 0:
                highest = pos.highest_price if pos.highest_price > 0 else current_price
                chandelier_stop = (
                    highest - _CHANDELIER_ATR_MULTIPLIER * atr_val
                ) / pos.entry_price - 1
                if chandelier_stop > 0:
                    effective_sl = max(effective_sl, chandelier_stop)

            # ── Hard per-trade loss cap: exit immediately if loss exceeds 8% ──
            # This handles gap-down scenarios where price blows through the stop
            if pnl <= -_HARD_LOSS_CAP:
                to_close.append((code, current_price, "stop_loss", elapsed_days))
            # ── Profit protection exit ──
            # If peak_pnl >= 5% and current pnl <= 1.5%, exit to protect gains
            # Tighter trigger (was 8%/2%) to better lock in mid-sized profits
            elif (
                pos.peak_pnl >= _PROFIT_PROTECTION_PEAK_THRESHOLD
                and pnl <= _PROFIT_PROTECTION_FLOOR
            ):
                to_close.append((code, current_price, "profit_protection", elapsed_days))
            # ── Regular exit conditions ──
            elif pnl <= -effective_sl:
                to_close.append((code, current_price, "stop_loss", elapsed_days))
            # ── Regime-adaptive take-profit ──
            # Tighter in bear/sideways to lock gains, looser in bull to let winners run
            elif pnl >= _regime_take_profit(current_regime, self.take_profit_pct):
                to_close.append((code, current_price, "take_profit", elapsed_days))
            # ── Time-decay: tighten stops on positions held too long ──
            # After 15 days, positions with negligible gain are "dead money"
            # Very conservative to avoid killing potential winners
            elif elapsed_days > 15 and 0 < pnl < 0.01:
                to_close.append((code, current_price, "time_decay", elapsed_days))
            elif elapsed_days > 10 and pnl < 0:
                # After 10 days of losing, tighten stop by 20% to cut losers faster
                tightened_sl = effective_sl * 0.8
                if pnl <= -tightened_sl:
                    to_close.append((code, current_price, "stop_loss", elapsed_days))
            # ── Momentum-based extended hold ──
            elif elapsed_days >= max_hold_bars:
                # Check if score is still strong enough to extend hold
                entry_score = pos.entry_score if pos.entry_score > 0 else 50
                current_score = self._score_stock(
                    code,
                    pos.name,
                    df.iloc[: df.index.get_loc(bar_date) + 1],
                    ctx=sector_ctx,
                    alpha_signals=alpha_signals,
                    ic_weights=self.ic_weights,
                    ml_scores=self._get_ml_scores_for_date(prev_date) if prev_date else None,
                )
                if (
                    current_score is not None
                    and current_score >= entry_score * 0.8
                    and pnl > 0
                    and elapsed_days < max_hold_bars * 2.0
                ):
                    # Strong momentum: extend hold (up to 1.5x max_hold_bars)
                    continue
                else:
                    to_close.append((code, current_price, "hold_period", elapsed_days))

        closed_return = 0.0
        for code, exit_price, reason, hdays in to_close:
            pos = positions.pop(code)
            weak_streak.pop(code, None)
            gross_ret = (exit_price - pos.entry_price) / pos.entry_price
            cost = self._buy_cost() + self._sell_cost()
            net_ret = gross_ret - cost
            trades.append(
                EnhancedTrade(
                    code=code,
                    name=pos.name,
                    entry_date=str(pos.entry_date),
                    exit_date=str(bar_date),
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    return_pct=net_ret * 100,
                    cost_pct=cost * 100,
                    exit_reason=reason,
                    hold_days=hdays,
                )
            )
            closed_return += net_ret * pos.weight
            recent_exits[code] = bar_count
            if reason == "stop_loss":
                stop_loss_count[code] = stop_loss_count.get(code, 0) + 1

        return to_close, closed_return

    def _phase2_momentum_check(
        self,
        bar_date: pd.Timestamp,
        prev_date: pd.Timestamp | None,
        positions: dict[str, EnhancedPosition],
        klines: dict[str, pd.DataFrame],
        trades: list[EnhancedTrade],
        alpha_signals: dict[str, object] | None,
        sector_ctx: dict[str, object] | None,
        weak_streak: dict[str, int],
        recent_exits: dict[str, int],
        bar_count: int,
        closed_return: float,
    ) -> float:
        """动量检查（每 3 个 bar）。

        评估持仓股票的动量，退出弱信号持仓。
        返回: 更新后的 closed_return
        """
        # 修复前瞻偏差：使用前一天的 alpha 信号，与技术信号时间基一致
        # 技术信号使用 df.iloc[:idx]（截至前一天），alpha 信号也应使用前一天
        alpha_query_date = prev_date if prev_date is not None else bar_date
        alpha_sigs = (
            self._get_alpha_signals_for_date(alpha_signals, alpha_query_date)
            if alpha_signals
            else None
        )
        weak_codes = []
        for code, pos in list(positions.items()):
            if code not in klines:
                continue
            df = klines[code]
            if bar_date not in df.index:
                continue
            idx = df.index.get_loc(bar_date)
            if idx < 60:
                continue
            window = df.iloc[:idx]
            ml_sigs = self._get_ml_scores_for_date(alpha_query_date)
            score = self._score_stock(
                code,
                pos.name,
                window,
                ctx=sector_ctx,
                alpha_signals=alpha_sigs,
                ic_weights=self.ic_weights,
                ml_scores=ml_sigs,
            )
            if score is not None and score < self.exit_threshold:
                weak_streak[code] = weak_streak.get(code, 0) + 1
                # 连续 2 次弱信号才退出（避免临时波动误触）
                if weak_streak[code] >= 2:
                    weak_codes.append(code)
            else:
                weak_streak.pop(code, None)

        for code in weak_codes:
            if code not in positions:
                continue
            if code not in klines or bar_date not in klines[code].index:
                continue
            weak_streak.pop(code, None)
            pos = positions.pop(code)
            # 修复前瞻偏差：使用开盘价而非收盘价执行退出（与止损/止盈一致）
            if "open" in klines[code].columns:
                exit_price = float(klines[code].loc[bar_date, "open"])
            else:
                exit_price = float(klines[code].loc[bar_date, "close"])  # fallback
            gross_ret = (exit_price - pos.entry_price) / pos.entry_price
            cost = self._buy_cost() + self._sell_cost()
            net_ret = gross_ret - cost
            # 确保 entry_date 是 datetime 类型
            entry_date = (
                pos.entry_date
                if isinstance(pos.entry_date, pd.Timestamp)
                else pd.Timestamp(pos.entry_date)
            )
            elapsed = (pd.Timestamp(bar_date) - entry_date).days
            trades.append(
                EnhancedTrade(
                    code=code,
                    name=pos.name,
                    entry_date=str(pos.entry_date),
                    exit_date=str(bar_date),
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    return_pct=net_ret * 100,
                    cost_pct=cost * 100,
                    exit_reason="momentum_exit",
                    hold_days=elapsed,
                )
            )
            closed_return += net_ret * pos.weight
            recent_exits[code] = bar_count

        return closed_return

    def _phase3_mark_to_market(
        self,
        bar_date: pd.Timestamp,
        positions: dict[str, EnhancedPosition],
        klines: dict[str, pd.DataFrame],
        benchmark_kline: pd.DataFrame | None,
        has_benchmark: bool,
        prev_bench_price: float | None,
        benchmark_equity: list[float],
    ) -> tuple[float, float | None, list[float]]:
        """逐日盯市。

        返回: (unrealized_return, prev_bench_price, benchmark_equity)
        """
        unrealized_return = 0.0
        for code, pos in positions.items():
            df = klines.get(code)
            if df is None or bar_date not in df.index:
                continue
            current_price = float(df.loc[bar_date, "close"])
            ret = (current_price - pos.entry_price) / pos.entry_price
            unrealized_return += ret * pos.weight

        if has_benchmark and benchmark_kline is not None and bar_date in benchmark_kline.index:
            bench_price_now = float(benchmark_kline.loc[bar_date, "close"])
            if prev_bench_price is not None:
                bench_ret = (bench_price_now - prev_bench_price) / prev_bench_price
                benchmark_equity.append(benchmark_equity[-1] * (1 + bench_ret))
            prev_bench_price = bench_price_now

        return unrealized_return, prev_bench_price, benchmark_equity

    def _phase4_open_replacements(
        self,
        bar_date: pd.Timestamp,
        prev_date: pd.Timestamp | None,
        positions: dict[str, EnhancedPosition],
        pending_entries: dict[str, dict],
        klines: dict[str, pd.DataFrame],
        trades: list[EnhancedTrade],
        names: dict[str, str],
        sector_map: dict[str, str],
        alpha_signals: dict[str, object] | None,
        sector_ctx: dict[str, object] | None,
        recent_exits: dict[str, int],
        stop_loss_count: dict[str, int],
        effective_positions: int,
        effective_threshold: int,
        current_regime: str,
        dd_scale: float,
        bar_count: int,
        rumi_signals: dict[str, object] | None = None,
    ) -> None:
        """开新仓补位。

        当持仓数 < 最大持仓数时，评估候选股票并记录到 pending_entries。
        """
        # 修复前瞻偏差：使用前一天的 alpha 信号（与 Phase 2 一致）
        alpha_query_date = prev_date if prev_date is not None else bar_date
        alpha_sigs = (
            self._get_alpha_signals_for_date(alpha_signals, alpha_query_date)
            if alpha_signals
            else None
        )
        sector_exposure: dict[str, float] = {}
        for pos in positions.values():
            sec = pos.sector if pos.sector else ""
            if sec:
                sector_exposure[sec] = sector_exposure.get(sec, 0.0) + pos.weight

        scored_candidates: list[tuple[str, str, int]] = []
        for code, df in klines.items():
            if code == self.benchmark_code or code in positions:
                continue
            if code in recent_exits and (bar_count - recent_exits[code]) < 5:
                continue
            # Stop-loss cooldown: allow re-entry after cooldown period
            # Permanently blacklist only after 3 total stop-outs
            sl_count = stop_loss_count.get(code, 0)
            if sl_count >= 3:
                continue  # Permanently blacklisted
            elif sl_count > 0 and code in recent_exits:
                bars_since_exit = bar_count - recent_exits[code]
                if bars_since_exit < self.stop_loss_cooldown:
                    continue  # Still in cooldown period
            if bar_date not in df.index:
                continue
            idx = df.index.get_loc(bar_date)
            if idx < 60:
                continue
            window = df.iloc[:idx]
            if len(window) < 60:
                continue
            ml_sigs = self._get_ml_scores_for_date(alpha_query_date)
            score = self._score_stock(
                code,
                names.get(code, code),
                window,
                ctx=sector_ctx,
                alpha_signals=alpha_sigs,
                ic_weights=self.ic_weights,
                ml_scores=ml_sigs,
            )
            if score is not None and score >= effective_threshold:
                scored_candidates.append((code, names.get(code, code), score))

        # RPS cross-sectional ranks
        rps_bonus: dict[str, float] = {}
        if len(scored_candidates) >= 5:
            roc_data: dict[str, dict[int, float]] = {}
            for code, _, _ in scored_candidates:
                if code not in klines or bar_date not in klines[code].index:
                    continue
                df = klines[code]
                loc = df.index.get_loc(bar_date)
                if loc < 20:
                    continue
                close = pd.to_numeric(df["close"].iloc[: loc + 1], errors="coerce")
                rocs: dict[int, float] = {}
                for period in [5, 10, 20]:
                    if len(close) > period and close.iloc[-period - 1] > 0:
                        rocs[period] = float(
                            (close.iloc[-1] - close.iloc[-period - 1])
                            / close.iloc[-period - 1]
                            * 100
                        )
                if rocs:
                    roc_data[code] = rocs
            for period in [5, 10, 20]:
                pvals = {c: v[period] for c, v in roc_data.items() if period in v}
                if len(pvals) < 5:
                    continue
                scodes = sorted(pvals, key=lambda c: pvals[c])
                total = len(scodes)
                for rank, code in enumerate(scodes):
                    pct = (rank + 1) / total * 100
                    if pct >= 90:
                        rps_bonus[code] = rps_bonus.get(code, 0) + 2
                    elif pct >= 75:
                        rps_bonus[code] = rps_bonus.get(code, 0) + 1
                    elif pct <= 10:
                        rps_bonus[code] = rps_bonus.get(code, 0) - 2
                    elif pct <= 25:
                        rps_bonus[code] = rps_bonus.get(code, 0) - 1

        scores = {c: s + rps_bonus.get(c, 0) for c, _, s in scored_candidates}

        # ── 短期反转加权：近期跌幅大的高分股优先买入 ──
        reversal_bonus: dict[str, float] = {}
        for code in scores:
            if code not in klines or bar_date not in klines[code].index:
                continue
            df = klines[code]
            loc = df.index.get_loc(bar_date)
            if loc < 5:
                continue
            close_5d = pd.to_numeric(df["close"].iloc[loc - 5 : loc + 1], errors="coerce")
            if len(close_5d) >= 2 and close_5d.iloc[0] > 0:
                roc5 = (close_5d.iloc[-1] - close_5d.iloc[0]) / close_5d.iloc[0]
                if roc5 < -0.05:  # 5日跌超5%
                    reversal_bonus[code] = 5  # 大幅加分
                elif roc5 < -0.03:  # 5日跌超3%
                    reversal_bonus[code] = 3
                elif roc5 > 0.05:  # 5日涨超5%，推迟入场
                    reversal_bonus[code] = -5

        for code, bonus in reversal_bonus.items():
            if code in scores:
                scores[code] = scores[code] + bonus

        # ── Rumi 信号加权：高 Rumi 得分的股票优先买入 ──
        rumi_bonus: dict[str, float] = {}
        if rumi_signals:  # 只在 Rumi 信号可用时应用
            for code in scores:
                if code in rumi_signals:
                    rumi_sig = rumi_signals[code]
                    # Rumi 得分越高，加分越多
                    if rumi_sig.rumi_score >= 80:
                        rumi_bonus[code] = 5  # 强看多
                    elif rumi_sig.rumi_score >= 70:
                        rumi_bonus[code] = 3  # 看多
                    elif rumi_sig.rumi_score >= 60:
                        rumi_bonus[code] = 1  # 温和看多
                    elif rumi_sig.rumi_score <= 20:
                        rumi_bonus[code] = -5  # 强看空

        for code, bonus in rumi_bonus.items():
            if code in scores:
                scores[code] = scores[code] + bonus

        if scores:
            # 修复前瞻偏差：只使用 bar_date 之前的历史交易计算 Kelly 参数
            # 而不是使用整个回测期间的所有交易（包括未来的交易）
            historical_trades = [
                t for t in trades if pd.Timestamp(t.entry_date) < pd.Timestamp(bar_date)
            ]
            weights = self._compute_position_weights(
                historical_trades,
                effective_positions,
                klines,
                scores,
                current_regime,
                dd_scale,
            )
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            slots = effective_positions - len(positions)
            for code, score in ranked:
                if slots <= 0:
                    break
                sector = sector_map.get(code, "")
                weight = weights.get(code, 1.0 / effective_positions)
                if sector:
                    cur_sec = sector_exposure.get(sector, 0.0)
                    if cur_sec + weight > self.max_sector_pct:
                        continue
                    sector_exposure[sector] = cur_sec + weight

                # 解决前瞻偏差：不在 T 日立即入场，记录到 pending_entries
                # 将在下一个交易日（T+1）的 Phase 0 中以开盘价执行
                pending_entries[code] = {
                    "name": names.get(code, code),
                    "weight": weight,
                    "sector": sector,
                    "score": score,
                    "signal_date": bar_date,
                }
                slots -= 1

    def run_portfolio(self, klines, names=None, sectors=None, ctx=None):
        """Momentum-driven portfolio: rebalance when signals change, not on fixed schedule."""
        if not klines:
            return self._empty_result()
        names = names or {c: c for c in klines}

        # 修复：确保所有 K 线数据使用正确的日期格式
        from aimoon.data.history import fix_kline_dates

        for code, kline in klines.items():
            klines[code] = fix_kline_dates(kline)

        all_dates = set()
        for df in klines.values():
            all_dates.update(df.index)
        sorted_dates = sorted(all_dates)
        if len(sorted_dates) < 60 + self.hold_days:
            return self._empty_result()

        alpha_signals = self._compute_alpha_signals(klines) if self.use_alpha else None
        equity = [100.0]
        dd_curve = [0.0]
        trades = []
        positions: dict[str, EnhancedPosition] = {}
        weak_streak: dict[str, int] = {}  # 连续弱信号计数
        benchmark_equity = [100.0]
        has_benchmark = self.benchmark_code in klines
        benchmark_kline = klines.get(self.benchmark_code) if has_benchmark else None
        prev_bench_price = None
        peak = 100.0
        sector_map = (ctx or {}).get("sector_map", {})
        recent_exits: dict[str, int] = {}
        stop_loss_count: dict[str, int] = {}
        pending_entries: dict[str, dict] = {}  # 延迟入场：信号日记录，次日开盘执行
        bar_count = 0
        check_interval = 3
        max_hold_bars = self.hold_days * 2  # Respect configured parameter (up to 2x)
        sector_ctx = {"sector_map": sector_map} if sector_map else None
        prev_date = None  # 用于追踪前一天的日期

        for bar_date in sorted_dates[60:]:
            # 确保 bar_date 是 Timestamp 类型
            bar_date = pd.Timestamp(bar_date)

            if self.backtest_start_date is not None and bar_date < pd.Timestamp(
                self.backtest_start_date
            ):
                bar_count += 1
                continue

            effective_positions = self.max_positions
            effective_threshold = self.entry_threshold
            current_regime = "sideways"  # Default regime
            if benchmark_kline is not None:
                regime = _detect_regime_safe(benchmark_kline, bar_date)
                if regime is not None:
                    current_regime = regime.state
                    # 使用增强 regime 检测的 position_scale
                    if hasattr(regime, "position_scale"):
                        effective_positions = max(
                            1, int(self.max_positions * regime.position_scale)
                        )
                    else:
                        # 向后兼容：使用原有逻辑
                        if regime.state == "bear":
                            effective_positions = max(2, self.max_positions // 2)
                            effective_threshold = self.entry_threshold + 7
                        elif regime.state == "high_volatility":
                            effective_positions = max(2, self.max_positions // 2)
                            effective_threshold = self.entry_threshold + 8
                        elif regime.state == "bull":
                            effective_threshold = max(50, self.entry_threshold - 5)

            # ── Generate Rumi signals for this bar (skip if disabled) ──
            if _RUMI_MIN_SCORE < 100.0:
                rumi_signals = self._generate_rumi_signals(klines, names, bar_date)
                logger.debug("Rumi signals generated: %d stocks", len(rumi_signals))
            else:
                rumi_signals = {}  # Rumi 策略已禁用

            # ── Drawdown-based exposure control ──
            # Proactively reduce exposure as drawdown deepens to protect capital.
            current_dd = dd_curve[-1] if dd_curve else 0.0
            dd_scale = 1.0
            for dd_threshold, scale in _DD_THRESHOLDS:
                if current_dd > dd_threshold:
                    dd_scale = scale
                    break
            if dd_scale < 1.0:
                effective_positions = max(1, int(effective_positions * dd_scale))

            # ── Phase 0: 执行上一轮的待入场订单（T+1 开盘价）──
            # 解决前瞻偏差：信号在 T 日生成，交易在 T+1 日开盘执行
            with self._perf.timer("phase0_execute"):
                self._phase0_execute_pending(
                    bar_date=bar_date,
                    positions=positions,
                    pending_entries=pending_entries,
                    klines=klines,
                    effective_positions=effective_positions,
                )

            # ── Phase 1: stop-loss / take-profit / max hold (every bar) ──
            with self._perf.timer("phase1_stop_loss"):
                to_close, closed_return = self._phase1_stop_loss_take_profit(
                    bar_date=bar_date,
                    positions=positions,
                    klines=klines,
                    trades=trades,
                    current_regime=current_regime,
                    max_hold_bars=max_hold_bars,
                    sector_ctx=sector_ctx,
                    alpha_signals=alpha_signals,
                    weak_streak=weak_streak,
                    recent_exits=recent_exits,
                    stop_loss_count=stop_loss_count,
                    bar_count=bar_count,
                )

            # ── Rumi/KRange exit check (after Phase 1) ──
            rumi_exits = []
            for code, pos in list(positions.items()):
                if code in rumi_signals:
                    rumi_sig = rumi_signals[code]
                    exit_signal = self._check_rumi_exit(
                        code=code,
                        position=pos,
                        klines=klines,
                        bar_date=bar_date,
                        rumi_score=rumi_sig.rumi_score,
                        regime=current_regime,
                    )
                    if exit_signal:
                        rumi_exits.append(
                            (
                                code,
                                exit_signal.exit_price,
                                "rumi_krange_exit",
                                (pd.Timestamp(bar_date) - pos.entry_date).days,
                            )
                        )
                        logger.info(
                            "Rumi/KRange exit: %s at %.2f (reason: %s)",
                            code,
                            exit_signal.exit_price,
                            exit_signal.exit_reason,
                        )

            # Process Rumi exits
            for code, exit_price, reason, hdays in rumi_exits:
                if code in positions:
                    pos = positions.pop(code)
                    weak_streak.pop(code, None)
                    gross_ret = (exit_price - pos.entry_price) / pos.entry_price
                    cost = self._buy_cost() + self._sell_cost()
                    net_ret = gross_ret - cost
                    trades.append(
                        EnhancedTrade(
                            code=code,
                            name=pos.name,
                            entry_date=str(pos.entry_date),
                            exit_date=str(bar_date),
                            entry_price=pos.entry_price,
                            exit_price=exit_price,
                            return_pct=net_ret * 100,
                            cost_pct=cost * 100,
                            exit_reason=reason,
                            hold_days=hdays,
                        )
                    )
                    closed_return += net_ret * pos.weight
                    recent_exits[code] = bar_count

            # ── Phase 2: momentum check (every 3 bars) ──
            if bar_count % check_interval == 0 and positions:
                with self._perf.timer("phase2_momentum"):
                    closed_return = self._phase2_momentum_check(
                        bar_date=bar_date,
                        prev_date=prev_date,
                        positions=positions,
                        klines=klines,
                        trades=trades,
                        alpha_signals=alpha_signals,
                        sector_ctx=sector_ctx,
                        weak_streak=weak_streak,
                        recent_exits=recent_exits,
                        bar_count=bar_count,
                        closed_return=closed_return,
                    )

            # ── Phase 3: mark-to-market ──
            with self._perf.timer("phase3_mark_to_market"):
                unrealized_return, prev_bench_price, benchmark_equity = self._phase3_mark_to_market(
                    bar_date=bar_date,
                    positions=positions,
                    klines=klines,
                    benchmark_kline=benchmark_kline,
                    has_benchmark=has_benchmark,
                    prev_bench_price=prev_bench_price,
                    benchmark_equity=benchmark_equity,
                )

            # 更新权益曲线和回撤
            period_return = closed_return + unrealized_return
            new_equity = equity[-1] * (1 + period_return)

            # 断路器：防止权益变为负数
            if new_equity <= 0:
                logger.warning("Portfolio wiped out at bar %d, stopping backtest", bar_count)
                equity.append(0.0)
                dd_curve.append(1.0)
                break

            equity.append(new_equity)
            current_val = equity[-1]
            peak = max(peak, current_val)
            dd = (peak - current_val) / peak if peak > 0 else 0.0
            dd_curve.append(dd)

            # ── Phase 4: open replacements when slots available ──
            if len(positions) < effective_positions and bar_count % check_interval == 0:
                with self._perf.timer("phase4_replacements"):
                    self._phase4_open_replacements(
                        bar_date=bar_date,
                        prev_date=prev_date,
                        positions=positions,
                        pending_entries=pending_entries,
                        klines=klines,
                        trades=trades,
                        names=names,
                        sector_map=sector_map,
                        alpha_signals=alpha_signals,
                        sector_ctx=sector_ctx,
                        recent_exits=recent_exits,
                        stop_loss_count=stop_loss_count,
                        effective_positions=effective_positions,
                        effective_threshold=effective_threshold,
                        current_regime=current_regime,
                        dd_scale=dd_scale,
                        bar_count=bar_count,
                        rumi_signals=rumi_signals,
                    )

            prev_date = bar_date  # 更新前一天日期，供下一轮 alpha 信号查询使用
            bar_count += 1

        # 记录性能报告
        logger.info("Backtest performance:\n%s", self._perf.summary())

        return self._compute_metrics(trades, equity, dd_curve, benchmark_equity)

    def _compute_metrics(self, trades, equity, dd_curve, benchmark_equity):
        if not trades:
            return self._empty_result()
        total_ret = (equity[-1] / equity[0] - 1) * 100
        n_periods = len(equity) - 1
        total_days = n_periods * 1
        annual_ret = ((equity[-1] / equity[0]) ** (252 / max(total_days, 1)) - 1) * 100
        returns = [(equity[i] / equity[i - 1] - 1) for i in range(1, len(equity))]
        if returns:
            mean_ret = np.mean(returns) * 252 / 1
            std_ret = np.std(returns) * np.sqrt(252 / 1)
            sharpe = mean_ret / std_ret if std_ret > 0 else 0.0
            downside = [r for r in returns if r < 0]
            downside_std = np.std(downside) * np.sqrt(252 / 1) if downside else 0.0
            sortino = mean_ret / downside_std if downside_std > 0 else 0.0
        else:
            sharpe = sortino = 0.0
        max_dd = max(dd_curve) if dd_curve else 0.0
        win_rate = sum(1 for t in trades if t.return_pct > 0) / len(trades)
        wins = [t.return_pct for t in trades if t.return_pct > 0]
        losses = [t.return_pct for t in trades if t.return_pct <= 0]
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (
            (gross_profit / gross_loss)
            if gross_loss > 0
            else (float("inf") if gross_profit > 0 else 0.0)
        )
        avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0.0
        bench_ret = (
            (benchmark_equity[-1] / benchmark_equity[0] - 1) * 100
            if len(benchmark_equity) > 1
            else 0.0
        )
        calmar = annual_ret / max_dd if max_dd > 0 else 0.0

        # 盈亏比（Vibe-Trading 移植）
        avg_w = float(avg_win)
        avg_l = abs(float(avg_loss))
        profit_loss_ratio = round(avg_w / avg_l, 4) if avg_l > 1e-10 else 0.0

        # 最大连续亏损次数（Vibe-Trading 移植）
        max_consec = 0
        cur_consec = 0
        for t in trades:
            if t.return_pct < 0:
                cur_consec += 1
                max_consec = max(max_consec, cur_consec)
            else:
                cur_consec = 0

        # 信息比率（Vibe-Trading 移植）
        information_ratio = 0.0
        if len(benchmark_equity) > 1 and len(returns) > 1:
            bench_returns = [
                (benchmark_equity[i] / benchmark_equity[i - 1] - 1)
                for i in range(1, len(benchmark_equity))
            ]
            n = min(len(returns), len(bench_returns))
            if n > 1:
                active = np.array(returns[-n:]) - np.array(bench_returns[-n:])
                active_std = float(np.std(active))
                information_ratio = round(
                    float(np.mean(active) / (active_std + 1e-10) * np.sqrt(252 / 1)), 4
                )

        return EnhancedPortfolioResult(
            total_return=round(total_ret, 2),
            annual_return=round(annual_ret, 2),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            max_drawdown=round(max_dd * 100, 2),
            win_rate=round(win_rate, 4),
            trade_count=len(trades),
            avg_hold_days=round(avg_hold, 1),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            benchmark_return=round(bench_ret, 2),
            excess_return=round(total_ret - bench_ret, 2),
            calmar_ratio=round(calmar, 2),
            trades=tuple(trades),
            equity_curve=tuple(equity),
            drawdown_curve=tuple(dd_curve),
            profit_loss_ratio=profit_loss_ratio,
            max_consecutive_loss=max_consec,
            information_ratio=information_ratio,
        )

    def _compute_position_weights(
        self,
        trades: list,
        max_positions: int,
        klines: dict,
        scores: dict[str, float],
        regime: str = "sideways",
        drawdown_scale: float = 1.0,
    ) -> dict[str, float]:
        """Compute position weights: Kelly + volatility + score-proportional.

        1. Kelly criterion for base sizing (based on trade history)
        2. Regime-adaptive Kelly scaling (bear/half, bull/full)
        3. Drawdown-based weight reduction
        4. Volatility targeting: scale down when market is volatile
        5. Per-stock vol adjustment: less volatile stocks get more weight
        6. Score-proportional adjustment: higher-scoring stocks get more weight
        """
        equal_weight = 1.0 / max_positions

        # ── 波动率目标：组合级调整 ──
        # 用所有候选股票的平均波动率估算市场波动
        vol_scale = 1.0
        target_vol = 0.20  # 目标年化波动率 20%
        realized_vols = []
        for code in scores:
            df = klines.get(code)
            if df is not None and len(df) >= 20:
                rv = float(df["close"].pct_change().iloc[-20:].std() * np.sqrt(252))
                realized_vols.append(rv)
        if realized_vols:
            avg_vol = float(np.mean(realized_vols))
            if avg_vol > 0.01:
                vol_scale = min(2.0, max(0.3, target_vol / avg_vol))

        # ── Score-proportional adjustment ──
        avg_score = np.mean(list(scores.values())) if scores else 1.0
        score_scale = {
            code: max(0.5, min(1.5, score / avg_score)) for code, score in scores.items()
        }

        if not self.use_kelly or len(trades) < 20:
            # 等权 × 波动率调整 × 分数比例调整
            base = equal_weight * vol_scale
            weights: dict[str, float] = {
                code: float(min(base * score_scale[code], 0.20)) for code in scores
            }
            # 归一化，上限 20%
            total = sum(weights.values())
            if total > 0:
                weights = {c: float(min(v / total, 0.20)) for c, v in weights.items()}
            return weights

        # ── Kelly 基础仓位 ──
        win_rate = sum(1 for t in trades if t.return_pct > 0) / len(trades)
        wins = [t.return_pct for t in trades if t.return_pct > 0]
        losses = [abs(t.return_pct) for t in trades if t.return_pct < 0]
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 1.0

        kelly = kelly_criterion(win_rate, avg_win, avg_loss)
        if kelly <= 0:
            return {code: equal_weight * vol_scale for code in scores}

        # ── Regime-adaptive Kelly scaling ──
        # Reduce sizing in adverse regimes to protect capital
        regime_kelly_scale = {
            "bull": 1.0,
            "sideways": 0.7,
            "bear": 0.3,
            "high_volatility": 0.5,
        }
        kelly *= regime_kelly_scale.get(regime, 0.7)

        # ── 个股仓位：Kelly × 波动率目标 × 个股波动率调整 × 分数比例 ──
        kelly_weights: dict[str, float] = {}
        for code in scores:
            df = klines.get(code)
            if df is not None and len(df) >= 20:
                stock_vol = float(df["close"].pct_change().iloc[-20:].std() * np.sqrt(252))
                # 低波动股票获得更多权重，高波动股票减少
                vol_adj = 1.0 / max(stock_vol / avg_vol, 0.5) if avg_vol > 0 else 1.0
                w = kelly * 0.5 * vol_scale * vol_adj * score_scale[code]
            else:
                w = kelly * 0.5 * vol_scale * score_scale[code]
            kelly_weights[code] = float(max(w, 0.02))

        # 归一化，上限 20%
        total = sum(kelly_weights.values())
        if total > 0:
            kelly_weights = {c: float(min(v / total, 0.20)) for c, v in kelly_weights.items()}
        return kelly_weights


def _get_atr_value(kline: pd.DataFrame) -> float:
    """Get absolute ATR(14) value from kline data."""
    try:
        if len(kline) < 20:
            return 0.0
        ti = TechInd(kline)
        atr = ti.atr(14)
        return float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
    except Exception:
        return 0.0


_REGIME_TAKE_PROFIT: dict[str, float] = {
    "bull": 0.15,  # 15%: let winners run in bull markets
    "sideways": 0.10,  # 10%: moderate target in range-bound markets
    "bear": 0.07,  # 7%: tight target in bear markets
    "high_volatility": 0.12,  # 12%: balanced in volatile conditions
}


def _regime_take_profit(regime: str, fallback: float = 0.15) -> float:
    """Return regime-adaptive take-profit threshold."""
    return _REGIME_TAKE_PROFIT.get(regime, fallback)


def _compute_dynamic_stop_loss(kline: pd.DataFrame, fallback: float = 0.06) -> float:
    """Compute ATR-based dynamic stop-loss: 2.0x ATR_pct, clamped to [4%, 8%].

    Balanced bounds: tight enough to limit losses, loose enough to avoid
    premature stop-outs from normal volatility.
    """
    try:
        if len(kline) < 20:
            return fallback
        ti = TechInd(kline)
        atr_pct = ti.atr_pct(14)
        if atr_pct <= 0:
            return fallback
        return max(0.04, min(0.08, atr_pct * 2.0 / 100))
    except Exception:
        return fallback
