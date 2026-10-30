from datetime import date

import pandas as pd
import pytest

from screener.universe import DataNotReady, check_trade_date, parse_stock_day_all, roc_to_date


def rec(code, name="", close="100.00", volume="350000", value="35000000", day="1151030"):
    return {"Date": day, "Code": code, "Name": name, "TradeVolume": volume, "TradeValue": value, "ClosingPrice": close}


def parse(records, cfg):
    uc = cfg["universe"]
    return parse_stock_day_all(records, uc["code_pattern"], uc["exclude_name_patterns"])


def test_roc_to_date():
    assert roc_to_date("1151030") == date(2026, 10, 30)
    assert roc_to_date("0990105") == date(2010, 1, 5)


def test_code_filter_excludes_non_common_stocks(cfg):
    records = [
        rec("2330", "台積電"),
        rec("1101", "台泥"),
        rec("0050", "元大台灣50"),
        rec("1101B", "台泥乙特"),
        rec("00631L", "元大台灣50正2"),
        rec("030001", "某某購01"),  # 6 位數權證
        rec("910322", "康師傅-DR"),  # 6 位數 TDR
        rec("9105", "泰金寶-DR"),  # 4 位數 TDR，以名稱排除
        rec("0001", "示範"),
    ]
    assert parse(records, cfg)["code"].tolist() == ["2330", "1101"]


def test_excludes_no_trade_and_converts_units(cfg):
    df = parse([rec("2330", close=""), rec("2317", close="--"), rec("2454", volume="1,234,567", value="98,765,432")], cfg)
    assert df["code"].tolist() == ["2454"]
    row = df.iloc[0]
    assert row["volume_lots"] == pytest.approx(1234.567)  # 股 → 張
    assert row["value"] == 98765432
    assert row["trade_date"] == date(2026, 10, 30)
    assert row["market"] == "TWSE"


def test_check_trade_date(cfg):
    df = parse([rec("2330"), rec("2317")], cfg)
    assert check_trade_date(df, date(2026, 10, 30)) == date(2026, 10, 30)
    with pytest.raises(DataNotReady):
        check_trade_date(df, date(2026, 10, 31))


def test_check_trade_date_rejects_empty_or_mixed(cfg):
    with pytest.raises(RuntimeError):
        check_trade_date(pd.DataFrame(columns=["trade_date"]), date(2026, 10, 30))
    mixed = parse([rec("2330"), rec("2317", day="1151029")], cfg)
    with pytest.raises(RuntimeError):
        check_trade_date(mixed, date(2026, 10, 30))
