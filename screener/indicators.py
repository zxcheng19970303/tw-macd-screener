"""EMA、MACD 與週月線轉換（規格書第 6 節）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

OHLCV_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
WEEKLY = "W-FRI"
MONTHLY = "ME"


def ema(series: pd.Series, n: int) -> pd.Series:
    """標準遞迴 EMA：alpha = 2/(n+1)，以第一筆值為起始值。"""
    return series.ewm(span=n, adjust=False).mean()


def macd(series: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    """回傳 dif、macd（訊號線）、dm（柱狀體 = dif - macd）。"""
    dif = ema(series, fast) - ema(series, slow)
    sig = ema(dif, signal)
    return pd.DataFrame({"dif": dif, "macd": sig, "dm": dif - sig}, index=series.index)


def price_series(df: pd.DataFrame, source: str) -> pd.Series:
    if source == "close":
        return df["Close"]
    if source == "hlcc4":
        return (df["High"] + df["Low"] + 2 * df["Close"]) / 4
    raise ValueError(f"未知的 price_source：{source}")


def clean_daily(df: pd.DataFrame) -> pd.DataFrame:
    """依日期排序，去除重複日期、無收盤價與成交量為 0 的列。

    Yahoo 會在部分休市日放一根量為 0、開高低收都等於前一日收盤的假 K 棒，
    留著會讓 EMA 多算一根；個股無成交日在證交所也沒有收盤價，一併排除。
    """
    out = df.copy()
    out.index = pd.DatetimeIndex(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out[out["Close"].notna() & (out["Volume"].fillna(0) > 0)]


def period_label(index: pd.DatetimeIndex, rule: str) -> np.ndarray:
    """每個日期所屬週期的標籤：週線為該週五，月線為該月最後一天（同 pandas W-FRI、ME）。"""
    days = index.values.astype("datetime64[D]")
    if rule == WEEKLY:
        return days + ((4 - index.dayofweek.values) % 7).astype("timedelta64[D]")
    if rule == MONTHLY:
        next_month = days.astype("datetime64[M]") + np.timedelta64(1, "M")
        return next_month.astype("datetime64[D]") - np.timedelta64(1, "D")
    raise ValueError(f"未支援的週期：{rule}")


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """日線（已排序、無重複）轉週線或月線；整段休市的週期不產生 K 棒。

    結果與 df.resample(rule).agg(...).dropna() 相同（見 test_resample），
    改用 numpy 分段彙總是因為 demo 回補 60 日需要重算上萬次，pandas resample 太慢。
    """
    if df.empty:
        return df[list(OHLCV_AGG)].iloc[:0]
    label = period_label(pd.DatetimeIndex(df.index), rule)
    starts = np.flatnonzero(np.r_[True, label[1:] != label[:-1]])
    ends = np.r_[starts[1:], len(label)] - 1
    o, h, low, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    v = np.nan_to_num(df["Volume"].to_numpy(dtype=float))
    return pd.DataFrame(
        {
            "Open": o[starts],
            "High": np.fmax.reduceat(h, starts),
            "Low": np.fmin.reduceat(low, starts),
            "Close": c[ends],
            "Volume": np.add.reduceat(v, starts),
        },
        index=pd.DatetimeIndex(label[starts]),
    )


def bar_end(label: pd.Timestamp, rule: str) -> pd.Timestamp:
    """K 棒的預期最後交易日：週線為該週五，月線為該月最後一個平日。"""
    if rule == MONTHLY:
        return pd.offsets.BMonthEnd().rollback(label)
    return label


def drop_incomplete(htf: pd.DataFrame, trade_date: pd.Timestamp, rule: str) -> pd.DataFrame:
    """交易日早於最後一根 K 棒的預期結束日時，視為未完成並捨棄。"""
    if htf.empty:
        return htf
    if trade_date < bar_end(htf.index[-1], rule):
        return htf.iloc[:-1]
    return htf


def volume_averages(daily: pd.DataFrame, today_lots: float, today_value: float, n: int = 5) -> dict:
    """n 日均量（張）與均成交金額（元）。

    當日用證交所官方數值；前 n-1 日取自日線，成交金額以 收盤 × 量 估算。
    """
    prev = daily.iloc[-n:-1]
    lots = [*(prev["Volume"] / 1000), today_lots]
    values = [*(prev["Volume"] * prev["Close"]), today_value]
    return {"avg5_lots": sum(lots) / len(lots), "avg5_value": sum(values) / len(values)}


def _with_macd(df: pd.DataFrame, source: str, sets: dict[str, dict]) -> pd.DataFrame:
    price = price_series(df, source)
    parts = [df]
    for name, p in sets.items():
        parts.append(macd(price, p["fast"], p["slow"], p["signal"]).add_prefix(f"{name}_"))
    return pd.concat(parts, axis=1)


def build_frames(
    daily: pd.DataFrame, trade_date: pd.Timestamp, cfg: dict
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """計算三週期指標。

    日線含波、羅、蜜三組 MACD；週線與月線只算羅。
    價格先截到 trade_date，確保 --date 與 demo 回補不會看到未來資料。
    """
    sw = cfg["switches"]
    sets = cfg["macd"]
    d = clean_daily(daily)
    d = d[d.index <= trade_date]

    daily_ind = _with_macd(d, sw["price_source"], sets)
    htf = []
    for rule in (WEEKLY, MONTHLY):
        bars = resample(d, rule)
        if sw["htf_bar_mode"] == "completed":
            bars = drop_incomplete(bars, trade_date, rule)
        htf.append(_with_macd(bars, sw["price_source"], {"ro": sets["ro"]}))
    return daily_ind, htf[0], htf[1]
