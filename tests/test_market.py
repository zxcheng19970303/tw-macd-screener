from datetime import date

import pytest
import requests

from screener import market

DAY = date(2026, 11, 18)


def mi_index(day="20261118", close="40,194.91"):
    # 2026-11-19 實測格式：價格指數表在收盤行情表之前，漲跌正負號放在 HTML 裡
    fields = ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]
    return {
        "stat": "OK",
        "date": day,
        "tables": [
            {"title": "價格指數(臺灣證券交易所)", "fields": fields, "data": [["寶島股價指數", "45,000.00", "", "1", "0", ""],
                                                                        ["發行量加權股價指數", close, "<p style ='color:red'>+</p>", "102.80", "0.26", ""]]},
            {"title": "價格指數(跨市場)", "fields": fields, "data": []},
            {"title": "每日收盤行情", "fields": ["證券代號", "證券名稱"], "data": [["2330", "台積電"]]},
        ],
    }


def tpex_month(rows, stat="ok"):
    return {"stat": stat, "date": "20261101", "tables": [{"title": "櫃買指數(月查詢)", "fields": ["日期", "開市", "最高", "最低", "收市", "漲/跌"], "data": rows}]}


def test_taiex_from_mi_index():
    assert market.taiex_from_mi_index(mi_index()) == {"2026-11-18": 40194.91}
    assert market.taiex_from_mi_index(None) == {}
    assert market.taiex_from_mi_index({"stat": "OK", "date": "20261118", "tables": [{"fields": ["證券代號"], "data": []}]}) == {}


def test_parse_tpex_month_handles_western_and_roc_dates():
    rows = [["2026/11/17", "305.00", "309.00", "300.00", "306.61", "-1.20"], ["115/11/18", "306.00", "308.00", "305.00", "307.55", "0.94"],
            ["合計", "", "", "", "", ""]]
    assert market.parse_tpex_month(tpex_month(rows)) == {"2026-11-17": 306.61, "2026-11-18": 307.55}
    assert market.parse_tpex_month(tpex_month(rows, stat="查詢日期錯誤")) == {}


def test_merge_series_overrides_trims_and_sorts():
    old = {"2025-10-01": 1.0, "2026-11-16": 10.0, "2026-11-17": 11.0}
    new = {"2026-11-17": 11.5, "2026-11-18": 12.0, "2026-11-19": 99.0}
    assert market.merge_series(old, new, DAY, 370) == {"2026-11-16": 10.0, "2026-11-17": 11.5, "2026-11-18": 12.0}


def test_summarize_change_from_previous_point():
    s = market.summarize("TAIEX", {"2026-11-18": 40194.91, "2026-11-17": 40092.11})
    assert s["date"] == "2026-11-18" and s["close"] == 40194.91
    assert s["change"] == 102.8 and s["change_pct"] == 0.26
    assert s["dates"] == ["2026-11-17", "2026-11-18"] and s["closes"] == [40092.11, 40194.91]
    empty = market.summarize("TPEX", {})
    assert empty["close"] is None and empty["dates"] == []


def test_tpex_history_backfills_only_missing_months(monkeypatch, cfg):
    calls = []
    monkeypatch.setattr(market.time, "sleep", lambda s: None)
    monkeypatch.setattr(market, "fetch_tpex_month", lambda m, t: calls.append(m) or {f"{m:%Y-%m}-03": 300.0})
    errors = []
    first = market.tpex_history({}, DAY, cfg, errors)
    assert len(calls) == cfg["market"]["history_days"] // 28 + 1 and calls[0] == date(2026, 11, 1)
    assert "2025-10-03" in first and not errors
    calls.clear()
    market.tpex_history(first, DAY, cfg, errors)
    assert calls == [date(2026, 11, 1), date(2026, 10, 1)]  # 已有的月份只重抓當月與上個月


def test_tpex_history_old_month_failure_is_soft(monkeypatch, cfg):
    monkeypatch.setattr(market.time, "sleep", lambda s: None)

    def fetch(m, t):
        if m < date(2026, 9, 1):
            raise requests.ConnectionError("timeout")
        return {f"{m:%Y-%m}-03": 300.0}

    monkeypatch.setattr(market, "fetch_tpex_month", fetch)
    errors = []
    assert set(market.tpex_history({}, DAY, cfg, errors)) == {"2026-11-03", "2026-10-03", "2026-09-03"}
    assert errors and "回補失敗" in errors[0]
    monkeypatch.setattr(market, "fetch_tpex_month", lambda m, t: (_ for _ in ()).throw(requests.ConnectionError("down")))
    with pytest.raises(requests.ConnectionError):
        market.tpex_history({}, DAY, cfg, [])


def test_build_market_official_close_wins_and_failures_keep_old(monkeypatch, cfg):
    monkeypatch.setattr(market.time, "sleep", lambda s: None)
    monkeypatch.setattr(market, "yahoo_history", lambda s, e, d: {"2026-11-17": 40092.11, "2026-11-18": 40100.0})
    monkeypatch.setattr(market, "fetch_tpex_month", lambda m, t: {"2026-11-17": 306.61, "2026-11-18": 307.55} if m.month == 11 else {})
    payload, errors = market.build_market(DAY, cfg, {}, mi_index(), False, "2026-11-18T19:20:00+08:00")
    taiex, tpex = payload["indices"]
    assert (taiex["id"], taiex["close"], taiex["change"]) == ("TAIEX", 40194.91, 102.8)  # 當天以官方收盤為準
    assert (tpex["id"], tpex["close"], tpex["change_pct"]) == ("TPEX", 307.55, 0.31)
    assert not errors

    # Yahoo 與櫃買中心都失敗：加權仍有官方當日值，櫃買沿用舊資料並回報落後
    old = {i["id"]: dict(zip(i["dates"], i["closes"])) for i in payload["indices"]}
    monkeypatch.setattr(market, "yahoo_history", lambda s, e, d: (_ for _ in ()).throw(ValueError("rate limited")))
    monkeypatch.setattr(market, "fetch_tpex_month", lambda m, t: (_ for _ in ()).throw(requests.ConnectionError("down")))
    nxt = date(2026, 11, 19)
    payload, errors = market.build_market(nxt, cfg, old, mi_index("20261119", "40,300.00"), False, "")
    taiex, tpex = payload["indices"]
    assert taiex["date"] == "2026-11-19" and taiex["change"] == 105.09
    assert tpex["date"] == "2026-11-18" and tpex["close"] == 307.55
    assert any("Yahoo" in e for e in errors) and any("櫃買指數資料只到 2026-11-18" in e for e in errors)
    assert "大盤：加權指數 40,300.00（+0.26%）" in market.summary_line(payload, errors)


def test_load_market_isolates_demo_and_bad_files(tmp_path, cfg):
    market.write_market(tmp_path, market.demo_market(DAY, cfg, ""))
    path = tmp_path / "market.json"
    assert set(market.load_market(path, demo=True)) == {"TAIEX", "TPEX"}
    assert market.load_market(path, demo=False) == {}
    path.write_text("{broken", encoding="utf-8")
    assert market.load_market(path, demo=True) == {}
    assert market.load_market(tmp_path / "missing.json", demo=False) == {}


def test_demo_market_shape(cfg):
    m = market.demo_market(DAY, cfg, "")
    assert m["demo"] is True and [i["id"] for i in m["indices"]] == ["TAIEX", "TPEX"]
    for i in m["indices"]:
        assert i["date"] == "2026-11-18" and len(i["dates"]) == len(i["closes"]) > 200
