"""--demo 模擬資料：不連網，供測試與儀表板預覽。

產生兩種資料，讓 demo 走與正式流程相同的解析、過濾與判斷：
- 證交所 MI_INDEX 格式的當日行情（字串欄位、千分位）
- yfinance 格式的日線 OHLCV（量為股）
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

# 應被股票池過濾掉的代號：ETF、槓桿 ETF、特別股、4 位數存託憑證、當日無成交
EXCLUDED = [
    ("0050", "元大台灣50", "100.00"),
    ("00631L", "元大台灣50正2", "100.00"),
    ("1101B", "台泥乙特", "100.00"),
    ("9105", "泰金寶-DR", "100.00"),
    ("9999", "示範無成交", "--"),
]
MI_FIELDS = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"]


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
            df = df.iloc[:-3]  # Yahoo 資料落後多日
        histories[code] = df
    return names, histories


def mi_index(day: date, names: dict[str, str], histories: dict[str, pd.DataFrame]) -> dict:
    """模擬證交所 MI_INDEX 回傳內容（只含每日收盤行情表）。"""
    ts = pd.Timestamp(day)
    rows = []
    for code, df in histories.items():
        bars = df[df.index <= ts]
        if bars.empty:
            continue
        b = bars.iloc[-1]
        price = [f"{b[k]:,.2f}" for k in ("Open", "High", "Low", "Close")]
        rows.append([code, names[code], f"{int(b['Volume']):,}", "1,000", f"{int(b['Volume'] * b['Close']):,}", *price])
    for code, name, close in EXCLUDED:
        rows.append([code, name, "5,000,000", "1,000", "500,000,000", close, close, close, close])
    table = {"title": f"{day:%Y年%m月%d日} 每日收盤行情（示範）", "fields": MI_FIELDS, "data": rows}
    return {"stat": "OK", "date": f"{day:%Y%m%d}", "tables": [table]}
