from datetime import date

import pandas as pd
import pytest

from screener import universe
from screener.universe import DataNotReady, check_trade_date, check_universe_size, combine, parse_quotes

TWSE_FIELDS = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)"]
# 櫃買中心實際欄位名稱帶有空白與 <br>（2026-10-31 實測）
TPEX_FIELDS = ["代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低", "成交股數  ", " 成交金額(元)", " 成交筆數 ", "最後買量<br>(張數)"]


def twse_row(code, name="", close="100.00", volume="350,000", value="35,000,000", o="99.00", h="101.00", low="98.00"):
    return [code, name, volume, "100", value, o, h, low, close, "<p style= color:red>+</p>"]


def tpex_row(code, name="", close="100.00", volume="350,000", value="35,000,000", o="99.00", h="101.00", low="98.00"):
    return [code, name, close, "+1.00", o, h, low, volume, value, "100", "5"]


def twse(rows, day="20261030", stat="OK"):
    # 真實回應前面還有指數、大盤統計等表格，收盤行情表以「證券代號」欄位辨識
    index_table = {"title": "價格指數", "fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "30,000"]]}
    return {"stat": stat, "date": day, "tables": [index_table, {"title": "每日收盤行情", "fields": TWSE_FIELDS, "data": rows}, {}]}


def tpex(rows, day="20261030"):
    return {"stat": "ok", "date": day, "tables": [{"title": "上櫃股票每日收盤行情(不含定價)", "fields": TPEX_FIELDS, "data": rows}]}


def parse(p, market, cfg):
    uc = cfg["universe"]
    return parse_quotes(p, market, uc["code_pattern"], uc["exclude_name_patterns"])


def test_twse_code_filter_excludes_non_common_stocks(cfg):
    rows = [
        twse_row("2330", "台積電"),
        twse_row("1101", "台泥"),
        twse_row("0050", "元大台灣50"),
        twse_row("1101B", "台泥乙特"),
        twse_row("00631L", "元大台灣50正2"),
        twse_row("00400A", "主動國泰動能高息"),
        twse_row("030001", "某某購01"),  # 6 位數權證
        twse_row("910322", "康師傅-DR"),  # 6 位數 TDR
        twse_row("9105", "泰金寶-DR"),  # 4 位數 TDR，以名稱排除
        twse_row("0001", "示範"),
    ]
    assert parse(twse(rows), "TWSE", cfg)["code"].tolist() == ["2330", "1101"]


def test_twse_parses_numbers_dates_and_no_trade(cfg):
    rows = [
        twse_row("2330", close="--"),  # 當日無成交
        twse_row("2454", "聯發科", close="4,045.00", volume="6,123,862", value="24,876,543,210", o="4,205.00", h="4,225.00", low="4,000.00"),
    ]
    df = parse(twse(rows), "TWSE", cfg)
    assert df["code"].tolist() == ["2454"]
    r = df.iloc[0]
    assert (r["open"], r["high"], r["low"], r["close"]) == (4205, 4225, 4000, 4045)
    assert r["volume_lots"] == pytest.approx(6123.862)  # 股 → 張
    assert r["value"] == 24876543210
    assert r["trade_date"] == date(2026, 10, 30)
    assert r["market"] == "TWSE"


def test_tpex_parses_padded_field_names(cfg):
    rows = [
        tpex_row("6488", "環球晶", close="756.00", volume="8,913,000", value="6,737,108,000", o="775.00", h="779.00", low="741.00"),
        tpex_row("00679B", "元大美債20年"),  # ETF 以代號排除
        tpex_row("8069", "元太", close="---"),  # 無成交
    ]
    df = parse(tpex(rows), "TPEX", cfg)
    assert df["code"].tolist() == ["6488"]
    r = df.iloc[0]
    assert (r["open"], r["high"], r["low"], r["close"]) == (775, 779, 741, 756)
    assert r["volume_lots"] == pytest.approx(8913)
    assert r["value"] == 6737108000
    assert r["market"] == "TPEX"


def test_not_ready_cases(cfg):
    with pytest.raises(DataNotReady):  # 證交所假日
        parse({"stat": "很抱歉，沒有符合條件的資料!"}, "TWSE", cfg)
    with pytest.raises(DataNotReady):  # 櫃買中心週末：stat 仍是 ok，但表格沒有資料
        parse({"stat": "ok", "date": "20261031", "tables": [{"title": "", "fields": [], "data": []}]}, "TPEX", cfg)
    with pytest.raises(DataNotReady):
        parse(tpex([], day="20261031"), "TPEX", cfg)


def test_format_change_is_an_error(cfg):
    p = twse([twse_row("2330")])
    p["tables"] = p["tables"][:1]  # 找不到收盤行情表
    with pytest.raises(RuntimeError):
        parse(p, "TWSE", cfg)
    p = twse([twse_row("2330")])
    p["tables"][1]["fields"] = [f for f in TWSE_FIELDS if f != "收盤價"]
    with pytest.raises(RuntimeError):
        parse(p, "TWSE", cfg)


def test_combine_and_check_trade_date(cfg):
    df = combine([parse(twse([twse_row("2330")]), "TWSE", cfg), parse(tpex([tpex_row("6488")]), "TPEX", cfg)])
    assert df["market"].tolist() == ["TWSE", "TPEX"]
    assert check_trade_date(df, date(2026, 10, 30)) == date(2026, 10, 30)
    with pytest.raises(DataNotReady):
        check_trade_date(df, date(2026, 10, 31))
    mixed = combine([parse(twse([twse_row("2330")]), "TWSE", cfg), parse(tpex([tpex_row("6488")], day="20261029"), "TPEX", cfg)])
    with pytest.raises(RuntimeError):
        check_trade_date(mixed, date(2026, 10, 30))
    with pytest.raises(RuntimeError):
        check_trade_date(pd.DataFrame(columns=["trade_date"]), date(2026, 10, 30))


def test_previous_trading_day_skips_holidays(cfg, monkeypatch):
    open_days = {"20261028"}  # 10/29、10/30 假設休市
    calls = []

    def fake_fetch(market, day, *a):
        calls.append(day.strftime("%Y%m%d"))
        return twse([twse_row("2330")], day=day.strftime("%Y%m%d")) if day.strftime("%Y%m%d") in open_days else {"stat": "很抱歉，沒有符合條件的資料!"}

    monkeypatch.setattr(universe, "fetch_quotes", fake_fetch)
    monkeypatch.setattr(universe.time, "sleep", lambda s: None)
    assert universe.previous_trading_day(date(2026, 11, 2), cfg) == date(2026, 10, 28)
    assert calls == ["20261030", "20261029", "20261028"]  # 週末不查


def test_check_universe_size(cfg):
    df = combine([parse(twse([twse_row(str(1101 + i)) for i in range(95)]), "TWSE", cfg), parse(tpex([tpex_row("6488")]), "TPEX", cfg)])
    check_universe_size(df, {"TWSE": 100, "TPEX": 1}, 0.9)  # 95／100 ≥ 90%：通過
    check_universe_size(df, {}, 0.9)  # 沒有上次紀錄：不檢查
    with pytest.raises(DataNotReady):
        check_universe_size(df, {"TWSE": 120, "TPEX": 1}, 0.9)
