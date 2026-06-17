"""Extract feature matrix from A-share panel for ML training/inference.

精简重写：删除 Alpha Zoo / Alpha360 / ICIR / 中性化 / SVD。
新设计 = 11 A-share 因子 z-score + 6 维技术统计 ≈ 18 特征。
"""

from __future__ import annotations

import logging

import pandas as pd

from aimoon.factors.ashare import compute_ashare_factors, robust_zscore

logger = logging.getLogger(__name__)


def extract_features(
    panel: dict[str, pd.DataFrame],
    target_date: pd.Timestamp | None = None,
    sector_map: dict[str, str] | None = None,
    fundamentals: dict[str, pd.DataFrame] | None = None,
    feature_medians: pd.Series | None = None,
) -> pd.DataFrame:
    """Extract feature matrix for ML training/inference.

    1. 从 compute_ashare_factors 切片目标日 → 11 因子截面（稳健 z-score）
    2. + 基础技术统计：5/10/20d 波动率、5/10/20d 收益率（6 维）
    3. 训练时保存 feature_medians（推理填 NaN 用）

    Args:
        panel: build_panel 输出的宽表 {field: DataFrame(日期×股票)}。
        target_date: 目标日期（推理时必传；训练时为 None 则用 panel 最后一天）。
        sector_map: {code: sector_name}，传给 compute_ashare_factors。
        fundamentals: {pe|pb|dividend: DataFrame(日期×股票)}，传给 compute_ashare_factors。
        feature_medians: 训练时保存的中位数（推理时填 NaN）。

    Returns:
        pd.DataFrame: index=code, columns=feature_name, dtype=float.
                      features 不足 5 时返回空 DataFrame。
    """
    close = panel.get("close")
    if close is None or close.empty:
        return pd.DataFrame()

    # 确定目标日期
    date = target_date or close.index[-1]

    # 提取基本面面板参数
    pe = fundamentals.get("pe") if fundamentals else None
    pb = fundamentals.get("pb") if fundamentals else None
    dividend = fundamentals.get("dividend") if fundamentals else None

    # 1. 计算全部 11 因子时间序列
    all_factors = compute_ashare_factors(
        panel,
        sector_map=sector_map,
        pe=pe,
        pb=pb,
        dividend=dividend,
    )
    if not all_factors:
        return pd.DataFrame()

    # 2. 切片目标日期截面作为因子特征
    codes_list = list(close.columns)
    result_dict: dict[str, dict[str, float]] = {code: {} for code in codes_list}

    for fid, factor_df in all_factors.items():
        if date in factor_df.index:
            row = factor_df.loc[date]
            for code in codes_list:
                if code in row.index:
                    val = row[code]
                    if pd.notna(val):
                        result_dict[code][fid] = float(val)

    # 3. 基础技术统计（6 维）
    if date in close.index:
        idx = close.index.get_loc(date)
        for code in codes_list:
            s = close[code].dropna()
            if len(s) < 5:
                continue
            for window, suffix in [(5, "5d"), (10, "10d"), (20, "20d")]:
                start = max(0, idx - window)
                segment = s.iloc[start:idx] if idx > 0 else s.iloc[-window:]
                if len(segment) < 2:
                    continue
                rets = segment.pct_change().dropna()
                if len(rets) < 1:
                    continue
                result_dict[code][f"tech_volatility_{suffix}"] = float(rets.std())
                result_dict[code][f"tech_return_{suffix}"] = float(rets.mean())

    result = pd.DataFrame.from_dict(result_dict, orient="index")

    # 4. 推理时使用训练中位数填 NaN
    if feature_medians is not None:
        medians = feature_medians.reindex(result.columns, fill_value=0.0)
        result = result.fillna(medians)
    else:
        result = result.fillna(result.median())

    # 稳健 z-score 确保特征尺度一致
    result = result.apply(lambda s: robust_zscore(s, clip=3.0), axis=0)

    if result.shape[1] < 5:
        logger.warning("Feature count low (%d)", result.shape[1])
        return pd.DataFrame()

    logger.debug(
        "extract_features: %d codes, %d features at %s",
        len(result),
        result.shape[1],
        date,
    )
    return result
