import json

from screener.report import compute_streaks, load_history, migrate_history, update_history


def hist(*days):
    """days：(日期, {組別: [代號...]})"""
    return {
        "version": 2,
        "demo": False,
        "days": [{"trade_date": d, "groups": {g: {"hits": h, "count": len(h)} for g, h in gs.items()}} for d, gs in days],
        "names": {},
        "markets": {},
    }


def hit(code, market="TWSE"):
    return {"code": code, "name": f"名{code}", "market": market}


def test_streaks_are_per_group():
    h = hist(
        ("2026-10-29", {"g2": ["2330", "2454"], "g1": ["2330"]}),
        ("2026-10-28", {"g2": ["2330"], "g1": ["2330"]}),
        ("2026-10-27", {"g2": ["2454"]}),  # 這天第 1 組沒有紀錄（例如停用）
    )
    assert compute_streaks(h, "2026-10-30", ["2330", "2454", "2317"], "g2") == {"2330": 3, "2454": 2, "2317": 1}
    assert compute_streaks(h, "2026-10-30", ["2330"], "g1") == {"2330": 3}
    assert compute_streaks(h, "2026-10-28", ["2454"], "g2") == {"2454": 2}


def test_streak_breaks_when_group_missing_that_day():
    h = hist(("2026-10-29", {"g2": ["2330"]}), ("2026-10-28", {"g1": ["2330"], "g2": ["2330"]}))
    assert compute_streaks(h, "2026-10-30", ["2330"], "g1") == {"2330": 1}


def test_update_history_overwrites_same_day_and_keeps_n_days():
    h = hist(*[(f"2026-09-{d:02d}", {"g2": ["1101"]}) for d in range(1, 30)])
    h = update_history(h, "2026-09-29", {"g1": [hit("6488", "TPEX")], "g2": [hit("2330")]}, keep_days=5)
    assert [d["trade_date"] for d in h["days"]] == ["2026-09-29", "2026-09-28", "2026-09-27", "2026-09-26", "2026-09-25"]
    assert h["days"][0]["groups"] == {"g1": {"hits": ["6488"], "count": 1}, "g2": {"hits": ["2330"], "count": 1}}
    assert h["names"] == {"2330": "名2330", "6488": "名6488"}  # 1101 的名稱沒有記錄過，不會憑空產生
    assert h["markets"] == {"2330": "TWSE", "6488": "TPEX"}


def test_names_pruned_when_day_expires():
    h = update_history(hist(), "2026-10-29", {"g2": [hit("1101")]}, keep_days=1)
    h = update_history(h, "2026-10-30", {"g2": [hit("2330")]}, keep_days=1)
    assert h["names"] == {"2330": "名2330"}


def test_migrate_v1_history_into_legacy_group():
    v1 = {"demo": False, "days": [{"trade_date": "2026-10-30", "hits": ["2609"], "count": 1}], "names": {"2609": "陽明"}}
    v2 = migrate_history(v1, "g2")
    assert v2["version"] == 2
    assert v2["days"] == [{"trade_date": "2026-10-30", "groups": {"g2": {"hits": ["2609"], "count": 1}}}]
    assert v2["names"] == {"2609": "陽明"}
    assert compute_streaks(v2, "2026-11-02", ["2609"], "g2") == {"2609": 2}  # 10/30 的連續天數延續下去


def test_load_history_resets_when_mode_differs(tmp_path):
    path = tmp_path / "history.json"
    assert load_history(path, demo=False)["days"] == []
    path.write_text(json.dumps({"demo": True, "days": [{"trade_date": "2026-10-30", "hits": ["1101"], "count": 1}]}))
    assert load_history(path, demo=False, legacy_group="g2")["days"] == []
    assert len(load_history(path, demo=True, legacy_group="g2")["days"]) == 1
