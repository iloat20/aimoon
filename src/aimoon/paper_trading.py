"""Paper trading simulation system for real-time strategy validation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def _trading_days_between(start: datetime, end: datetime) -> int:
    """Count trading days between two dates (weekdays only)."""
    if start >= end:
        return 0
    days = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Monday-Friday
            days += 1
    return days


@dataclass
class Position:
    """Represents an open position."""

    code: str
    name: str
    entry_date: datetime
    entry_price: float
    shares: int
    cost: float  # Total cost including commission
    stop_loss: float
    take_profit: float
    entry_score: float
    current_score: float = 0.0  # Latest hybrid score (updated daily)
    peak_price: float  # Track highest price for trailing stop
    sector: str = ""


@dataclass
class Trade:
    """Represents a completed trade."""

    code: str
    name: str
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    shares: int
    pnl: float  # Profit/loss in RMB
    pnl_pct: float  # Profit/loss percentage
    exit_reason: str
    hold_days: int
    cost: float  # Transaction costs


@dataclass
class PortfolioSnapshot:
    """Portfolio state at a point in time."""

    date: datetime
    cash: float
    positions_value: float
    total_value: float
    positions: dict  # code -> {shares, entry_price, current_price}


class PaperTradingEngine:
    """Real-time paper trading simulation engine."""

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,  # 100万初始资金
        commission_rate: float = 0.0003,  # 万三佣金
        slippage_rate: float = 0.001,  # 0.1%滑点
        stamp_tax_rate: float = 0.0005,  # 万五印花税（仅卖出）
        max_positions: int = 5,
        max_position_pct: float = 0.20,  # 单只最大仓位20%
        entry_threshold: float = 55.0,
        exit_threshold: float = 33.0,
        stop_loss_pct: float = 0.06,
        take_profit_pct: float = 0.15,
        trailing_stop_start: float = 0.05,  # +5%开始追踪止损
        trailing_stop_pct: float = 0.50,  # 追踪50%的峰值利润
        profit_protection_start: float = 0.05,  # +5%启动利润保护
        profit_protection_floor: float = 0.01,  # 回落到+1%退出
        max_sector_pct: float = 0.25,
        data_dir: str = "paper_trading",
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.stamp_tax_rate = stamp_tax_rate
        self.max_positions = max_positions
        self.max_position_pct = max_position_pct
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_stop_start = trailing_stop_start
        self.trailing_stop_pct = trailing_stop_pct
        self.profit_protection_start = profit_protection_start
        self.profit_protection_floor = profit_protection_floor
        self.max_sector_pct = max_sector_pct

        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.snapshots: list[PortfolioSnapshot] = []
        self.sector_exposure: dict[str, float] = {}

        # Data persistence
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.portfolio_file = self.data_dir / "portfolio.json"
        self.trades_file = self.data_dir / "trades.json"
        self.snapshots_file = self.data_dir / "snapshots.json"

        # Load existing state if available
        self._load_state()

    def _load_state(self):
        """Load portfolio state from disk."""
        if self.portfolio_file.exists():
            try:
                data = json.loads(self.portfolio_file.read_text(encoding="utf-8"))
                self.cash = data.get("cash", self.initial_capital)
                self.positions = {
                    code: Position(**pos_data)
                    for code, pos_data in data.get("positions", {}).items()
                }
                logger.info(
                    "Loaded portfolio state: %.2f cash, %d positions",
                    self.cash,
                    len(self.positions),
                )
            except Exception as e:
                logger.warning("Failed to load portfolio state: %s", e)

        if self.trades_file.exists():
            try:
                trades_data = json.loads(self.trades_file.read_text(encoding="utf-8"))
                self.trades = [Trade(**trade) for trade in trades_data]
                logger.info("Loaded %d historical trades", len(self.trades))
            except Exception as e:
                logger.warning("Failed to load trades: %s", e)

    def _save_state(self):
        """Save portfolio state to disk."""
        try:
            portfolio_data = {
                "cash": self.cash,
                "positions": {
                    code: {
                        "code": pos.code,
                        "name": pos.name,
                        "entry_date": pos.entry_date.isoformat(),
                        "entry_price": pos.entry_price,
                        "shares": pos.shares,
                        "cost": pos.cost,
                        "stop_loss": pos.stop_loss,
                        "take_profit": pos.take_profit,
                        "entry_score": pos.entry_score,
                        "peak_price": pos.peak_price,
                        "sector": pos.sector,
                    }
                    for code, pos in self.positions.items()
                },
            }
            self.portfolio_file.write_text(
                json.dumps(portfolio_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            trades_data = [
                {
                    "code": t.code,
                    "name": t.name,
                    "entry_date": t.entry_date.isoformat(),
                    "exit_date": t.exit_date.isoformat(),
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "shares": t.shares,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "exit_reason": t.exit_reason,
                    "hold_days": t.hold_days,
                    "cost": t.cost,
                }
                for t in self.trades
            ]
            self.trades_file.write_text(
                json.dumps(trades_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            logger.info("Saved portfolio state")
        except Exception as e:
            logger.error("Failed to save portfolio state: %s", e)

    def calculate_buy_cost(self, price: float, shares: int) -> float:
        """Calculate total cost for buying shares."""
        commission = max(price * shares * self.commission_rate, 5.0)  # Min 5 RMB
        slippage = price * shares * self.slippage_rate
        return price * shares + commission + slippage

    def calculate_sell_revenue(self, price: float, shares: int) -> float:
        """Calculate revenue from selling shares."""
        commission = max(price * shares * self.commission_rate, 5.0)
        stamp_tax = price * shares * self.stamp_tax_rate
        slippage = price * shares * self.slippage_rate
        return price * shares - commission - stamp_tax - slippage

    def can_open_position(self, code: str, sector: str, price: float) -> tuple[bool, str]:
        """Check if we can open a new position."""
        if code in self.positions:
            return False, "Already holding this stock"

        if len(self.positions) >= self.max_positions:
            return False, f"Max positions reached ({self.max_positions})"

        position_value = price * (self.initial_capital * self.max_position_pct / price)
        if self.cash < position_value:
            return (
                False,
                f"Insufficient cash (need {position_value:.2f}, have {self.cash:.2f})",
            )

        sector_value = self.sector_exposure.get(sector, 0.0)
        sector_limit = self.initial_capital * self.max_sector_pct
        if sector_value + position_value > sector_limit:
            return (
                False,
                f"Sector exposure limit reached ({sector}: {sector_value:.0f} + {position_value:.0f} > {sector_limit:.0f})",
            )

        return True, "OK"

    def open_position(
        self,
        code: str,
        name: str,
        price: float,
        score: float,
        sector: str,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
    ) -> Position | None:
        """Open a new position."""
        can_open, reason = self.can_open_position(code, sector, price)
        if not can_open:
            logger.warning("Cannot open position for %s: %s", code, reason)
            return None

        # Calculate position size (equal weight, max 20%)
        position_pct = min(self.max_position_pct, 1.0 / max(len(self.positions) + 1, 1))
        position_value = self.initial_capital * position_pct
        shares = int(position_value / price / 100) * 100  # Round to 100 shares

        if shares <= 0:
            logger.warning("Insufficient cash for %s", code)
            return None

        # Calculate costs
        cost = self.calculate_buy_cost(price, shares)
        if cost > self.cash:
            shares = int((self.cash * 0.99) / price / 100) * 100
            if shares <= 0:
                return None
            cost = self.calculate_buy_cost(price, shares)

        # Open position
        stop_loss = stop_loss_pct or self.stop_loss_pct
        take_profit = take_profit_pct or self.take_profit_pct

        position = Position(
            code=code,
            name=name,
            entry_date=datetime.now(),
            entry_price=price,
            shares=shares,
            cost=cost,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_score=score,
            current_score=score,
            peak_price=price,
            sector=sector,
        )

        self.positions[code] = position
        self.cash -= cost
        self.sector_exposure[sector] = self.sector_exposure.get(sector, 0.0) + position_value

        logger.info(
            "Opened position: %s (%s) at %.2f, %d shares, score %.1f",
            code,
            name,
            price,
            shares,
            score,
        )
        self._save_state()

        return position

    def close_position(
        self,
        code: str,
        price: float,
        exit_reason: str,
    ) -> Trade | None:
        """Close an existing position."""
        if code not in self.positions:
            logger.warning("No position found for %s", code)
            return None

        pos = self.positions[code]
        revenue = self.calculate_sell_revenue(price, pos.shares)
        pnl = revenue - pos.cost
        pnl_pct = pnl / pos.cost * 100

        # Create trade record
        hold_days = _trading_days_between(pos.entry_date, datetime.now())
        trade = Trade(
            code=code,
            name=pos.name,
            entry_date=pos.entry_date,
            exit_date=datetime.now(),
            entry_price=pos.entry_price,
            exit_price=price,
            shares=pos.shares,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            hold_days=hold_days,
            cost=pos.cost - pos.entry_price * pos.shares + price * pos.shares - revenue,
        )

        self.trades.append(trade)
        self.cash += revenue

        # Update sector exposure
        sector_value = self.sector_exposure.get(pos.sector, 0.0)
        position_value = pos.entry_price * pos.shares
        self.sector_exposure[pos.sector] = max(0.0, sector_value - position_value)

        # Remove position
        del self.positions[code]

        logger.info(
            "Closed position: %s (%s) at %.2f, PnL: %.2f%%, reason: %s",
            code,
            pos.name,
            price,
            pnl_pct,
            exit_reason,
        )
        self._save_state()

        return trade

    def check_exit_conditions(
        self,
        code: str,
        current_price: float,
        current_score: float | None = None,
    ) -> str | None:
        """Check if a position should be closed."""
        if code not in self.positions:
            return None

        pos = self.positions[code]
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price

        # Update peak price for trailing stop
        pos.peak_price = max(pos.peak_price, current_price)
        peak_pnl_pct = (pos.peak_price - pos.entry_price) / pos.entry_price

        # 1. Stop-loss
        stop_loss = pos.stop_loss
        if pnl_pct >= 0.02:
            stop_loss = max(stop_loss, 0.0)  # Breakeven at +2%
        if pnl_pct >= self.trailing_stop_start:
            # Trailing stop: protect trailing_stop_pct of peak profit
            trail_stop = peak_pnl_pct * self.trailing_stop_pct
            stop_loss = max(stop_loss, trail_stop)

        if pnl_pct <= -stop_loss:
            return "stop_loss"

        # 2. Take-profit
        if pnl_pct >= pos.take_profit:
            return "take_profit"

        # 3. Profit protection
        if peak_pnl_pct >= self.profit_protection_start and pnl_pct <= self.profit_protection_floor:
            return "profit_protection"

        # 4. Momentum exit (score-based)
        if current_score is not None and current_score < self.exit_threshold:
            hold_days = _trading_days_between(pos.entry_date, datetime.now())
            if hold_days >= 3:  # Allow 3 days before checking score
                return "momentum_exit"

        # 5. Max hold period (20 days)
        hold_days = _trading_days_between(pos.entry_date, datetime.now())
        if hold_days >= 20:
            # Check if still strong momentum
            if current_score is not None and current_score >= pos.entry_score * 0.8 and pnl_pct > 0:
                return None  # Extend hold
            return "hold_period"

        return None

    def update_positions(
        self,
        current_prices: dict[str, float],
        current_scores: dict[str, float] | None = None,
    ) -> list[Trade]:
        """Update all positions and close those that meet exit conditions.

        Also updates ``current_score`` on each position so the momentum exit
        (score-based) uses the latest score rather than the entry score.
        """
        closed_trades = []

        for code in list(self.positions.keys()):
            if code not in current_prices:
                continue

            price = current_prices[code]
            score = current_scores.get(code) if current_scores else None

            # Update the position's current score for momentum exit checks
            if score is not None and code in self.positions:
                self.positions[code].current_score = score

            exit_reason = self.check_exit_conditions(code, price, score)
            if exit_reason:
                trade = self.close_position(code, price, exit_reason)
                if trade:
                    closed_trades.append(trade)

        return closed_trades

    def get_portfolio_value(self, current_prices: dict[str, float]) -> float:
        """Calculate total portfolio value."""
        positions_value = sum(
            current_prices.get(pos.code, pos.entry_price) * pos.shares
            for pos in self.positions.values()
        )
        return self.cash + positions_value

    def take_snapshot(self, current_prices: dict[str, float]):
        """Take a portfolio snapshot."""
        positions_value = sum(
            current_prices.get(pos.code, pos.entry_price) * pos.shares
            for pos in self.positions.values()
        )
        total_value = self.cash + positions_value

        snapshot = PortfolioSnapshot(
            date=datetime.now(),
            cash=self.cash,
            positions_value=positions_value,
            total_value=total_value,
            positions={
                code: {
                    "shares": pos.shares,
                    "entry_price": pos.entry_price,
                    "current_price": current_prices.get(pos.code, pos.entry_price),
                    "pnl_pct": (current_prices.get(pos.code, pos.entry_price) - pos.entry_price)
                    / pos.entry_price
                    * 100,
                }
                for code, pos in self.positions.items()
            },
        )

        self.snapshots.append(snapshot)
        return snapshot

    def get_performance_metrics(self, current_prices: dict[str, float]) -> dict:
        """Calculate performance metrics."""
        if not self.trades:
            return {
                "total_return": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "trade_count": 0,
                "avg_hold_days": 0.0,
                "max_drawdown": 0.0,
            }

        # Trade statistics
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]

        win_rate = len(wins) / len(self.trades) if self.trades else 0.0
        avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

        total_pnl = sum(t.pnl for t in self.trades)
        total_return = total_pnl / self.initial_capital * 100

        avg_hold_days = sum(t.hold_days for t in self.trades) / len(self.trades)

        # Drawdown calculation
        portfolio_values = [s.total_value for s in self.snapshots]
        if portfolio_values:
            peak = portfolio_values[0]
            max_drawdown = 0.0
            for value in portfolio_values:
                if value > peak:
                    peak = value
                drawdown = (peak - value) / peak
                max_drawdown = max(max_drawdown, drawdown)
        else:
            max_drawdown = 0.0

        return {
            "total_return": total_return,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "trade_count": len(self.trades),
            "avg_hold_days": avg_hold_days,
            "max_drawdown": max_drawdown * 100,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "cash": self.cash,
            "positions_value": sum(
                current_prices.get(pos.code, pos.entry_price) * pos.shares
                for pos in self.positions.values()
            ),
            "portfolio_value": self.get_portfolio_value(current_prices),
        }

    def generate_report(self, current_prices: dict[str, float]) -> str:
        """Generate a performance report."""
        metrics = self.get_performance_metrics(current_prices)

        report = f"""
=== Paper Trading Performance Report ===
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

Portfolio Summary:
  Initial Capital: ¥{self.initial_capital:,.2f}
  Current Value: ¥{metrics.get("portfolio_value", self.initial_capital):,.2f}
  Total P&L: ¥{metrics.get("total_pnl", 0):,.2f} ({metrics.get("total_return", 0):.2f}%)
  Cash: ¥{metrics.get("cash", 0):,.2f}
  Positions Value: ¥{metrics.get("positions_value", 0):,.2f}

Trading Statistics:
  Total Trades: {metrics.get("trade_count", 0)}
  Win Rate: {metrics.get("win_rate", 0) * 100:.1f}%
  Profit Factor: {metrics.get("profit_factor", 0):.2f}
  Avg Hold Days: {metrics.get("avg_hold_days", 0):.1f}
  Avg Win: ¥{metrics.get("avg_win", 0):,.2f}
  Avg Loss: ¥{metrics.get("avg_loss", 0):,.2f}

Risk Metrics:
  Max Drawdown: {metrics.get("max_drawdown", 0):.2f}%

Current Positions ({len(self.positions)}):
"""
        for code, pos in self.positions.items():
            current_price = current_prices.get(code, pos.entry_price)
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
            report += f"  {code} ({pos.name}): Entry ¥{pos.entry_price:.2f}, Current ¥{current_price:.2f}, P&L {pnl_pct:+.2f}%\n"

        if self.trades:
            recent_trades = self.trades[-5:]
            report += f"\nRecent Trades ({len(self.trades)} total):\n"
            for trade in recent_trades:
                report += (
                    f"  {trade.code} ({trade.name}): {trade.pnl_pct:+.2f}% ({trade.exit_reason})\n"
                )

        return report
