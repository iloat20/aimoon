"""Genetic Programming factor mining using gplearn.

Automatically discovers alpha factors by evolving mathematical expressions.

Usage:
    from aimoon.factors.genetic import GeneticFactorMiner
    miner = GeneticFactorMiner(population_size=500, generations=3)
    factors = miner.fit(close, volume, returns)
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np
import pandas as pd

from aimoon.config import Config

logger = logging.getLogger(__name__)

try:
    from gplearn.functions import make_function
    from gplearn.genetic import SymbolicRegressor

    _HAS_GPLEARN = True
except ImportError:
    _HAS_GPLEARN = False


def _safe_div(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Safe division avoiding divide-by-zero."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return np.where(np.abs(y) > 1e-10, x / y, 0.0)


def _safe_log(x: np.ndarray) -> np.ndarray:
    """Safe log avoiding log(0)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return np.where(np.abs(x) > 1e-10, np.log(np.abs(x)), 0.0)


def _safe_sqrt(x: np.ndarray) -> np.ndarray:
    """Safe sqrt avoiding sqrt(negative)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return np.sqrt(np.clip(x, 0, None))


def _ts_mean(x: np.ndarray, period: int) -> np.ndarray:
    """Rolling mean over period."""
    s = pd.Series(x)
    return s.rolling(period, min_periods=max(1, period // 2)).mean().fillna(0).values


def _ts_std(x: np.ndarray, period: int) -> np.ndarray:
    """Rolling std over period."""
    s = pd.Series(x)
    return s.rolling(period, min_periods=max(1, period // 2)).std().fillna(0).values


def _rank(x: np.ndarray) -> np.ndarray:
    """Cross-sectional rank (percentile)."""
    s = pd.Series(x)
    return s.rank(pct=True).values


# Custom functions for gplearn
_safe_div_fn = make_function(
    function=_safe_div,
    name="safe_div",
    arity=2,
)
_safe_log_fn = make_function(
    function=_safe_log,
    name="safe_log",
    arity=1,
)
_safe_sqrt_fn = make_function(
    function=_safe_sqrt,
    name="safe_sqrt",
    arity=1,
)
_ts_mean_5_fn = make_function(
    function=lambda x: _ts_mean(x, 5),
    name="ts_mean_5",
    arity=1,
)
_ts_mean_10_fn = make_function(
    function=lambda x: _ts_mean(x, 10),
    name="ts_mean_10",
    arity=1,
)
_ts_mean_20_fn = make_function(
    function=lambda x: _ts_mean(x, 20),
    name="ts_mean_20",
    arity=1,
)
_ts_std_5_fn = make_function(
    function=lambda x: _ts_std(x, 5),
    name="ts_std_5",
    arity=1,
)
_ts_std_10_fn = make_function(
    function=lambda x: _ts_std(x, 10),
    name="ts_std_10",
    arity=1,
)
_ts_std_20_fn = make_function(
    function=lambda x: _ts_std(x, 20),
    name="ts_std_20",
    arity=1,
)
_rank_fn = make_function(
    function=_rank,
    name="rank",
    arity=1,
)


class GeneticFactorMiner:
    """Genetic Programming-based factor miner.

    Evolves mathematical expressions to find predictive alpha factors.

    Parameters
    ----------
    population_size : int
        Number of programs per generation.
    generations : int
        Number of evolutionary generations.
    max_depth : int
        Maximum expression tree depth.
    function_set : list, optional
        Custom function set for evolution.
    random_state : int
        Random seed for reproducibility.
    """

    def __init__(
        self,
        population_size: int = 500,
        generations: int = 3,
        max_depth: int = 4,
        random_state: int = 42,
        cfg: Config | None = None,
    ) -> None:
        self.population_size = population_size
        self.generations = generations
        self.max_depth = max_depth
        self.random_state = random_state
        self.cfg = cfg or Config()
        self._best_programs: list[dict[str, Any]] = []

    def _default_function_set(self) -> list:
        """Default function set for factor evolution."""
        return [
            "add",
            "sub",
            "mul",
            _safe_div_fn,
            _safe_log_fn,
            _safe_sqrt_fn,
            _ts_mean_5_fn,
            _ts_mean_10_fn,
            _ts_mean_20_fn,
            _ts_std_5_fn,
            _ts_std_10_fn,
            _ts_std_20_fn,
            _rank_fn,
        ]

    def fit(
        self,
        close: pd.Series,
        volume: pd.Series,
        returns: pd.Series,
        n_best: int = 5,
    ) -> list[dict[str, Any]]:
        """Run genetic programming to discover factors.

        Parameters
        ----------
        close : pd.Series
            Close prices (time series).
        volume : pd.Series
            Volume (time series).
        returns : pd.Series
            Forward returns (labels for fitness).
        n_best : int
            Number of best factors to return.

        Returns
        -------
        list[dict]
            Each dict has keys: "expression", "fitness", "program".
        """
        if not _HAS_GPLEARN:
            logger.warning("gplearn not installed, skipping genetic factor mining")
            return []

        X = np.column_stack([close.values, volume.values])
        y = returns.values

        # Remove NaN rows
        valid = ~(np.isnan(X).any(axis=1) | np.isnan(y))
        X = X[valid]
        y = y[valid]

        if len(X) < 50:
            logger.warning("Insufficient data for genetic programming (%d samples)", len(X))
            return []

        est = SymbolicRegressor(
            population_size=self.population_size,
            generations=self.generations,
            max_samples=0.9,
            verbose=0,
            random_state=self.random_state,
            function_set=self._default_function_set(),
            parsimony_coefficient=0.001,
            max_depth=self.max_depth,
            n_jobs=-1,
        )

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                est.fit(X, y)
        except Exception as e:
            logger.warning("Genetic programming failed: %s", e)
            return []

        # Extract best programs
        programs = []
        for prog in est._programs:
            if hasattr(prog, "fitness_") and hasattr(prog, "__str__"):
                programs.append(
                    {
                        "expression": str(prog),
                        "fitness": float(prog.fitness_),
                        "depth": int(prog.depth_),
                        "length": int(prog.length_),
                    }
                )

        programs.sort(key=lambda p: p["fitness"], reverse=True)
        self._best_programs = programs[:n_best]

        logger.info(
            "Genetic mining complete: %d programs, best fitness=%.4f",
            len(programs),
            programs[0]["fitness"] if programs else 0.0,
        )

        return self._best_programs

    def transform(
        self,
        close: pd.Series,
        volume: pd.Series,
        expression: str | None = None,
    ) -> pd.Series:
        """Apply a discovered factor expression to new data.

        Parameters
        ----------
        close : pd.Series
            Close prices.
        volume : pd.Series
            Volume.
        expression : str, optional
            Factor expression string. If None, uses the best factor.

        Returns
        -------
        pd.Series
            Factor values.
        """
        if not self._best_programs and expression is None:
            return pd.Series(0.0, index=close.index)

        if expression is None:
            expression = self._best_programs[0]["expression"]

        # Simple evaluation using numpy
        # For production use, parse the expression tree
        try:
            result = eval(
                expression,
                {
                    "add": np.add,
                    "sub": np.sub,
                    "mul": np.mul,
                    "close": close.values,
                    "volume": volume.values,
                    "np": np,
                },
            )
            return pd.Series(result, index=close.index)
        except Exception:
            return pd.Series(0.0, index=close.index)

    @staticmethod
    def is_available() -> bool:
        """Check if gplearn is installed."""
        return _HAS_GPLEARN
