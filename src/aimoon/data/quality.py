
"""Data quality checks and cleaning for K-line data."""
from __future__ import annotations
import logging
import pandas as pd

logger = logging.getLogger(__name__)


def validate_kline(df: pd.DataFrame, min_rows: int = 60) -> tuple:
    issues = []
    if df is None or df.empty:
        issues.append("Empty DataFrame")
        return False, issues
    if len(df) < min_rows:
        issues.append(f"Too few rows: {len(df)} < {min_rows}")
        return False, issues
    required_cols = ["open", "close", "high", "low", "volume"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        issues.append(f"Missing columns: {missing}")
        return False, issues
    for col in required_cols:
        recent_nans = df[col].iloc[-5:].isna().sum()
        if recent_nans > 0:
            issues.append(f"{col}: {recent_nans} NaN in last 5 rows")
    zero_vol = (df["volume"].iloc[-5:] <= 0).sum()
    if zero_vol > 3:
        issues.append(f"Suspicious: {zero_vol} zero-volume days in last 5")
    if len(df) > 1:
        pct = df["close"].pct_change()
        extreme = (pct.abs() > 0.20).sum()
        if extreme > 0:
            issues.append(f"{extreme} days with >20% price change")
    if (df["high"] < df["low"]).any():
        issues.append("high < low detected")
    is_valid = len(issues) == 0
    return is_valid, issues


def clean_kline(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    result = df.copy()
    for col in ["open", "close", "high", "low", "volume"]:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")
    result["high"] = result[["open", "close", "high"]].max(axis=1)
    result["low"] = result[["open", "close", "low"]].min(axis=1)
    result = result.ffill(limit=2)
    result = result.dropna(subset=["close"])
    return result
