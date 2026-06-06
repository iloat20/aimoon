"""Alpha360 时序特征 — QLib 风格的 60 天 OHLCV 展平特征。

将每只股票最近 60 天的 OHLCV 数据归一化后展平为 360 个特征列，
补充 Alpha Zoo 百分位因子的截面信息。
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_ALPHA360_WINDOW = 60
_ALPHA360_COLUMNS = ("open", "high", "low", "close", "volume", "vwap")


def extract_alpha360_features(
    panel: dict[str, pd.DataFrame],
    target_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """提取 Alpha360 时序特征矩阵。

    对每只股票：
    1. 取最近 60 天的 OHLCV + VWAP 数据
    2. 除以第 0 天的 close 归一化（价格归一化）
    3. volume 除以 60 天均值归一化（量归一化）
    4. 展平为 360 个特征列

    Parameters
    ----------
    panel : dict[str, pd.DataFrame]
        Alpha Zoo 宽表，需含 open/high/low/close/volume。
    target_date : pd.Timestamp | None
        截面日期。None 则取最后一行。

    Returns
    -------
    pd.DataFrame
        index=stock codes, columns=a360_open_0..a360_vol_59.
        Empty if insufficient data.
    """
    if panel is None or "close" not in panel:
        return pd.DataFrame()

    close = panel["close"]
    if len(close) < _ALPHA360_WINDOW + 5:
        return pd.DataFrame()

    codes = list(close.columns)
    if len(codes) < 5:
        return pd.DataFrame()

    # 确定目标日期
    if target_date is not None and target_date in close.index:
        end_idx = close.index.get_loc(target_date)
    else:
        end_idx = len(close) - 1

    start_idx = end_idx - _ALPHA360_WINDOW + 1
    if start_idx < 0:
        return pd.DataFrame()

    feature_dicts: dict[str, dict[str, float]] = {}

    for code in codes:
        features: dict[str, float] = {}
        valid = True

        # 归一化基准：第 0 天的 close
        close_series = close[code].iloc[start_idx : end_idx + 1]
        if close_series.isna().any() or len(close_series) < _ALPHA360_WINDOW:
            continue
        base_price = float(close_series.iloc[0])
        if base_price <= 0:
            continue

        for col_name in ("open", "high", "low", "close"):
            df = panel.get(col_name)
            if df is None or code not in df.columns:
                valid = False
                break
            series = df[code].iloc[start_idx : end_idx + 1]
            if series.isna().any() or len(series) < _ALPHA360_WINDOW:
                valid = False
                break
            # 价格归一化：除以第 0 天 close
            normalized = series.values / base_price
            for i, val in enumerate(normalized):
                features[f"a360_{col_name}_{i}"] = float(val)

        if not valid:
            continue

        # Volume 归一化：除以 60 天均值
        vol_df = panel.get("volume")
        if vol_df is not None and code in vol_df.columns:
            vol_series = vol_df[code].iloc[start_idx : end_idx + 1]
            if len(vol_series) == _ALPHA360_WINDOW and not vol_series.isna().all():
                vol_mean = float(vol_series.mean())
                vol_norm = vol_series.values / vol_mean if vol_mean > 0 else vol_series.values
                for i, val in enumerate(vol_norm):
                    features[f"a360_volume_{i}"] = float(val)

        # VWAP（如果可用）：归一化同价格
        vwap_df = panel.get("vwap") or panel.get("amount")
        if vwap_df is not None and code in vwap_df.columns:
            vwap_series = vwap_df[code].iloc[start_idx : end_idx + 1]
            if len(vwap_series) == _ALPHA360_WINDOW and not vwap_series.isna().all():
                vwap_norm = vwap_series.values / base_price
                for i, val in enumerate(vwap_norm):
                    features[f"a360_vwap_{i}"] = float(val)
            else:
                # 用 (high+low+close)/3 作为 VWAP 代理
                if "a360_high_0" in features:
                    for i in range(_ALPHA360_WINDOW):
                        h = features.get(f"a360_high_{i}", 1.0)
                        l = features.get(f"a360_low_{i}", 1.0)
                        c = features.get(f"a360_close_{i}", 1.0)
                        features[f"a360_vwap_{i}"] = (h + l + c) / 3.0

        feature_dicts[code] = features

    if not feature_dicts:
        return pd.DataFrame()

    result = pd.DataFrame.from_dict(feature_dicts, orient="index")
    result = result.fillna(0)
    logger.info("Alpha360: %d stocks × %d features", len(result), result.shape[1])
    return result
