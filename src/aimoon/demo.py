"""Demo 模式模拟数据生成"""
from __future__ import annotations
import numpy as np
import pandas as pd


def generate_demo() -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    np.random.seed(42)
    stocks = [
        ("000001", "PingAnBank"), ("000002", "VankeA"),
        ("000858", "Wuliangye"), ("000725", "BOE"),
        ("002415", "Hikvision"), ("002594", "BYD"),
        ("300750", "CATL"), ("600036", "CMB"),
        ("600519", "Moutai"), ("600887", "Yili"),
        ("601318", "PingAn"), ("601398", "ICBC"),
        ("000333", "Midea"), ("002475", "Luxshare"),
        ("300059", "EastMoney"), ("002714", "Muyuan"),
        ("600276", "Hengrui"), ("601888", "ChinaTour"),
        ("000568", "Luzhou"), ("002304", "Yanghe"),
        ("600309", "Wanhua"), ("601166", "CIB"),
        ("600030", "CITIC"), ("000651", "Gree"),
        ("002049", "Unigroup"), ("300124", "Inovance"),
        ("002230", "iFlytek"), ("600585", "Conch"),
        ("601012", "LONGi"), ("000100", "TCL"),
    ]
    rows = []
    for code, name in stocks:
        price = float(np.random.uniform(10, 200))
        rows.append({
            "stock_code": code, "stock_name": name, "price": price,
            "pct_change": float(np.random.uniform(-5, 5)),
            "turnover": float(np.random.uniform(1, 15)),
            "volume": float(np.random.randint(100000, 10000000)),
            "amount": float(np.random.randint(10000000, 1000000000)),
            "amplitude": float(np.random.uniform(1, 8)),
            "high": price * 1.02, "low": price * 0.98,
            "open": price * 1.001, "prev_close": price * 0.99,
            "volume_ratio": float(np.random.uniform(0.5, 3)),
            "pe": float(np.random.uniform(5, 50)),
            "pb": float(np.random.uniform(0.5, 10)),
            "total_market_cap": float(np.random.uniform(5e9, 3e12)),
            "float_market_cap": float(np.random.uniform(1e9, 2e12)),
            "pct_60d": float(np.random.uniform(-30, 30)),
            "pct_ytd": float(np.random.uniform(-20, 50)),
        })
    spot_df = pd.DataFrame(rows)
    klines = {}
    for code, name in stocks:
        n = 260
        dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
        c = np.random.uniform(10, 200)
        close = np.maximum(c + np.cumsum(np.random.randn(n) * c * 0.02), 1.0)
        high = close + np.abs(np.random.randn(n) * close * 0.02)
        low = close - np.abs(np.random.randn(n) * close * 0.02)
        open_ = close + np.random.randn(n) * close * 0.01
        vol = np.random.randint(100000, 10000000, n).astype(float)
        df = pd.DataFrame({
            "open": open_, "close": close, "high": high, "low": low,
            "volume": vol, "turnover": np.random.uniform(0.5, 15, n),
            "pct_change": np.random.randn(n) * 3,
        }, index=dates)
        df.index.name = "date"
        klines[code] = df
    return spot_df, klines
