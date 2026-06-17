"""?????????? ? ?? paper_trading.py ? while-sleep ?????

??:
    MarketDataFeed (WebSocket/HTTP) -> SignalEngine -> OrderManager -> PaperTradingEngine

????:
    1. asyncio ?????? time.sleep ??
    2. ????????????
    3. ???????
    4. ??????????????

????:
    engine = PaperTradingEngine(...)
    framework = AsyncTradingFramework(engine, config)
    asyncio.run(framework.run())
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import pandas as pd

from aimoon.config import Config
from aimoon.result import Err, Ok, Result

logger = logging.getLogger(__name__)


class MarketDataFeed(Protocol):
    """????????"""

    async def connect(self) -> None:
        """?????"""
        ...

    async def disconnect(self) -> None:
        """?????"""
        ...

    async def subscribe(self, codes: list[str], callback: Callable[..., Any]) -> None:
        """???????"""
        ...

    async def get_snapshot(self, codes: list[str]) -> dict[str, float]:
        """?????????"""
        ...


@dataclass
class Order:
    """???"""

    code: str
    action: str  # "buy" or "sell"
    price: float
    shares: int
    timestamp: datetime = field(default_factory=datetime.now)
    filled: bool = False
    fill_price: float = 0.0


@dataclass
class MarketEvent:
    """?????"""

    type: str  # "quote", "signal", "order_fill"
    code: str
    data: dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)


class SignalEngine:
    """???????"""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    async def compute_signals(
        self,
        code: str,
        kline: pd.DataFrame,
    ) -> dict[str, float]:
        """??????????????"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._compute_sync,
            code,
            kline,
        )

    def _compute_sync(
        self,
        code: str,
        kline: pd.DataFrame,
    ) -> dict[str, float]:
        """????????????????"""
        from aimoon.indicators.technical import TechInd
        from aimoon.scoring import collect_signals

        result: dict[str, float] = {"score": 50.0, "signals": 0.0}
        if kline is None or len(kline) < 60:
            return result
        try:
            ti = TechInd(kline)
            signals = collect_signals(ti, code=code)
            if signals:
                from aimoon.scoring import hybrid_score

                score = hybrid_score(signals)
                result["score"] = float(score)
                result["signals"] = float(len(signals))
        except Exception as e:
            logger.debug("Signal computation failed for %s: %s", code, e)
        return result

    async def compute_batch(
        self,
        klines: dict[str, pd.DataFrame],
        max_concurrent: int = 10,
    ) -> dict[str, dict[str, float]]:
        """???????????"""
        semaphore = asyncio.Semaphore(max_concurrent)
        results: dict[str, dict[str, float]] = {}

        async def _compute_one(code: str, kline: pd.DataFrame) -> tuple[str, dict[str, float]]:
            async with semaphore:
                r = await self.compute_signals(code, kline)
                return code, r

        tasks = [_compute_one(c, k) for c, k in klines.items()]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, Exception):
                continue
            code, signal_data = item
            results[code] = signal_data

        return results


class OrderManager:
    """????????"""

    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0) -> None:
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._pending: list[Order] = []
        self._filled: list[Order] = []

    async def submit(self, order: Order) -> Result[Order, str]:
        """??????????"""
        for attempt in range(self._max_retries):
            try:
                fill_price = await self._execute(order)
                order.filled = True
                order.fill_price = fill_price
                self._filled.append(order)
                return Ok(order)
            except Exception as e:
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._retry_delay * (attempt + 1))
                else:
                    return Err(f"Order failed after {self._max_retries} retries: {e}")

    async def _execute(self, order: Order) -> float:
        """???????????"""
        await asyncio.sleep(0.01)  # ??????
        return order.price

    @property
    def pending_orders(self) -> list[Order]:
        return list(self._pending)

    @property
    def filled_orders(self) -> list[Order]:
        return list(self._filled)


class AsyncTradingFramework:
    """???????????"""

    def __init__(
        self,
        engine: Any,
        config: Config | None = None,
        signal_engine: SignalEngine | None = None,
        order_manager: OrderManager | None = None,
    ) -> None:
        self._engine = engine
        self._config = config or Config()
        self._signal_engine = signal_engine or SignalEngine(self._config)
        self._order_manager = order_manager or OrderManager()
        self._running = False
        self._klines: dict[str, pd.DataFrame] = {}
        self._current_prices: dict[str, float] = {}
        self._current_scores: dict[str, float] = {}
        self._last_signal_time: float = 0.0
        self._signal_interval: float = 60.0  # ?????????

    def update_klines(self, klines: dict[str, pd.DataFrame]) -> None:
        """?? K ????"""
        self._klines = klines

    async def run(self) -> None:
        """??????????"""
        self._running = True
        logger.info("AsyncTradingFramework started")

        try:
            while self._running:
                loop_start = time.time()

                await self._tick()

                elapsed = time.time() - loop_start
                sleep_time = max(0.1, self._signal_interval - elapsed)
                await asyncio.sleep(sleep_time)
        except asyncio.CancelledError:
            logger.info("AsyncTradingFramework cancelled")
        finally:
            self._running = False
            logger.info("AsyncTradingFramework stopped")

    async def _tick(self) -> None:
        """???????"""
        # 1. ??????
        if self._current_prices:
            self._engine.update_positions(
                self._current_prices,
                self._current_scores,
            )

        # 2. ??????
        now = time.time()
        if now - self._last_signal_time >= self._signal_interval and self._klines:
            self._last_signal_time = now
            await self._update_signals()

    async def _update_signals(self) -> None:
        """???????????"""
        positions_klines = {
            code: self._klines[code] for code in self._engine.positions if code in self._klines
        }
        if not positions_klines:
            return

        results = await self._signal_engine.compute_batch(positions_klines)
        for code, data in results.items():
            self._current_scores[code] = data.get("score", 50.0)

    async def on_price_update(
        self,
        prices: dict[str, float],
    ) -> list[Any]:
        """???????"""
        self._current_prices = prices
        closed_trades = self._engine.update_positions(prices, self._current_scores)
        return closed_trades

    async def on_signal_update(
        self,
        code: str,
        kline: pd.DataFrame,
    ) -> None:
        """???????"""
        result = await self._signal_engine.compute_signals(code, kline)
        self._current_scores[code] = result.get("score", 50.0)

    def stop(self) -> None:
        """???????"""
        self._running = False

    def get_status(self) -> dict[str, Any]:
        """Get framework status."""
        engine = self._engine
        return {
            "running": self._running,
            "positions": len(engine.positions) if engine else 0,
            "trades": len(engine.trades) if engine else 0,
            "pending_orders": len(self._order_manager.pending_orders),
            "filled_orders": len(self._order_manager.filled_orders),
            "signal_cache_size": len(self._current_scores),
        }


async def run_async_backtest(
    engine: Any,
    klines: dict[str, pd.DataFrame],
    config: Config | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """???????????"""

    config = config or Config()
    framework = AsyncTradingFramework(engine, config)
    framework.update_klines(klines)

    # ??????
    all_dates: set[str] = set()
    for kdf in klines.values():
        all_dates.update(str(d)[:10] for d in kdf.index)
    sorted_dates = sorted(all_dates)

    if not sorted_dates:
        return {"error": "No dates available"}

    # ?????
    total_ticks = min(days, len(sorted_dates))

    for tick in range(total_ticks):
        date = sorted_dates[tick]
        prices = {}
        for code, kdf in klines.items():
            if date in [str(d)[:10] for d in kdf.index]:
                idx = [str(d)[:10] for d in kdf.index].index(date)
                prices[code] = float(kdf.iloc[idx]["close"])

        if prices:
            await framework.on_price_update(prices)

        await asyncio.sleep(0.01)  # ?????

    return framework.get_status()
