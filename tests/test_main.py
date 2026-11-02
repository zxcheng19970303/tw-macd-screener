"""main 的流程、結束碼與輸出格式（不連網）。"""

import json
from datetime import date

import pandas as pd
import pytest

from screener import demo, main, universe

DAY = date(2026, 10, 30)


@pytest.fixture
def demo_data():
    names, histories = demo.demo_dataset(DAY, n=20)
    return names, histories, demo.demo_markets(names), demo.demo_intraday(histories)


def _universe(cfg, names, histories, markets, day=DAY):
    uc = cfg["universe"]
    return universe.combine(
        [universe.parse_quotes(demo.quotes(day, names, histories, markets, m), m, uc["code_pattern"], uc["exclude_name_patterns"]) for m in cfg["markets"]]
    )


def test_demo_outputs(tmp_path, cfg):
    assert main.run_demo(cfg, tmp_path, DAY, n_stocks=20, days=3) == main.EXIT_OK
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    history = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert latest["version"] == 2 and latest["trade_date"] == "2026-10-30"
    assert latest["demo"] is True and history["demo"] is True
    assert latest["universe"]["total"] == 20  # 兩個市場的 ETF、特別股、TDR、無成交股已被過濾
    assert set(latest["universe"]["markets"]) == {"TWSE", "TPEX"}
    groups = {g["id"]: g for g in latest["groups"]}
    assert list(groups) == ["g1", "g2", "watch"]
    assert groups["g1"]["label"] == "60 分／日／週" and groups["g2"]["label"] == "日／週／月"
    assert "週線" in groups["g1"]["conditions_meta"]["M1"]["label"] and "60 分線" in groups["g1"]["conditions_meta"]["D2"]["label"]
    assert "月線" in groups["g2"]["conditions_meta"]["M1"]["label"]
    assert list(groups["watch"]["conditions_meta"]) == ["A1", "A2", "A3"]
    assert all(h["volume_lots"] > 200 for h in groups["watch"]["hits"])
    assert [d["trade_date"] for d in history["days"]] == ["2026-10-30", "2026-10-29", "2026-10-28"]
    assert set(history["days"][0]["groups"]) == {"g1", "g2", "watch"}
    for g in latest["groups"]:
        for h in g["hits"]:
            assert all(h["conditions"].values()) and h["is_new"] == (h["streak"] == 1)
        for n in g["near_miss"]:
            assert len(n["failed"]) == 1 and not n["conditions"][n["failed"][0]]
    assert groups["watch"]["near_miss"] == []


def test_screen_counts_insufficient_per_group(cfg, demo_data):
    names, histories, markets, intraday = demo_data
    univ = _universe(cfg, names, histories, markets)
    r = main.screen(univ, histories, DAY, cfg, intraday)
    # demo_dataset：第 5 檔上市 3 年、第 7 檔 Yahoo 落後；demo_intraday：第 9 檔沒有 60 分資料
    assert dict(r["g2"]["insufficient"]) == {"未滿 60 個月": 1, "日期落後": 1}
    assert dict(r["g1"]["insufficient"]) == {"日期落後": 1, "60 分無資料": 1}  # 上市 3 年已超過 60 週
    assert dict(r["watch"]["insufficient"]) == {"日期落後": 1}
    assert (r["g1"]["evaluated"], r["g2"]["evaluated"], r["watch"]["evaluated"]) == (18, 18, 19)
    assert r["g1"]["intraday_failed"] == 1 and r["g2"]["intraday_failed"] == 0
    assert r["g2"]["patched"] == 0  # demo 的 Yahoo 日線已含當日


def test_screen_patches_today_when_yahoo_lags(cfg, demo_data):
    # 實測情境：Yahoo 當晚還沒有當日開高低收，只能用交易所官方行情補上
    names, histories, markets, intraday = demo_data
    univ = _universe(cfg, names, histories, markets)
    lagging = {c: df[df.index < pd.Timestamp(DAY)] for c, df in histories.items()}
    full = main.screen(univ, histories, DAY, cfg, intraday)
    patched = main.screen(univ, lagging, DAY, cfg, intraday)
    assert patched["g2"]["patched"] == full["g2"]["evaluated"] + full["g2"]["insufficient"]["未滿 60 個月"]
    # 官方行情只到小數兩位，補上的 K 棒與原本只差四捨五入：名單相同、數值誤差 < 1%
    for gid in ("g1", "g2"):
        assert patched[gid]["evaluated"] == full[gid]["evaluated"]
        codes = lambda r: sorted(h["code"] for h in r[gid]["hits"] + r[gid]["near_miss"])
        dm = lambda r: [h["values"]["d_ro_dm"] for h in sorted(r[gid]["hits"] + r[gid]["near_miss"], key=lambda h: h["code"])]
        assert codes(patched) == codes(full)
        assert dm(patched) == pytest.approx(dm(full), rel=1e-2)


def test_intraday_guard_disables_only_60m_group(cfg, demo_data):
    names, histories, markets, _ = demo_data
    univ = _universe(cfg, names, histories, markets)
    r = main.screen(univ, histories, DAY, cfg, {})  # 60 分 K 全部抓不到
    main.guard_intraday(r, cfg)
    assert r["g1"]["available"] is False and r["g1"]["hits"] == []
    assert "60 分 K 抓取失敗" in r["g1"]["unavailable_reason"]
    assert r["g2"]["available"] is True and r["watch"]["available"] is True


def _patch_live(monkeypatch, cfg, names, histories, markets, intraday, day):
    monkeypatch.setattr(main.universe, "load_universe", lambda m, c, d: _universe(cfg, names, histories, markets, day))
    monkeypatch.setattr(main.prices, "fetch_history", lambda codes, *a, **k: {c: histories[c] for c in codes if c in histories})
    monkeypatch.setattr(main.prices, "fetch_intraday", lambda codes, *a, **k: {c: intraday[c] for c in codes if c in intraday})
    monkeypatch.setattr(main.universe, "previous_trading_day", lambda d, c: (pd.Timestamp(d) - pd.offsets.BDay(1)).date())


def _read(out, name="latest.json"):
    return json.loads((out / name).read_text(encoding="utf-8"))


def test_live_exit_codes_and_history(tmp_path, monkeypatch, cfg, demo_data):
    names, histories, markets, intraday = demo_data
    out = tmp_path / "data"

    # 交易所回傳的日期不符：非交易日或尚未更新 → 2，不寫檔
    _patch_live(monkeypatch, cfg, names, histories, markets, intraday, date(2026, 10, 29))
    assert main.main(["--date", "2026-10-30", "--out", str(out)]) == main.EXIT_SKIP
    assert not out.exists()

    # 交易所回應沒有資料（假日）→ 2
    def not_ready(m, c, d):
        raise universe.DataNotReady("很抱歉，沒有符合條件的資料!")

    monkeypatch.setattr(main.universe, "load_universe", not_ready)
    assert main.main(["--date", "2026-10-30", "--out", str(out)]) == main.EXIT_SKIP

    # 既有 v1 紀錄（只有日／週／月一份名單）要延續到第 2 組
    _patch_live(monkeypatch, cfg, names, histories, markets, intraday, DAY)
    univ = _universe(cfg, names, histories, markets)
    g2_hits = sorted(h["code"] for h in main.screen(univ, histories, DAY, cfg, intraday)["g2"]["hits"])
    out.mkdir()
    (out / "history.json").write_text(json.dumps({"demo": False, "days": [{"trade_date": "2026-10-29", "hits": g2_hits, "count": len(g2_hits)}]}))

    # 正常 → 0
    assert main.main(["--date", "2026-10-30", "--out", str(out)]) == main.EXIT_OK
    latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    groups = {g["id"]: g for g in latest["groups"]}
    assert latest["demo"] is False and latest["trade_date"] == "2026-10-30"
    assert g2_hits and all(h["streak"] == 2 for h in groups["g2"]["hits"])
    history = json.loads((out / "history.json").read_text(encoding="utf-8"))
    assert history["version"] == 2 and history["days"][1]["groups"] == {"g2": {"hits": g2_hits, "count": len(g2_hits)}}

    # 補跑排程：當日已有正式結果 → 2
    assert main.main(["--date", "2026-10-30", "--out", str(out), "--skip-if-current"]) == main.EXIT_SKIP

    # 60 分 K 全部抓不到：第 1 組停用，其他照常發布，history 不寫第 1 組
    monkeypatch.setattr(main.prices, "fetch_intraday", lambda codes, *a, **k: {})
    assert main.main(["--date", "2026-10-30", "--out", str(out)]) == main.EXIT_OK
    groups = {g["id"]: g for g in json.loads((out / "latest.json").read_text(encoding="utf-8"))["groups"]}
    assert groups["g1"]["available"] is False and groups["g1"]["warnings"]
    assert groups["g2"]["available"] is True
    history = json.loads((out / "history.json").read_text(encoding="utf-8"))
    assert "g1" not in history["days"][0]["groups"]

    # 日線抓價大量失敗 → 1，不覆寫既有結果
    before = (out / "latest.json").read_text(encoding="utf-8")
    monkeypatch.setattr(main.prices, "fetch_history", lambda codes, *a, **k: {})
    assert main.main(["--date", "2026-10-30", "--out", str(out)]) == main.EXIT_ERROR
    assert (out / "latest.json").read_text(encoding="utf-8") == before


def test_demo_date_rolls_back_from_weekend(tmp_path, monkeypatch, cfg):
    seen = {}
    monkeypatch.setattr(main, "run_demo", lambda c, out, d: seen.setdefault("d", d) and 0)
    main.main(["--demo", "--date", "2026-11-01", "--out", str(tmp_path)])  # 週日
    assert seen["d"] == pd.Timestamp("2026-10-30").date()


def test_screen_flags_stocks_behind_previous_trading_day(cfg, demo_data):
    # 所有股票一起缺 10/29、10/30：眾數基準跟著落後，只有交易所的前一交易日抓得出來
    names, histories, markets, intraday = demo_data
    univ = _universe(cfg, names, histories, markets)
    lagging = {c: df[df.index < pd.Timestamp("2026-10-29")] for c, df in histories.items()}
    assert main.screen(univ, lagging, DAY, cfg, intraday)["g2"]["fetch_failed"] == 1  # 舊做法：察覺不到
    r = main.screen(univ, lagging, DAY, cfg, intraday, previous_day=date(2026, 10, 29))
    assert r["g2"]["fetch_failed"] == len(univ) and r["g2"]["evaluated"] == 0


def test_live_guards(tmp_path, monkeypatch, cfg, demo_data):
    names, histories, markets, intraday = demo_data
    out = tmp_path / "data"
    args = ["--date", "2026-10-30", "--out", str(out)]
    _patch_live(monkeypatch, cfg, names, histories, markets, intraday, DAY)
    assert main.main(args) == main.EXIT_OK
    published = (out / "latest.json").read_text(encoding="utf-8")

    # Yahoo 日線整體落後（缺前一交易日）→ 2，不發布
    monkeypatch.setattr(main.prices, "fetch_history", lambda codes, *a, **k: {c: histories[c][histories[c].index < pd.Timestamp("2026-10-29")] for c in codes})
    assert main.main(args) == main.EXIT_SKIP
    assert (out / "latest.json").read_text(encoding="utf-8") == published

    # 交易所股票數只有上次的一半（資料更新到一半）→ 2，不發布
    _patch_live(monkeypatch, cfg, names, histories, markets, intraday, DAY)
    half = _universe(cfg, names, histories, markets).iloc[::2].reset_index(drop=True)
    monkeypatch.setattr(main.universe, "load_universe", lambda m, c, d: half)
    assert main.main(args) == main.EXIT_SKIP
    assert (out / "latest.json").read_text(encoding="utf-8") == published

    # 第 1 組停用時，補跑排程不可略過；60 分 K 恢復後補跑會把第 1 組補上
    _patch_live(monkeypatch, cfg, names, histories, markets, intraday, DAY)
    monkeypatch.setattr(main.prices, "fetch_intraday", lambda codes, *a, **k: {})
    assert main.main(args) == main.EXIT_OK
    assert {g["id"]: g["available"] for g in _read(out)["groups"]}["g1"] is False
    _patch_live(monkeypatch, cfg, names, histories, markets, intraday, DAY)
    assert main.main([*args, "--skip-if-current"]) == main.EXIT_OK
    assert all(g["available"] for g in _read(out)["groups"])
    assert main.main([*args, "--skip-if-current"]) == main.EXIT_SKIP  # 全部可用後才略過

    # 補跑較早的日期：只補歷史紀錄，儀表板仍顯示最新一天
    _patch_live(monkeypatch, cfg, names, histories, markets, intraday, date(2026, 10, 29))
    assert main.main(["--date", "2026-10-29", "--out", str(out)]) == main.EXIT_OK
    assert _read(out)["trade_date"] == "2026-10-30"
    assert [d["trade_date"] for d in _read(out, "history.json")["days"]][:2] == ["2026-10-30", "2026-10-29"]


def test_previous_day_flag(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(main, "taipei_now", lambda: pd.Timestamp("2026-11-03 07:23", tz="Asia/Taipei").to_pydatetime())
    monkeypatch.setattr(main, "run_live", lambda c, out, d, skip: seen.setdefault("d", d) and 0)
    main.main(["--previous-day", "--out", str(tmp_path)])
    assert seen["d"] == date(2026, 11, 2)  # 週二清晨補週一
    seen.clear()
    monkeypatch.setattr(main, "taipei_now", lambda: pd.Timestamp("2026-11-02 07:23", tz="Asia/Taipei").to_pydatetime())
    main.main(["--previous-day", "--out", str(tmp_path)])
    assert seen["d"] == date(2026, 10, 30)  # 週一清晨補上週五


def test_screen_handles_missing_13_bar(cfg, demo_data):
    names, histories, markets, intraday = demo_data
    univ = _universe(cfg, names, histories, markets)
    drop13 = lambda df: df[~((df.index.normalize().tz_localize(None) == pd.Timestamp(DAY)) & (df.index.hour == 13))]
    # 只有一檔缺 13:00（冷門股）：用官方收盤價補上，照常評估
    code = next(iter(intraday))
    one = {**intraday, code: drop13(intraday[code])}
    full = main.screen(univ, histories, DAY, cfg, intraday)
    r = main.screen(univ, histories, DAY, cfg, one)
    assert r["g1"]["evaluated"] == full["g1"]["evaluated"] and "60 分當日未收完" not in r["g1"]["insufficient"]
    # 全部都缺 13:00（Yahoo 還沒更新完）：整組判為未收完，交給停用與補跑機制
    r = main.screen(univ, histories, DAY, cfg, {c: drop13(df) for c, df in intraday.items()})
    assert r["g1"]["evaluated"] == 0 and r["g1"]["insufficient"]["60 分當日未收完"] > 0
    main.guard_intraday(r, cfg)
    assert r["g1"]["available"] is False and r["g2"]["available"] is True
