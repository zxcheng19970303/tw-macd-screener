from datetime import date

import numpy as np
import pandas as pd

from screener import demo
from screener.indicators import MONTHLY, OHLCV_AGG, WEEKLY, build_frames, drop_incomplete, resample


def _daily(dates, closes, volumes=None):
    idx = pd.DatetimeIndex(dates)
    c = np.array(closes, dtype=float)
    v = np.array(volumes if volumes is not None else [1000] * len(c), dtype=float)
    return pd.DataFrame({"Open": c - 1, "High": c + 2, "Low": c - 2, "Close": c, "Volume": v}, index=idx)


def test_weekly_close_is_last_trading_day_of_week():
    # 2026-10-05（一）～10-09（五），10-09 休市：該週最後交易日為週四
    df = _daily(["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-16"], [10, 11, 12, 13, 20, 21])
    w = resample(df, WEEKLY)
    assert list(w.index) == [pd.Timestamp("2026-10-09"), pd.Timestamp("2026-10-16")]
    assert w["Close"].tolist() == [13, 21]
    assert w["Open"].tolist() == [9, 19]
    assert w["High"].tolist() == [15, 23]
    assert w["Low"].tolist() == [8, 18]


def test_monthly_volume_is_month_sum():
    df = _daily(["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-30"], [10, 11, 12, 13], [100, 200, 300, 400])
    m = resample(df, MONTHLY)
    assert list(m.index) == [pd.Timestamp("2026-09-30"), pd.Timestamp("2026-10-31")]
    assert m["Volume"].tolist() == [300, 700]
    assert m["Close"].tolist() == [11, 13]


def test_whole_week_closed_produces_no_bar():
    # 農曆年整週休市不應產生空 K 棒
    df = _daily(["2026-02-06", "2026-02-23"], [10, 11])
    assert len(resample(df, WEEKLY)) == 2


def test_matches_pandas_resample():
    _, histories = demo.demo_dataset(date(2026, 10, 30), n=3)
    for df in histories.values():
        for rule in (WEEKLY, MONTHLY):
            expected = df.resample(rule).agg(OHLCV_AGG).dropna(subset=["Close"])
            got = resample(df, rule)
            assert (got.index == expected.index).all()
            assert np.allclose(got.to_numpy(), expected.to_numpy())


def test_drop_incomplete_weekly():
    df = _daily(["2026-10-26", "2026-10-27", "2026-10-28"], [10, 11, 12])
    w = resample(df, WEEKLY)  # 標籤 2026-10-30（五）
    assert len(drop_incomplete(w, pd.Timestamp("2026-10-28"), WEEKLY)) == 0
    assert len(drop_incomplete(w, pd.Timestamp("2026-10-30"), WEEKLY)) == 1


def test_drop_incomplete_monthly_uses_last_weekday():
    # 2026-10-31 是週六，10-30（五）即為該月最後一個平日
    df = _daily(["2026-10-01", "2026-10-29"], [10, 11])
    m = resample(df, MONTHLY)
    assert len(drop_incomplete(m, pd.Timestamp("2026-10-29"), MONTHLY)) == 0
    assert len(drop_incomplete(m, pd.Timestamp("2026-10-30"), MONTHLY)) == 1


def test_build_frames_modes_and_truncation(cfg):
    _, histories = demo.demo_dataset(date(2026, 10, 30), n=1)
    df = next(iter(histories.values()))
    wed = pd.Timestamp("2026-10-28")
    daily, weekly, monthly = build_frames(df, wed, cfg)
    assert daily.index[-1] == wed  # 不會看到未來資料
    assert weekly.index[-1] == pd.Timestamp("2026-10-23")
    assert monthly.index[-1] == pd.Timestamp("2026-09-30")
    assert {"bo_dm", "ro_dif", "ro_macd", "ro_dm", "mi_macd", "mi_dm"} <= set(daily.columns)

    cfg["switches"]["htf_bar_mode"] = "current"
    _, weekly, monthly = build_frames(df, wed, cfg)
    assert weekly.index[-1] == pd.Timestamp("2026-10-30")
    assert monthly.index[-1] == pd.Timestamp("2026-10-31")
    assert weekly["Close"].iloc[-1] == df.loc[wed, "Close"]
