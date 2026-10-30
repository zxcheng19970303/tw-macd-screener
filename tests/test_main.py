"""main 的結束碼與 demo 輸出格式（不連網）。"""

import json
from datetime import date

import pandas as pd
import pytest

from screener import demo, main, universe

DAY = date(2026, 10, 30)


@pytest.fixture
def demo_data():
    return demo.demo_dataset(DAY, n=20)


def _universe(cfg, names, histories, day=DAY):
    uc = cfg["universe"]
    return universe.parse_mi_index(demo.mi_index(day, names, histories), uc["code_pattern"], uc["exclude_name_patterns"])


def test_demo_outputs(tmp_path, cfg):
    assert main.run_demo(cfg, tmp_path, DAY, n_stocks=20, days=3) == main.EXIT_OK
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    history = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert latest["trade_date"] == "2026-10-30"
    assert latest["demo"] is True and history["demo"] is True
    assert set(latest["stats"]) >= {"universe", "evaluated", "insufficient_data", "hits", "near_miss"}
    assert latest["stats"]["universe"] == 20  # EXCLUDED 的 ETF、特別股、TDR、無成交股已被過濾
    assert list(latest["conditions_meta"]) == ["M1", "W1", "W2", "D1", "D2", "D3", "D4", "V1"]
    assert [d["trade_date"] for d in history["days"]] == ["2026-10-30", "2026-10-29", "2026-10-28"]
    for h in latest["hits"]:
        assert all(h["conditions"].values()) and h["is_new"] == (h["streak"] == 1)
    for n in latest["near_miss"]:
        assert len(n["failed"]) == 1 and not n["conditions"][n["failed"][0]]


def test_screen_counts_insufficient(cfg, demo_data):
    names, histories = demo_data
    univ = _universe(cfg, names, histories)
    result = main.screen(univ, histories, DAY, cfg)
    reasons = result["insufficient"]
    assert reasons["未滿 60 個月"] == 1 and reasons["日期落後"] == 1  # 見 demo_dataset 的刻意設定
    assert result["fetch_failed"] == 1
    assert result["evaluated"] == 18
    assert result["patched"] == 0  # demo 的 Yahoo 資料已含當日


def test_screen_patches_today_when_yahoo_lags(cfg, demo_data):
    # 實測情境：Yahoo 當晚還沒有當日開高低收，只能用證交所官方行情補上
    names, histories = demo_data
    univ = _universe(cfg, names, histories)
    lagging = {c: df[df.index < pd.Timestamp(DAY)] for c, df in histories.items()}
    full = main.screen(univ, histories, DAY, cfg)
    patched = main.screen(univ, lagging, DAY, cfg)
    assert patched["evaluated"] == full["evaluated"]
    assert patched["patched"] == full["evaluated"] + full["insufficient"]["未滿 60 個月"]  # 抓價正常的都補上
    assert patched["insufficient"] == full["insufficient"]
    # 官方行情只到小數兩位，補上的 K 棒與原本只差四捨五入：名單相同、數值誤差 < 1%
    codes = lambda r: sorted(h["code"] for h in r["hits"] + r["near_miss"])
    dm = lambda r: [h["values"]["d_ro_dm"] for h in sorted(r["hits"] + r["near_miss"], key=lambda h: h["code"])]
    assert codes(patched) == codes(full)
    assert dm(patched) == pytest.approx(dm(full), rel=1e-2)


def _patch_live(monkeypatch, cfg, names, histories, day):
    monkeypatch.setattr(main.universe, "load_universe", lambda markets, c, d: _universe(cfg, names, histories, day))
    monkeypatch.setattr(main.prices, "fetch_history", lambda codes, *a, **k: {c: histories[c] for c in codes if c in histories})


def test_live_exit_codes(tmp_path, monkeypatch, cfg, demo_data):
    names, histories = demo_data
    out = tmp_path / "data"

    # 證交所回傳的日期不符：非交易日或尚未更新 → 2，不寫檔
    _patch_live(monkeypatch, cfg, names, histories, date(2026, 10, 29))
    assert main.main(["--date", "2026-10-30", "--out", str(out)]) == main.EXIT_SKIP
    assert not out.exists()

    # 證交所回應沒有資料（假日）→ 2
    def not_ready(markets, c, d):
        raise universe.DataNotReady("很抱歉，沒有符合條件的資料!")

    monkeypatch.setattr(main.universe, "load_universe", not_ready)
    assert main.main(["--date", "2026-10-30", "--out", str(out)]) == main.EXIT_SKIP

    # 正常 → 0
    _patch_live(monkeypatch, cfg, names, histories, DAY)
    assert main.main(["--date", "2026-10-30", "--out", str(out)]) == main.EXIT_OK
    latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert latest["demo"] is False and latest["trade_date"] == "2026-10-30"

    # 補跑排程：當日已有正式結果 → 2
    assert main.main(["--date", "2026-10-30", "--out", str(out), "--skip-if-current"]) == main.EXIT_SKIP

    # 抓價大量失敗 → 1，不覆寫既有結果
    before = (out / "latest.json").read_text(encoding="utf-8")
    monkeypatch.setattr(main.prices, "fetch_history", lambda codes, *a, **k: {})
    assert main.main(["--date", "2026-10-30", "--out", str(out)]) == main.EXIT_ERROR
    assert (out / "latest.json").read_text(encoding="utf-8") == before


def test_demo_date_rolls_back_from_weekend(tmp_path, monkeypatch, cfg):
    seen = {}
    monkeypatch.setattr(main, "run_demo", lambda c, out, d: seen.setdefault("d", d) and 0)
    main.main(["--demo", "--date", "2026-11-01", "--out", str(tmp_path)])  # 週日
    assert seen["d"] == pd.Timestamp("2026-10-30").date()
