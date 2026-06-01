"""mootdx 数据源 — 通达信 TCP 协议直连，无 HTTP 限流。

mootdx (https://github.com/mootdx/mootdx) 使用通达信原生二进制协议，
不受 HTTP 抓取限流影响。公开行情数据，无需 token，无 IP 限制。

范围：仅 A 股日线（沪/深自动识别，北交所不支持）。
"""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

import pandas as pd

from aimoon.result import Err, Ok, Result

logger = logging.getLogger(__name__)

# TCP 连接比 HTTP 更重，限制并发
_mootdx_semaphore = threading.Semaphore(3)
_tdx_client = None
_tdx_lock = threading.Lock()


def _get_client():
    """线程安全懒初始化 mootdx Quotes 客户端（双重检查锁）。"""
    global _tdx_client
    if _tdx_client is None:
        with _tdx_lock:
            if _tdx_client is None:
                from mootdx.quotes import Quotes
                _tdx_client = Quotes.factory(market="std")
    return _tdx_client


def _is_bj(code: str) -> bool:
    """检测北交所股票（4xxxxx/8xxxxx）。mootdx 不支持北交所。"""
    return len(code) == 6 and code.isdigit() and code[0] in ("4", "8")


def mootdx_kline(code: str, days: int) -> Result[pd.DataFrame, str]:
    """通过 mootdx TCP 协议获取日线数据。

    失败时返回 Err（未安装、北交所、TDX 返回空数据等）。
    """
    if _is_bj(code):
        return Err(f"{code}: mootdx 不支持北交所")

    try:
        import mootdx  # noqa: F401
    except ImportError:
        return Err("mootdx 未安装")

    try:
        with _mootdx_semaphore:
            client = _get_client()
            end = date.today().strftime("%Y-%m-%d")
            start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
            df = client.get_k_data(code=code, start_date=start, end_date=end)

        if df is None or df.empty:
            return Err(f"{code}: mootdx 返回空数据")

        return Ok(_normalize(df))
    except Exception as e:
        return Err(f"{code}: mootdx 失败: {e}")


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """将 mootdx 输出规范化为 aimoon 的 K 线格式。

    get_k_data 返回: index=date, columns=[open, close, high, low, vol, amount, code]
    aimoon 期望: index=date, columns=[open, close, high, low, volume, amount, amplitude, pct_change, change, turnover]
    """
    out = df.rename(columns={"vol": "volume"}).copy()
    out.index = pd.to_datetime(out.index)
    out.index.name = "date"
    for col in ("open", "high", "low", "close", "volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    # mootdx 不返回 amount/amplitude/pct_change/change/turnover，用 0.0 填充
    # （与 Tencent 备用方案一致）
    for col in ("amount", "amplitude", "pct_change", "change", "turnover"):
        if col not in out.columns:
            out[col] = 0.0
    return out.sort_index()
