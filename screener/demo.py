"""--demo 模擬資料：不連網，供測試與儀表板預覽。

產生下列資料，讓 demo 走與正式流程相同的解析、過濾與判斷：
- 證交所 MI_INDEX、櫃買中心收盤行情格式的當日行情（字串欄位、千分位）
- yfinance 格式的日線 OHLCV（量為股）與 60 分 K（09:00 那根量為 0，與 Yahoo 實測相同）
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
TPEX_EXCLUDED = [("00679B", "元大美債20年", "26.46"), ("6999", "示範上櫃無成交", "---")]
MI_FIELDS = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"]
# 櫃買中心欄位名稱實際帶有空白與 <br>，照抄以驗證解析
TPEX_FIELDS = ["代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低", "成交股數  ", " 成交金額(元)", " 成交筆數 ", "最後買量<br>(張數)"]


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


def demo_markets(names: dict[str, str]) -> dict[str, str]:
    """約三分之一的示範股設為上櫃。"""
    return {code: "TPEX" if i % 3 == 1 else "TWSE" for i, code in enumerate(names)}


def demo_intraday(histories: dict[str, pd.DataFrame], days: int = 120, seed: int = 60) -> dict[str, pd.DataFrame]:
    """由日線拆出每天 5 根 60 分 K（09:00～13:00，台北時區），開盤與收盤對齊日線。

    少數個股刻意不給 60 分資料，用來驗證第 1 組的資料不足處理。
    """
    rng = np.random.default_rng(seed)
    hours = pd.to_timedelta([9, 10, 11, 12, 13], unit="h")
    out: dict[str, pd.DataFrame] = {}
    for i, (code, daily) in enumerate(histories.items()):
        if i % 47 == 9:
            continue
        d = daily.iloc[-days:]
        n = len(d)
        o, c = d["Open"].to_numpy(), d["Close"].to_numpy()
        hi, lo = d["High"].to_numpy(), d["Low"].to_numpy()
        # 6 個節點：開盤、4 個整點、收盤；中間點在開收連線附近擺動，並限制在當日高低之間
        path = np.linspace(o, c, 6).T + rng.normal(0, 0.25, (n, 6)) * (hi - lo)[:, None]
        path[:, 0], path[:, -1] = o, c
        path = np.clip(path, lo[:, None], hi[:, None])
        bo, bc = path[:, :-1], path[:, 1:]
        bh = np.minimum(np.maximum(bo, bc) + np.abs(rng.normal(0, 0.1, (n, 5))) * (hi - lo)[:, None], hi[:, None])
        bl = np.maximum(np.minimum(bo, bc) - np.abs(rng.normal(0, 0.1, (n, 5))) * (hi - lo)[:, None], lo[:, None])
        w = rng.dirichlet(np.ones(4), n)
        vol = np.concatenate([np.zeros((n, 1)), w * d["Volume"].to_numpy()[:, None]], axis=1).round()
        idx = pd.DatetimeIndex([day + h for day in d.index for h in hours]).tz_localize("Asia/Taipei")
        out[code] = pd.DataFrame(
            {"Open": bo.ravel(), "High": bh.ravel(), "Low": bl.ravel(), "Close": bc.ravel(), "Volume": vol.ravel()}, index=idx
        )
    return out


def quotes(day: date, names: dict[str, str], histories: dict[str, pd.DataFrame], markets: dict[str, str], market: str) -> dict:
    """模擬指定市場的交易所回傳內容（只含每日收盤行情表）。"""
    ts = pd.Timestamp(day)
    rows = []
    for code, df in histories.items():
        if markets.get(code, "TWSE") != market:
            continue
        bars = df[df.index <= ts]
        if bars.empty:
            continue
        b = bars.iloc[-1]
        o, h, low, c = (f"{b[k]:,.2f}" for k in ("Open", "High", "Low", "Close"))
        vol, val = f"{int(b['Volume']):,}", f"{int(b['Volume'] * b['Close']):,}"
        if market == "TWSE":
            rows.append([code, names[code], vol, "1,000", val, o, h, low, c])
        else:
            rows.append([code, names[code], c, "+0.10", o, h, low, vol, val, "1,000", "10"])
    if market == "TWSE":
        rows += [[code, name, "5,000,000", "1,000", "500,000,000", close, close, close, close] for code, name, close in EXCLUDED]
        table = {"title": f"{day:%Y年%m月%d日} 每日收盤行情（示範）", "fields": MI_FIELDS, "data": rows}
        return {"stat": "OK", "date": f"{day:%Y%m%d}", "tables": [table]}
    rows += [[code, name, close, "0.00", close, close, close, "41,103,000", "1,089,904,110", "5,102", "299"] for code, name, close in TPEX_EXCLUDED]
    table = {"title": "上櫃股票每日收盤行情(不含定價)（示範）", "fields": TPEX_FIELDS, "data": rows}
    return {"stat": "ok", "date": f"{day:%Y%m%d}", "tables": [table]}
