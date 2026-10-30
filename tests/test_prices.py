"""yfinance 輸出拆解、當日 K 棒補值與資料狀態判斷（不連網）。"""

from types import SimpleNamespace

import numpy as np
import pandas as pd

from screener.prices import (
    OHLCV,
    _split_download,
    data_status,
    last_before,
    official_bar,
    patch_today,
    reference_date,
    yahoo_symbol,
)

IDX = pd.DatetimeIndex(["2026-10-28", "2026-10-29", "2026-10-30"])
TODAY = pd.Timestamp("2026-10-30")


def _download(symbols, missing=(), ticker_first=True):
    cols = {}
    for s in symbols:
        for f in OHLCV:
            values = [np.nan] * 3 if s in missing else [1.0, 2.0, 3.0]
            cols[(s, f) if ticker_first else (f, s)] = values
    return pd.DataFrame(cols, index=IDX)


def _hist(dates, close=100.0, volume=1000.0):
    n = len(dates)
    return pd.DataFrame({k: [close] * n for k in ("Open", "High", "Low", "Close")} | {"Volume": [volume] * n}, index=pd.DatetimeIndex(dates))


def test_yahoo_symbol():
    assert yahoo_symbol("2330") == "2330.TW"


def test_split_group_by_ticker_and_failed_symbol():
    got = _split_download(_download(["2330.TW", "2317.TW"], missing={"2317.TW"}), ["2330.TW", "2317.TW"])
    assert list(got) == ["2330.TW"]
    assert list(got["2330.TW"].columns) == OHLCV


def test_split_group_by_column():
    got = _split_download(_download(["2330.TW", "2454.TW"], ticker_first=False), ["2330.TW", "2454.TW"])
    assert sorted(got) == ["2330.TW", "2454.TW"]


def test_split_drops_yahoo_row_with_volume_only():
    # 實測：Yahoo 當晚的當日 K 棒只有量，開高低收都是 NaN
    data = _download(["2330.TW"])
    for f in ("Open", "High", "Low", "Close"):
        data.loc[TODAY, ("2330.TW", f)] = np.nan
    got = _split_download(data, ["2330.TW"])
    assert got["2330.TW"].index[-1] == pd.Timestamp("2026-10-29")


def test_split_empty():
    assert _split_download(None, ["2330.TW"]) == {}
    assert _split_download(pd.DataFrame(), ["2330.TW"]) == {}


def test_official_bar():
    row = SimpleNamespace(open=10.0, high=11.0, low=9.0, close=10.5, volume_lots=350.0)
    assert official_bar(row) == {"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.5, "Volume": 350000.0}


def test_patch_today_appends_when_missing():
    hist = _hist(["2026-10-28", "2026-10-29"]).tz_localize("Asia/Taipei")
    bar = {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 5000.0}
    out = patch_today(hist, bar, TODAY)
    assert out.index[-1] == TODAY and out.loc[TODAY, "Close"] == 1.5
    assert len(out) == 3


def test_patch_today_keeps_existing_valid_bar():
    hist = _hist(["2026-10-29", "2026-10-30"], close=100.0)
    out = patch_today(hist, {"Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1}, TODAY)
    assert out.loc[TODAY, "Close"] == 100.0  # 已還原權值的 Yahoo 價優先


def test_patch_today_without_history():
    out = patch_today(None, {"Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1}, TODAY)
    assert list(out.index) == [TODAY]


def test_zero_volume_placeholder_is_ignored():
    hist = _hist(["2026-10-22", "2026-10-23", "2026-10-27"])
    hist.loc["2026-10-23", "Volume"] = 0  # Yahoo 休市日假 K 棒
    assert last_before(hist, pd.Timestamp("2026-10-27")) == pd.Timestamp("2026-10-22")


def test_reference_date_and_status():
    histories = {
        "a": _hist(["2026-10-28", "2026-10-29"]),
        "b": _hist(["2026-10-28", "2026-10-29", "2026-10-30"]),  # 已有當日，不影響前一交易日基準
        "c": _hist(["2026-10-27"]),  # 落後
        "d": None,
    }
    ref = reference_date(histories, TODAY)
    assert ref == pd.Timestamp("2026-10-29")
    assert data_status(histories["a"], TODAY, ref) is None
    assert data_status(histories["b"], TODAY, ref) is None
    assert data_status(histories["c"], TODAY, ref) == "日期落後"
    assert data_status(histories["d"], TODAY, ref) == "無資料"
    assert data_status(_hist(["2026-11-02"]), TODAY, ref) == "無資料"  # 只有未來資料
