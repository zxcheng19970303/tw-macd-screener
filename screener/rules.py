"""選股條件（規格書第 2 節）。

所有條件只在這個檔案定義；參數與開關一律從 config.yaml 讀取。
條件代號沿用講義：M＝上級週期、W＝本級週期、D＝下級週期；兩組週期套用同一套條件，
第 1 組（60 分／日／週）的 M1 看週線、W1／W2 看日線、D1～D4 看 60 分線。
"""

from __future__ import annotations

import pandas as pd

CONDITION_IDS = ("M1", "W1", "W2", "D1", "D2", "D3", "D4", "V1")
WATCH_IDS = ("A1", "A2", "A3")
SWITCH_CHOICES = {
    "price_source": ("close", "hlcc4"),
    "htf_bar_mode": ("completed", "current"),
    "volume_rule": ("day_lots", "avg5_lots", "avg5_value"),
    "d2_mode": ("shrink_1d", "shrink_2d"),
}


TIMEFRAMES = ("60m", "D", "W", "M")
TF_LINE = {"60m": "60 分線", "D": "日線", "W": "週線", "M": "月線"}


def validate_groups(groups: list[dict]) -> None:
    ids = [g.get("id") for g in groups]
    if not groups or len(set(ids)) != len(ids):
        raise ValueError(f"groups 不可為空，且 id 不可重複：{ids}")
    for g in groups:
        for slot in ("lower", "middle", "upper"):
            if g.get(slot) not in TIMEFRAMES:
                raise ValueError(f"groups.{g.get('id')}.{slot} 必須是 {TIMEFRAMES} 之一，目前為 {g.get(slot)!r}")
        order = [TIMEFRAMES.index(g[s]) for s in ("lower", "middle", "upper")]
        if not order[0] < order[1] < order[2]:
            raise ValueError(f"groups.{g['id']} 的週期必須由短到長：下級 < 本級 < 上級")


def validate_switches(switches: dict) -> None:
    for key, choices in SWITCH_CHOICES.items():
        if switches.get(key) not in choices:
            raise ValueError(f"switches.{key} 必須是 {choices} 之一，目前為 {switches.get(key)!r}")


def _at(frame: pd.DataFrame, col: str, back: int = 0) -> float | None:
    """倒數第 back 根 K 棒的值（0 = 當根，對應規格的 [0]、[1]）；不足或 NaN 回傳 None。"""
    if frame is None or len(frame) <= back:
        return None
    x = frame[col].iloc[-1 - back]
    return None if pd.isna(x) else float(x)


def _gt(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and a > b


def _lt(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and a < b


def volume_metric(today: dict, cfg: dict) -> tuple[float | None, float]:
    """依 volume_rule 回傳（比較值, 門檻）。"""
    rule = cfg["switches"]["volume_rule"]
    th = cfg["thresholds"]
    if rule == "day_lots":
        return today.get("volume_lots"), th["volume_lots"]
    if rule == "avg5_lots":
        return today.get("avg5_lots"), th["volume_lots"]
    return today.get("avg5_value"), th["avg5_value"]


def _d2(v: dict, mode: str) -> bool:
    dm0, dm1, dm2 = v["d_ro_dm"], v["d_ro_dm_prev"], v["d_ro_dm_prev2"]
    if mode == "shrink_1d":
        # 前一日為綠柱，今日比前一日短（含綠翻紅）
        return _lt(dm1, 0) and _gt(dm0, dm1)
    # 連續 2 日縮短：前兩日皆為綠柱，且逐日變短
    return _lt(dm2, 0) and _lt(dm1, 0) and _gt(dm1, dm2) and _gt(dm0, dm1)


def evaluate(
    lower: pd.DataFrame, middle: pd.DataFrame, upper: pd.DataFrame, today: dict, cfg: dict
) -> dict:
    """判斷 8 個條件，回傳 {conditions, selected, values}。

    lower／middle／upper 為該組的下級／本級／上級週期 K 棒（第 2 組＝日／週／月）。
    數值欄位名稱沿用 d_／w_／m_ 前綴，分別代表下級／本級／上級。
    """
    v = {
        "m_ro_dm": _at(upper, "ro_dm"),
        "w_ro_dif": _at(middle, "ro_dif"),
        "w_ro_dm": _at(middle, "ro_dm"),
        "d_ro_dif": _at(lower, "ro_dif"),
        "d_ro_macd": _at(lower, "ro_macd"),
        "d_ro_dm": _at(lower, "ro_dm"),
        "d_ro_dm_prev": _at(lower, "ro_dm", 1),
        "d_ro_dm_prev2": _at(lower, "ro_dm", 2),
        "d_mi_macd": _at(lower, "mi_macd"),
        "d_mi_dm": _at(lower, "mi_dm"),
        "d_mi_dm_prev": _at(lower, "mi_dm", 1),
        "d_bo_dif": _at(lower, "bo_dif"),
        "d_bo_macd": _at(lower, "bo_macd"),
        "d_bo_dm": _at(lower, "bo_dm"),
    }
    volume, threshold = volume_metric(today, cfg)
    v["volume"] = volume

    c = {
        "M1": _gt(v["m_ro_dm"], 0),
        "W1": _gt(v["w_ro_dif"], 0),
        "W2": _gt(v["w_ro_dm"], 0),
        "D1": _gt(v["d_ro_macd"], 0),
        "D2": _d2(v, cfg["switches"]["d2_mode"]),
        "D3": _gt(v["d_mi_macd"], 0),
        "D4": _gt(v["d_mi_dm"], 0) or _gt(v["d_mi_dm"], v["d_mi_dm_prev"]),
        "V1": _gt(volume, threshold),
    }
    return {"conditions": c, "selected": all(c.values()), "values": v}


def describe(cfg: dict, group: dict) -> dict:
    """條件說明與對應數值欄位（標示該組實際週期），寫入 latest.json 供儀表板顯示。"""
    sw = cfg["switches"]
    th = cfg["thresholds"]
    up, mid, low = (f"{name}（{TF_LINE[group[slot]]}）" for name, slot in (("上級", "upper"), ("本級", "middle"), ("下級", "lower")))
    if sw["d2_mode"] == "shrink_1d":
        d2 = (f"{low} 羅 綠柱縮短或翻紅：D-M[1] < 0 且 D-M > D-M[1]", [["D-M[1]", "d_ro_dm_prev"], ["D-M", "d_ro_dm"]])
    else:
        d2 = (
            f"{low} 羅 綠柱連續 2 根縮短：D-M[2] < D-M[1] < 0 且 D-M > D-M[1]",
            [["D-M[2]", "d_ro_dm_prev2"], ["D-M[1]", "d_ro_dm_prev"], ["D-M", "d_ro_dm"]],
        )
    volume_text = {
        "day_lots": f"當日成交量 > {th['volume_lots']:,} 張",
        "avg5_lots": f"5 日均量 > {th['volume_lots']:,} 張",
        "avg5_value": f"5 日均成交金額 > {th['avg5_value'] / 1e8:g} 億元",
    }[sw["volume_rule"]]
    meta = {
        "M1": (f"{up} 羅 紅柱：D-M > 0", [["D-M", "m_ro_dm"]]),
        "W1": (f"{mid} 羅 0 軸上：DIF > 0", [["DIF", "w_ro_dif"]]),
        "W2": (f"{mid} 羅 D-M > 0", [["D-M", "w_ro_dm"]]),
        "D1": (f"{low} 羅 0 軸上：MACD > 0", [["MACD", "d_ro_macd"]]),
        "D2": d2,
        "D3": (f"{low} 蜜 0 軸上：MACD > 0", [["MACD", "d_mi_macd"]]),
        "D4": (f"{low} 蜜 D-M > 0 或綠柱縮短：D-M > D-M[1]", [["D-M[1]", "d_mi_dm_prev"], ["D-M", "d_mi_dm"]]),
        "V1": (volume_text, [["量", "volume"]]),
    }
    return {cid: {"label": label, "values": values} for cid, (label, values) in meta.items()}


def evaluate_watchlist(daily: pd.DataFrame, today: dict, cfg: dict) -> dict:
    """觀察清單（獨立於 8 個條件）：A1 日線 MACD > 0，不看 DIF；A2 今天剛站上 N 日線；A3 當日成交量 > 門檻張數。"""
    wl = cfg["watchlist"]
    k, ma = wl["macd_set"], f"ma{wl['ma_days']}"
    v = {
        "macd": _at(daily, f"{k}_macd"),
        "dif": _at(daily, f"{k}_dif"),
        "close": _at(daily, "Close"),
        "close_prev": _at(daily, "Close", 1),
        "ma": _at(daily, ma),
        "ma_prev": _at(daily, ma, 1),
        "volume_lots": today.get("volume_lots"),
    }
    crossed = v["close_prev"] is not None and v["ma_prev"] is not None and v["close_prev"] <= v["ma_prev"] and _gt(v["close"], v["ma"])
    c = {"A1": _gt(v["macd"], 0), "A2": crossed, "A3": _gt(v["volume_lots"], wl["min_volume_lots"])}
    return {"conditions": c, "selected": all(c.values()), "values": v}


def describe_watchlist(cfg: dict) -> dict:
    wl = cfg["watchlist"]
    p = cfg["macd"][wl["macd_set"]]
    n = wl["ma_days"]
    names = {"bo": "波", "ro": "羅", "mi": "蜜"}
    meta = {
        "A1": (f"日線 {names.get(wl['macd_set'], wl['macd_set'])}（{p['fast']}/{p['slow']}/{p['signal']}）MACD > 0，不看 DIF", [["MACD", "macd"], ["DIF", "dif"]]),
        "A2": (f"剛站上 {n} 日線：昨收 ≤ {n} 日均線，今收 > {n} 日均線", [["昨收", "close_prev"], ["昨均線", "ma_prev"], ["今收", "close"], ["今均線", "ma"]]),
        "A3": (f"當日成交量 > {wl['min_volume_lots']:,} 張", [["量", "volume_lots"]]),
    }
    return {cid: {"label": label, "values": values} for cid, (label, values) in meta.items()}


def rule_tag(cfg: dict) -> str:
    """量能規則的簡短標示，例如 day_lots>300。"""
    rule = cfg["switches"]["volume_rule"]
    th = cfg["thresholds"]
    limit = th["avg5_value"] if rule == "avg5_value" else th["volume_lots"]
    return f"{rule}>{limit}"
