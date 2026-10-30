"""歷史價格：yfinance 還原權值日線（規格書第 4 節）。

要換成 FinMind、XQ 等資料源時，只需改寫這個檔案，維持 fetch_history 的輸出格式：
dict[code, DataFrame]，index 為日期，欄位 Open/High/Low/Close/Volume（還原權值，量為股）。
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas as pd

log = logging.getLogger(__name__)

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def yahoo_symbol(code: str) -> str:
    return f"{code}.TW"  # 上市；上櫃為 .TWO（v2）


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


def fetch_history(
    codes: list[str],
    years: int,
    end: date,
    batch_size: int = 100,
    retries: int = 3,
    retry_wait: float = 10,
) -> dict[str, pd.DataFrame]:
    """分批下載近 years 年日線（auto_adjust=True）；抓不到的代號會重試，最後仍失敗者不在回傳結果中。"""
    import yfinance as yf

    start = end - timedelta(days=round(years * 365.25) + 10)
    symbol_to_code = {yahoo_symbol(c): c for c in codes}
    result: dict[str, pd.DataFrame] = {}
    pending = list(symbol_to_code)
    for attempt in range(retries + 1):
        failed: list[str] = []
        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            try:
                data = yf.download(
                    batch,
                    start=start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),  # end 不含當日，需 +1
                    interval="1d",
                    auto_adjust=True,
                    group_by="ticker",
                    threads=True,
                    progress=False,
                )
            except Exception as e:  # yfinance 對網路錯誤的例外型別不固定
                log.warning("yfinance 批次 %d 下載失敗：%s", i // batch_size + 1, e)
                data = None
            got = _split_download(data, batch)
            for s in batch:
                if s in got:
                    result[symbol_to_code[s]] = got[s]
                else:
                    failed.append(s)
        log.info("yfinance 第 %d 輪：成功 %d，失敗 %d", attempt + 1, len(result), len(failed))
        if not failed:
            break
        pending = failed
        if attempt < retries:
            time.sleep(retry_wait)
    return result


def data_status(df: pd.DataFrame | None, trade_date: pd.Timestamp) -> str | None:
    """資料源層級的問題：無資料或最新日期落後。正常時回傳 None。"""
    if df is None or df.empty:
        return "無資料"
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    idx = idx.normalize()
    upto = idx[idx <= trade_date]
    if upto.empty:
        return "無資料"
    if upto.max() < trade_date:
        return "日期落後"
    return None
