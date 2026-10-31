"""歷史價格：yfinance 還原權值日線（規格書第 4 節）。

要換成 FinMind、XQ 等資料源時，只需改寫這個檔案，維持 fetch_history 的輸出格式：
dict[code, DataFrame]，index 為日期，欄位 Open/High/Low/Close/Volume（還原權值，量為股）。
"""

from __future__ import annotations

import logging
import time
import warnings
from collections import Counter
from datetime import date, timedelta

import pandas as pd

from .indicators import clean_daily, clean_intraday

log = logging.getLogger(__name__)

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def yahoo_symbol(code: str, market: str = "TWSE") -> str:
    return f"{code}.TWO" if market == "TPEX" else f"{code}.TW"


def _split_download(data: pd.DataFrame | None, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """把 yf.download 的多檔結果拆成每檔一個 DataFrame。"""
    out: dict[str, pd.DataFrame] = {}
    if data is None or data.empty:
        return out
    if not isinstance(data.columns, pd.MultiIndex):
        if len(symbols) == 1:
            out[symbols[0]] = data
    else:
        lv0 = set(data.columns.get_level_values(0))
        lv1 = set(data.columns.get_level_values(1))
        for s in symbols:
            if s in lv0:
                out[s] = data[s]
            elif s in lv1:
                out[s] = data.xs(s, axis=1, level=1)
    for s in list(out):
        df = out[s][[c for c in OHLCV if c in out[s].columns]].dropna(subset=["Close"])
        if df.empty or len(df.columns) < len(OHLCV):
            del out[s]
        else:
            out[s] = df
    return out


def _download(
    symbol_to_code: dict[str, str], start: date, end: date, interval: str, batch_size: int, retries: int, retry_wait: float
) -> dict[str, pd.DataFrame]:
    """分批下載（auto_adjust=True）；抓不到的代號會重試，最後仍失敗者不在回傳結果中。"""
    import yfinance as yf

    result: dict[str, pd.DataFrame] = {}
    pending = list(symbol_to_code)
    for attempt in range(retries + 1):
        failed: list[str] = []
        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            try:
                # yfinance 對新版 pandas 會發出大量棄用警告，洗掉 Actions 的 log
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    data = yf.download(
                        batch,
                        start=start.isoformat(),
                        end=(end + timedelta(days=1)).isoformat(),  # end 不含當日，需 +1
                        interval=interval,
                        auto_adjust=True,
                        group_by="ticker",
                        threads=True,
                        progress=False,
                    )
            except Exception as e:  # yfinance 對網路錯誤的例外型別不固定
                log.warning("yfinance %s 批次 %d 下載失敗：%s", interval, i // batch_size + 1, e)
                data = None
            got = _split_download(data, batch)
            for s in batch:
                if s in got:
                    result[symbol_to_code[s]] = got[s]
                else:
                    failed.append(s)
        log.info("yfinance %s 第 %d 輪：成功 %d，失敗 %d", interval, attempt + 1, len(result), len(failed))
        if not failed:
            break
        pending = failed
        if attempt < retries:
            time.sleep(retry_wait)
    return result


def fetch_history(
    codes: list[str],
    years: int,
    end: date,
    batch_size: int = 100,
    retries: int = 3,
    retry_wait: float = 10,
    markets: dict[str, str] | None = None,
) -> dict[str, pd.DataFrame]:
    """近 years 年的還原權值日線；markets 為代號對市場（TWSE／TPEX），未提供時視為上市。"""
    start = end - timedelta(days=round(years * 365.25) + 10)
    symbols = {yahoo_symbol(c, (markets or {}).get(c, "TWSE")): c for c in codes}
    return _download(symbols, start, end, "1d", batch_size, retries, retry_wait)


def fetch_intraday(
    codes: list[str],
    days: int,
    end: date,
    batch_size: int = 100,
    retries: int = 1,
    retry_wait: float = 10,
    markets: dict[str, str] | None = None,
) -> dict[str, pd.DataFrame]:
    """近 days 天的 60 分 K（台北時間 09:00～13:00 每天 5 根；Yahoo 最多約 730 天）。"""
    start = end - timedelta(days=days)
    symbols = {yahoo_symbol(c, (markets or {}).get(c, "TWSE")): c for c in codes}
    return _download(symbols, start, end, "60m", batch_size, retries, retry_wait)


def intraday_status(df: pd.DataFrame | None, trade_date: pd.Timestamp) -> str | None:
    """60 分 K 必須有交易日當天的 K 棒；Yahoo 沒有官方資料可補，缺了就只能跳過。"""
    if df is None or df.empty:
        return "60 分無資料"
    days = clean_intraday(df).index.normalize()
    days = days[days <= trade_date]
    if days.empty:
        return "60 分無資料"
    if days.max() < trade_date:
        return "60 分資料落後"
    return None


def official_bar(row) -> dict:
    """證交所當日行情（universe 的一列）轉成日線 K 棒；量換回股。"""
    return {"Open": row.open, "High": row.high, "Low": row.low, "Close": row.close, "Volume": row.volume_lots * 1000}


def patch_today(df: pd.DataFrame | None, bar: dict, trade_date: pd.Timestamp) -> pd.DataFrame:
    """Yahoo 缺當日 K 棒時，以證交所官方數字補上。

    Yahoo 的台股日線常到隔天才有當日開高低收（實測只有量）。已有有效 K 棒時不動，
    以免 --date 重跑過去日期時，未還原的官方價與還原權值的歷史價混用。
    """
    d = clean_daily(df) if df is not None and not df.empty else pd.DataFrame(columns=OHLCV)
    if trade_date in d.index:
        return d
    return pd.concat([d, pd.DataFrame([bar], index=pd.DatetimeIndex([trade_date]))[OHLCV]]).sort_index()


def last_before(df: pd.DataFrame | None, trade_date: pd.Timestamp) -> pd.Timestamp | None:
    """trade_date 之前最後一根有效 K 棒的日期。"""
    if df is None or df.empty:
        return None
    idx = clean_daily(df).index
    idx = idx[idx < trade_date]
    return idx.max() if len(idx) else None


def reference_date(histories: dict[str, pd.DataFrame], trade_date: pd.Timestamp) -> pd.Timestamp | None:
    """多數股票的前一交易日，作為判斷個股資料是否落後的基準（不需交易日曆）。"""
    counts = Counter(d for d in (last_before(df, trade_date) for df in histories.values()) if d is not None)
    return counts.most_common(1)[0][0] if counts else None


def data_status(df: pd.DataFrame | None, trade_date: pd.Timestamp, reference: pd.Timestamp | None) -> str | None:
    """資料源層級的問題：無資料，或前一交易日以前的資料落後於多數股票。正常時回傳 None。"""
    last = last_before(df, trade_date)
    if last is None:
        return "無資料"
    if reference is not None and last < reference:
        return "日期落後"
    return None
