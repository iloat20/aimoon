"""AlphaModel that wraps aimoon ML scores and technical signals."""

from __future__ import annotations

import logging
from typing import Any

from aimoon.qf_backtest.imports import QF_AVAILABLE

if QF_AVAILABLE:
    from qf_lib.backtesting.alpha_model.alpha_model import AlphaModel
    from qf_lib.backtesting.alpha_model.exposure_enum import Exposure
    from qf_lib.data_providers.data_provider import DataProvider

logger = logging.getLogger(__name__)


class AimoonAlphaModel(AlphaModel if QF_AVAILABLE else object):  # type: ignore[misc]
    """Alpha model that uses pre-computed ML scores and technical signals.

    The model receives pre-computed daily ML scores and alpha signals
    from aimoon's screening pipeline, and converts them into QF-Lib
    Exposure signals.
    """

    def __init__(
        self,
        risk_estimation_factor: float,
        data_provider: DataProvider,
        ml_scores_by_date: dict[str, dict[str, int]],
        entry_threshold: int = 50,
    ) -> None:
        if not QF_AVAILABLE:
            raise ImportError("qf-lib is required but not installed")
        super().__init__(risk_estimation_factor, data_provider)
        self._ml_scores_by_date: dict[str, dict[str, int]] = ml_scores_by_date
        self._entry_threshold: int = entry_threshold

    def calculate_exposure(
        self,
        ticker: Any,
        current_exposure: Exposure,
        current_time: Any,
        frequency: Any,
    ) -> Exposure:
        """Return LONG if the stock's ML score >= entry_threshold on the given date."""
        date_str = str(current_time)[:10]
        scores = self._ml_scores_by_date.get(date_str, {})
        score = scores.get(ticker.as_string, 0)
        if score >= self._entry_threshold:
            return Exposure.LONG
        return Exposure.OUT

    def calculate_fraction_at_risk(self, ticker: Any) -> float:
        """Return risk fraction (simplified ATR-based or fixed)."""
        return 0.02  # 2% risk per trade (simplified)

    def get_signal(
        self,
        ticker: Any,
        current_exposure: Exposure,
        current_time: Any,
        frequency: Any,
    ) -> Any:
        """Override to attach confidence based on ML score."""
        from qf_lib.backtesting.alpha_model.signal import Signal

        exposure = self.calculate_exposure(ticker, current_exposure, current_time, frequency)
        fraction = self.calculate_fraction_at_risk(ticker)
        date_str = str(current_time)[:10]
        scores = self._ml_scores_by_date.get(date_str, {})
        score = scores.get(ticker.as_string, 0)
        confidence = score / 100.0 if score > 0 else 0.0
        return Signal(exposure, fraction, confidence, 0.0)
