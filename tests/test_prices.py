"""yfinance 輸出拆解與資料狀態判斷（不連網，以 yf.download 的欄位格式模擬）。"""

import numpy as np
import pandas as pd

from screener.prices import OHLCV, _split_download, data_status, yahoo_symbol

IDX = pd.DatetimeIndex(["2026-10-28", "2026-10-29", "2026-10-30"])


def _download(symbols, missing=(), ticker_first=True):
    cols = {}
    for s in symbols:
        for f in OHLCV:
            values = [np.nan] * 3 if s in missing else [1.0, 2.0, 3.0]
            cols[(s, f) if ticker_first else (f, s)] = values
    return pd.DataFrame(cols, index=IDX)


def test_yahoo_symbol():
    assert yahoo_symbol("2330") == "2330.TW"


def test_split_group_by_ticker_and_failed_symbol():
    got = _split_download(_download(["2330.TW", "2317.TW"], missing={"2317.TW"}), ["2330.TW", "2317.TW"])
    assert list(got) == ["2330.TW"]
    assert list(got["2330.TW"].columns) == OHLCV


def test_split_group_by_column():
    got = _split_download(_download(["2330.TW", "2454.TW"], ticker_first=False), ["2330.TW", "2454.TW"])
    assert sorted(got) == ["2330.TW", "2454.TW"]


def test_split_empty():
    assert _split_download(None, ["2330.TW"]) == {}
    assert _split_download(pd.DataFrame(), ["2330.TW"]) == {}


def test_data_status():
    df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=IDX.tz_localize("Asia/Taipei"))
    assert data_status(df, pd.Timestamp("2026-10-30")) is None
    assert data_status(df, pd.Timestamp("2026-10-29")) is None  # 指定較早日期，之後的資料會被截掉
    assert data_status(df, pd.Timestamp("2026-11-02")) == "日期落後"
    assert data_status(df, pd.Timestamp("2026-10-01")) == "無資料"
    assert data_status(None, pd.Timestamp("2026-10-30")) == "無資料"
