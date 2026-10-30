"""--demo 模擬資料：不連網，供測試與儀表板預覽。

產生兩種資料，讓 demo 走與正式流程相同的解析、過濾與判斷：
- OpenAPI STOCK_DAY_ALL 格式的當日行情（字串欄位、民國日期）
- yfinance 格式的日線 OHLCV（量為股）
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

# 應被股票池過濾掉的代號：ETF、槓桿 ETF、特別股、4 位數存託憑證
EXCLUDED = [("0050", "元大台灣50"), ("00631L", "元大台灣50正2"), ("1101B", "台泥乙特"), ("9105", "泰金寶-DR")]


def roc_date(d: date) -> str:
    return f"{d.year - 1911:03d}{d.month:02d}{d.day:02d}"


def _series(rng: np.random.Generator, days: pd.DatetimeIndex) -> pd.DataFrame:
    n = len(days)
    t = np.arange(n)
    # 兩組週期性趨勢疊加雜訊，讓月、週、日線都會出現多空循環
    trend = 0.0012 * np.sin(2 * np.pi * t / rng.uniform(300, 700) + rng.uniform(0, 6.3))
    swing = 0.0025 * np.sin(2 * np.pi * t / rng.uniform(25, 70) + rng.uniform(0, 6.3))
    ret = trend + swing + rng.normal(rng.uniform(-0.0002, 0.0006), rng.uniform(0.010, 0.022), n)
    close = rng.uniform(15, 600) * np.exp(np.cumsum(ret))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    lots = rng.lognormal(np.log(rng.uniform(150, 8000)), 0.5, n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": np.round(lots * 1000)},
        index=days,
    )


def demo_dataset(trade_date: date, n: int = 200, years: int = 8, seed: int = 20261030):
    """回傳（代號→名稱, 代號→日線）。少數個股刻意設為歷史不足或資料落後。"""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=pd.Timestamp(trade_date), periods=years * 252)
    names: dict[str, str] = {}
    histories: dict[str, pd.DataFrame] = {}
    for i in range(n):
        code = str(1101 + i * 41)
        names[code] = f"示範{code}"
        df = _series(rng, days)
        if i % 37 == 5:
            df = df.iloc[-3 * 252 :]  # 上市未滿 60 個月
        elif i % 53 == 7:
            df = df.iloc[:-1]  # 最新一日缺資料
        histories[code] = df
    return names, histories


def stock_day_all(day: date, names: dict[str, str], histories: dict[str, pd.DataFrame]) -> list[dict]:
    """模擬證交所 STOCK_DAY_ALL 回傳內容。"""
    ts = pd.Timestamp(day)
    records = []
    for code, df in histories.items():
        bars = df[df.index <= ts]
        if bars.empty:
            continue
        bar = bars.iloc[-1]
        records.append(
            {
                "Date": roc_date(day),
                "Code": code,
                "Name": names[code],
                "TradeVolume": str(int(bar["Volume"])),
                "TradeValue": str(int(bar["Volume"] * bar["Close"])),
                "ClosingPrice": f"{bar['Close']:.2f}",
            }
        )
    for code, name in EXCLUDED:
        records.append(
            {"Date": roc_date(day), "Code": code, "Name": name, "TradeVolume": "5000000", "TradeValue": "500000000", "ClosingPrice": "100.00"}
        )
    return records
