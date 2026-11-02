"""每個條件各有通過與不通過的最小案例（規格書第 11 節）。"""

import pandas as pd
import pytest

from screener.rules import CONDITION_IDS, describe, evaluate, evaluate_watchlist, rule_tag, validate_groups, validate_switches

# 全部通過的基準值；序列由舊到新，最後一個是當根 [0]
BASE = {
    "m_ro_dm": [5.1],
    "w_ro_dif": [12.4],
    "w_ro_dm": [0.8],
    "d_ro_macd": [3.0],
    "d_ro_dm": [-2.0, -1.35, -0.82],
    "d_mi_macd": [6.0],
    "d_mi_dm": [0.3, 0.5],
}
TODAY = {"volume_lots": 32150, "avg5_lots": 30000, "avg5_value": 6e10}


def _frame(**cols):
    n = max(len(v) for v in cols.values())
    return pd.DataFrame({k: [float("nan")] * (n - len(v)) + v for k, v in cols.items()})


def run(cfg, today=None, **override):
    v = {**BASE, **override}
    monthly = _frame(ro_dm=v["m_ro_dm"])
    weekly = _frame(ro_dif=v["w_ro_dif"], ro_dm=v["w_ro_dm"])
    daily = _frame(
        ro_dif=[1.0],
        ro_macd=v["d_ro_macd"],
        ro_dm=v["d_ro_dm"],
        mi_macd=v["d_mi_macd"],
        mi_dm=v["d_mi_dm"],
        bo_dif=[0.1],
        bo_macd=[0.1],
        bo_dm=[0.0],
    )
    return evaluate(daily, weekly, monthly, today or TODAY, cfg)


def failed(res):
    return [cid for cid in CONDITION_IDS if not res["conditions"][cid]]


def test_all_pass(cfg):
    res = run(cfg)
    assert res["selected"] is True
    assert failed(res) == []
    assert res["values"]["d_ro_dm_prev"] == -1.35


@pytest.mark.parametrize(
    "cid, override",
    [
        ("M1", {"m_ro_dm": [-0.1]}),
        ("M1", {"m_ro_dm": [0.0]}),
        ("W1", {"w_ro_dif": [-3.0]}),
        ("W2", {"w_ro_dm": [-0.2]}),
        ("D1", {"d_ro_macd": [-0.5]}),
        ("D3", {"d_mi_macd": [-1.0]}),
    ],
)
def test_single_condition_fails(cfg, cid, override):
    res = run(cfg, **override)
    assert failed(res) == [cid]
    assert res["selected"] is False


@pytest.mark.parametrize(
    "seq, ok",
    [
        ([-1.35, -0.82], True),  # 綠柱縮短
        ([-0.5, 0.3], True),  # 綠翻紅
        ([-0.5, -0.8], False),  # 綠柱放大
        ([-0.5, -0.5], False),  # 持平不算縮短
        ([0.2, 0.5], False),  # 前一日已是紅柱
        ([0.0, 0.5], False),  # 前一日為 0 不算綠柱
    ],
)
def test_d2_shrink_1d(cfg, seq, ok):
    assert run(cfg, d_ro_dm=seq)["conditions"]["D2"] is ok


@pytest.mark.parametrize(
    "seq, ok",
    [
        ([-2.0, -1.5, -1.0], True),  # 連續 2 日縮短
        ([-2.0, -1.5, 0.4], True),  # 縮短後翻紅
        ([-1.0, -1.5, -1.2], False),  # 前一日先放大
        ([0.3, -0.5, -0.2], False),  # 前二日不是綠柱
    ],
)
def test_d2_shrink_2d(cfg, seq, ok):
    cfg["switches"]["d2_mode"] = "shrink_2d"
    assert run(cfg, d_ro_dm=seq)["conditions"]["D2"] is ok


@pytest.mark.parametrize(
    "seq, ok",
    [
        ([0.3, 0.5], True),  # 紅柱
        ([0.6, 0.2], True),  # 紅柱縮短仍為正
        ([-0.8, -0.4], True),  # 綠柱縮短
        ([-0.4, -0.8], False),  # 綠柱放大
    ],
)
def test_d4(cfg, seq, ok):
    assert run(cfg, d_mi_dm=seq)["conditions"]["D4"] is ok


@pytest.mark.parametrize(
    "rule, today, ok",
    [
        ("day_lots", {"volume_lots": 301}, True),
        ("day_lots", {"volume_lots": 300}, False),  # 嚴格大於
        ("avg5_lots", {"volume_lots": 5000, "avg5_lots": 250}, False),
        ("avg5_lots", {"volume_lots": 100, "avg5_lots": 350}, True),
        ("avg5_value", {"volume_lots": 100, "avg5_value": 6e7}, True),
        ("avg5_value", {"volume_lots": 9000, "avg5_value": 4e7}, False),
    ],
)
def test_v1(cfg, rule, today, ok):
    cfg["switches"]["volume_rule"] = rule
    res = run(cfg, today=today)
    assert res["conditions"]["V1"] is ok
    assert failed(res) == ([] if ok else ["V1"])


def test_missing_values_fail_safely(cfg):
    res = run(cfg, d_ro_dm=[-0.5])  # 只有一根，無法判斷 [1]
    assert res["conditions"]["D2"] is False
    assert res["values"]["d_ro_dm_prev"] is None


def test_describe_and_tag(cfg):
    g1, g2 = cfg["groups"]
    meta = describe(cfg, g2)
    assert list(meta) == list(CONDITION_IDS)
    assert "300 張" in meta["V1"]["label"]
    assert meta["M1"]["label"].startswith("上級（月線）") and meta["D2"]["label"].startswith("下級（日線）")
    meta = describe(cfg, g1)
    assert meta["M1"]["label"].startswith("上級（週線）") and meta["W1"]["label"].startswith("本級（日線）")
    assert meta["D4"]["label"].startswith("下級（60 分線）")
    assert rule_tag(cfg) == "day_lots>300"


@pytest.mark.parametrize(
    "groups",
    [
        [],
        [{"id": "a", "name": "x", "lower": "D", "middle": "W", "upper": "M"}] * 2,  # id 重複
        [{"id": "a", "name": "x", "lower": "5m", "middle": "D", "upper": "W"}],  # 不支援的週期
        [{"id": "a", "name": "x", "lower": "W", "middle": "D", "upper": "M"}],  # 順序錯誤
    ],
)
def test_validate_groups_rejects_bad_config(groups):
    with pytest.raises(ValueError):
        validate_groups(groups)


LIQUID = {"volume_lots": 5000}


def _watch(close, ma, macd, dif=-1.0):
    return pd.DataFrame({"Close": close, "ma10": ma, "ro_macd": [0.0] * (len(close) - 1) + [macd], "ro_dif": [0.0] * (len(close) - 1) + [dif]})


@pytest.mark.parametrize(
    "close, ma, macd, a1, a2",
    [
        ([99.0, 101.0], [100.0, 100.0], 0.5, True, True),  # 剛站上 10 日線，MACD > 0
        ([100.0, 101.0], [100.0, 100.5], 0.5, True, True),  # 昨收剛好等於均線也算「剛站上」
        ([102.0, 103.0], [100.0, 100.5], 0.5, True, False),  # 昨天就已在均線上：不是「剛」站上
        ([99.0, 101.0], [100.0, 100.0], -0.2, False, True),  # MACD 在 0 軸下
        ([99.0, 99.5], [100.0, 100.0], 0.5, True, False),  # 還沒站上
    ],
)
def test_watchlist(cfg, close, ma, macd, a1, a2):
    res = evaluate_watchlist(_watch(close, ma, macd), LIQUID, cfg)
    assert res["conditions"] == {"A1": a1, "A2": a2, "A3": True}
    assert res["selected"] is (a1 and a2)


def test_watchlist_ignores_dif(cfg):
    assert evaluate_watchlist(_watch([99.0, 101.0], [100.0, 100.0], 0.5, dif=-3.0), LIQUID, cfg)["selected"] is True


@pytest.mark.parametrize("lots, ok", [(201, True), (200, False), (12, False)])  # 嚴格大於 200 張
def test_watchlist_volume(cfg, lots, ok):
    res = evaluate_watchlist(_watch([99.0, 101.0], [100.0, 100.0], 0.5), {"volume_lots": lots}, cfg)
    assert res["conditions"]["A3"] is ok and res["selected"] is ok


def test_validate_switches(cfg):
    cfg["switches"]["d2_mode"] = "shrink_3d"
    with pytest.raises(ValueError):
        validate_switches(cfg["switches"])
