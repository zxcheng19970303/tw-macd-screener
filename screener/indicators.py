"""EMA、MACD 與週月線轉換（規格書第 6 節）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

OHLCV_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
WEEKLY = "W-FRI"
MONTHLY = "ME"
TIMEFRAMES = ("60m", "D", "W", "M")
TF_LINE = {"60m": "60 分線", "D": "日線", "W": "週線", "M": "月線"}
TF_UNIT = {"60m": "根 60 分 K", "D": "日", "W": "週", "M": "個月"}
HTF_RULE = {"W": WEEKLY, "M": MONTHLY}


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


def clean_intraday(df: pd.DataFrame) -> pd.DataFrame:
    """60 分 K：轉成台北時間後去掉時區，排序、去重、刪除無收盤價的列。

    不刪量為 0 的列：Yahoo 每天 09:00 那根 60 分 K 的量固定是 0，但價格正常（2026-10-31 實測）。
    """
    out = df.copy()
    idx = pd.DatetimeIndex(out.index)
    out.index = idx.tz_convert("Asia/Taipei").tz_localize(None) if idx.tz is not None else idx
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out[out["Close"].notna()]


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


def required_sets(cfg: dict) -> dict[str, list[str]]:
    """各週期需要計算的 MACD 參數組：下級週期算波、羅、蜜，本級與上級只算羅；日線一定計算（量能用）。"""
    need: dict[str, set[str]] = {"D": {"ro"}}
    if cfg.get("watchlist"):
        need["D"].add(cfg["watchlist"]["macd_set"])
    for g in cfg["groups"]:
        need.setdefault(g["lower"], set()).update({"bo", "ro", "mi"})
        need.setdefault(g["middle"], set()).add("ro")
        need.setdefault(g["upper"], set()).add("ro")
    return {tf: [k for k in ("bo", "ro", "mi") if k in sets] for tf, sets in need.items()}


def build_frames(
    daily: pd.DataFrame, trade_date: pd.Timestamp, cfg: dict, intraday: pd.DataFrame | None = None
) -> dict[str, pd.DataFrame]:
    """計算各週期含 MACD 的 K 棒：{"60m", "D", "W", "M"} 中各組用得到的週期。

    週線、月線由日線重新取樣；60 分 K 直接用 intraday（沒有提供時不產生 "60m"）。
    價格先截到 trade_date，確保 --date 與 demo 回補不會看到未來資料。
    """
    sw = cfg["switches"]
    d = clean_daily(daily)
    d = d[d.index <= trade_date]
    frames = {}
    for tf, names in required_sets(cfg).items():
        if tf == "D":
            bars = d
        elif tf in HTF_RULE:
            bars = resample(d, HTF_RULE[tf])
            if sw["htf_bar_mode"] == "completed":
                bars = drop_incomplete(bars, trade_date, HTF_RULE[tf])
        elif tf == "60m":
            if intraday is None:
                continue
            bars = clean_intraday(intraday)
            bars = bars[bars.index.normalize() <= trade_date]
        else:
            raise ValueError(f"未支援的週期：{tf}")
        frames[tf] = _with_macd(bars, sw["price_source"], {k: cfg["macd"][k] for k in names})
    if cfg.get("watchlist"):
        n = cfg["watchlist"]["ma_days"]
        frames["D"][f"ma{n}"] = frames["D"]["Close"].rolling(n).mean()  # 簡單移動平均，用收盤價
    return frames
