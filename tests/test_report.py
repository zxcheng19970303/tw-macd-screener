import json

from screener.report import compute_streaks, load_history, update_history


def hist(*days):
    return {"demo": False, "days": [{"trade_date": d, "hits": h, "count": len(h)} for d, h in days], "names": {}}


def hit(code):
    return {"code": code, "name": f"名{code}"}


def test_streaks_count_consecutive_previous_days():
    h = hist(("2026-10-29", ["2330", "2454"]), ("2026-10-28", ["2330"]), ("2026-10-27", ["2454"]))
    s = compute_streaks(h, "2026-10-30", ["2330", "2454", "2317"])
    assert s == {"2330": 3, "2454": 2, "2317": 1}


def test_streak_ignores_same_day_rerun():
    h = hist(("2026-10-30", ["2330"]), ("2026-10-29", ["2330"]))
    assert compute_streaks(h, "2026-10-30", ["2330"]) == {"2330": 2}


def test_update_history_overwrites_same_day_and_keeps_n_days():
    h = hist(*[(f"2026-09-{d:02d}", ["1101"]) for d in range(1, 30)])
    h = update_history(h, "2026-09-29", [hit("2330")], keep_days=5)
    assert [d["trade_date"] for d in h["days"]] == ["2026-09-29", "2026-09-28", "2026-09-27", "2026-09-26", "2026-09-25"]
    assert h["days"][0] == {"trade_date": "2026-09-29", "hits": ["2330"], "count": 1}
    assert h["names"] == {"2330": "名2330"}  # 1101 的名稱沒有記錄過，不會憑空產生


def test_names_pruned_when_day_expires():
    h = update_history(hist(), "2026-10-29", [hit("1101")], keep_days=1)
    h = update_history(h, "2026-10-30", [hit("2330")], keep_days=1)
    assert h["names"] == {"2330": "名2330"}


def test_load_history_resets_when_mode_differs(tmp_path):
    path = tmp_path / "history.json"
    assert load_history(path, demo=False)["days"] == []
    path.write_text(json.dumps({"demo": True, "days": [{"trade_date": "2026-10-30", "hits": ["1101"], "count": 1}]}))
    assert load_history(path, demo=False)["days"] == []
    assert len(load_history(path, demo=True)["days"]) == 1
