from datetime import date

import pandas as pd
import pytest

from screener.universe import DataNotReady, check_trade_date, parse_mi_index

FIELDS = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)"]


def row(code, name="", close="100.00", volume="350,000", value="35,000,000", o="99.00", h="101.00", low="98.00"):
    return [code, name, volume, "100", value, o, h, low, close, "<p style= color:red>+</p>"]


def payload(rows, day="20261030", stat="OK"):
    # 真實回應前面還有指數、大盤統計等表格，收盤行情表以「證券代號」欄位辨識
    index_table = {"title": "價格指數", "fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "30,000"]]}
    quote = {"title": "每日收盤行情", "fields": FIELDS, "data": rows}
    return {"stat": stat, "date": day, "tables": [index_table, quote, {}]}


def parse(p, cfg):
    uc = cfg["universe"]
    return parse_mi_index(p, uc["code_pattern"], uc["exclude_name_patterns"])


def test_code_filter_excludes_non_common_stocks(cfg):
    rows = [
        row("2330", "台積電"),
        row("1101", "台泥"),
        row("0050", "元大台灣50"),
        row("1101B", "台泥乙特"),
        row("00631L", "元大台灣50正2"),
        row("00400A", "主動國泰動能高息"),
        row("030001", "某某購01"),  # 6 位數權證
        row("910322", "康師傅-DR"),  # 6 位數 TDR
        row("9105", "泰金寶-DR"),  # 4 位數 TDR，以名稱排除
        row("0001", "示範"),
    ]
    assert parse(payload(rows), cfg)["code"].tolist() == ["2330", "1101"]


def test_parses_numbers_dates_and_no_trade(cfg):
    rows = [
        row("2330", close="--"),  # 當日無成交
        row("2454", "聯發科", close="4,045.00", volume="6,123,862", value="24,876,543,210", o="4,205.00", h="4,225.00", low="4,000.00"),
    ]
    df = parse(payload(rows), cfg)
    assert df["code"].tolist() == ["2454"]
    r = df.iloc[0]
    assert (r["open"], r["high"], r["low"], r["close"]) == (4205, 4225, 4000, 4045)
    assert r["volume_lots"] == pytest.approx(6123.862)  # 股 → 張
    assert r["value"] == 24876543210
    assert r["trade_date"] == date(2026, 10, 30)
    assert r["market"] == "TWSE"


def test_stat_not_ok_means_not_ready(cfg):
    with pytest.raises(DataNotReady):
        parse({"stat": "很抱歉，沒有符合條件的資料!"}, cfg)


def test_format_change_is_an_error(cfg):
    p = payload([row("2330")])
    p["tables"] = p["tables"][:1]  # 找不到收盤行情表
    with pytest.raises(RuntimeError):
        parse(p, cfg)
    p = payload([row("2330")])
    p["tables"][1]["fields"] = [f for f in FIELDS if f != "收盤價"]
    with pytest.raises(RuntimeError):
        parse(p, cfg)


def test_check_trade_date(cfg):
    df = parse(payload([row("2330"), row("2317")]), cfg)
    assert check_trade_date(df, date(2026, 10, 30)) == date(2026, 10, 30)
    with pytest.raises(DataNotReady):
        check_trade_date(df, date(2026, 10, 31))
    with pytest.raises(RuntimeError):
        check_trade_date(pd.DataFrame(columns=["trade_date"]), date(2026, 10, 30))
