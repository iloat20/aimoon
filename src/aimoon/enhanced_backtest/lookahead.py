"""Look-ahead bias prevention — runtime assertions for backtest data windows."""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


class LookAheadError(AssertionError):
    """Raised when a data window contains dates after the allowed cutoff."""


def assert_no_lookahead(
    data: pd.DataFrame | dict,
    cutoff_date: pd.Timestamp,
    name: str = "data",
) -> None:
    """Assert that *data* contains no rows/index entries after *cutoff_date*.

    Parameters
    ----------
    data : pd.DataFrame | dict
        DataFrame (checked by index) or dict of DataFrames (each checked).
    cutoff_date : pd.Timestamp
        Exclusive upper bound — entries >= cutoff_date trigger an error.
    name : str
        Label for error messages.

    Raises
    ------
    LookAheadError
        If any entry is on or after *cutoff_date*.
    """
    if isinstance(data, dict):
        for key, df in data.items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                _check_df(df, cutoff_date, f"{name}[{key}]")
    elif isinstance(data, pd.DataFrame) and not data.empty:
        _check_df(data, cutoff_date, name)


def _check_df(
    df: pd.DataFrame,
    cutoff: pd.Timestamp,
    label: str,
) -> None:
    max_date = df.index.max()
    if max_date is not None and max_date >= cutoff:
        n_future = (df.index >= cutoff).sum()
        logger.error(
            "Look-ahead detected in %s: %d rows >= cutoff %s (max=%s)",
            label,
            n_future,
            cutoff.date(),
            max_date.date() if hasattr(max_date, "date") else max_date,
        )
        raise LookAheadError(
            f"{label}: {n_future} rows >= cutoff {cutoff.date()}"
        )


def filter_to_cutoff(
    df: pd.DataFrame,
    cutoff_date: pd.Timestamp,
    name: str = "data",
) -> pd.DataFrame:
    """Return a copy of *df* containing only rows before *cutoff_date*.

    Logs a warning if any rows are dropped.
    """
    mask = df.index < cutoff_date
    n_dropped = int((~mask).sum())
    if n_dropped > 0:
        logger.debug(
            "%s: dropping %d rows >= cutoff %s",
            name,
            n_dropped,
            cutoff_date.date(),
        )
    return df.loc[mask].copy()
