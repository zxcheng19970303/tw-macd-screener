"""選股條件（規格書第 2 節）。

所有條件只在這個檔案定義；參數與開關一律從 config.yaml 讀取。
"""

from __future__ import annotations

import pandas as pd

CONDITION_IDS = ("M1", "W1", "W2", "D1", "D2", "D3", "D4", "V1")
SWITCH_CHOICES = {
    "price_source": ("close", "hlcc4"),
    "htf_bar_mode": ("completed", "current"),
    "volume_rule": ("day_lots", "avg5_lots", "avg5_value"),
    "d2_mode": ("shrink_1d", "shrink_2d"),
}


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
    daily: pd.DataFrame, weekly: pd.DataFrame, monthly: pd.DataFrame, today: dict, cfg: dict
) -> dict:
    """判斷 8 個條件，回傳 {conditions, selected, values}。"""
    v = {
        "m_ro_dm": _at(monthly, "ro_dm"),
        "w_ro_dif": _at(weekly, "ro_dif"),
        "w_ro_dm": _at(weekly, "ro_dm"),
        "d_ro_dif": _at(daily, "ro_dif"),
        "d_ro_macd": _at(daily, "ro_macd"),
        "d_ro_dm": _at(daily, "ro_dm"),
        "d_ro_dm_prev": _at(daily, "ro_dm", 1),
        "d_ro_dm_prev2": _at(daily, "ro_dm", 2),
        "d_mi_macd": _at(daily, "mi_macd"),
        "d_mi_dm": _at(daily, "mi_dm"),
        "d_mi_dm_prev": _at(daily, "mi_dm", 1),
        "d_bo_dif": _at(daily, "bo_dif"),
        "d_bo_macd": _at(daily, "bo_macd"),
        "d_bo_dm": _at(daily, "bo_dm"),
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


def describe(cfg: dict) -> dict:
    """條件說明與對應數值欄位，寫入 latest.json 供儀表板顯示。"""
    sw = cfg["switches"]
    th = cfg["thresholds"]
    if sw["d2_mode"] == "shrink_1d":
        d2 = ("日線 羅 綠柱縮短或翻紅：D-M[1] < 0 且 D-M > D-M[1]", [["D-M[1]", "d_ro_dm_prev"], ["D-M", "d_ro_dm"]])
    else:
        d2 = (
            "日線 羅 綠柱連續 2 日縮短：D-M[2] < D-M[1] < 0 且 D-M > D-M[1]",
            [["D-M[2]", "d_ro_dm_prev2"], ["D-M[1]", "d_ro_dm_prev"], ["D-M", "d_ro_dm"]],
        )
    volume_text = {
        "day_lots": f"當日成交量 > {th['volume_lots']:,} 張",
        "avg5_lots": f"5 日均量 > {th['volume_lots']:,} 張",
        "avg5_value": f"5 日均成交金額 > {th['avg5_value'] / 1e8:g} 億元",
    }[sw["volume_rule"]]
    meta = {
        "M1": ("月線 羅 紅柱：D-M > 0", [["D-M", "m_ro_dm"]]),
        "W1": ("週線 羅 0 軸上：DIF > 0", [["DIF", "w_ro_dif"]]),
        "W2": ("週線 羅 D-M > 0", [["D-M", "w_ro_dm"]]),
        "D1": ("日線 羅 0 軸上：MACD > 0", [["MACD", "d_ro_macd"]]),
        "D2": d2,
        "D3": ("日線 蜜 0 軸上：MACD > 0", [["MACD", "d_mi_macd"]]),
        "D4": ("日線 蜜 D-M > 0 或綠柱縮短：D-M > D-M[1]", [["D-M[1]", "d_mi_dm_prev"], ["D-M", "d_mi_dm"]]),
        "V1": (volume_text, [["量", "volume"]]),
    }
    return {cid: {"label": label, "values": values} for cid, (label, values) in meta.items()}


def rule_tag(cfg: dict) -> str:
    """量能規則的簡短標示，例如 day_lots>300。"""
    rule = cfg["switches"]["volume_rule"]
    th = cfg["thresholds"]
    limit = th["avg5_value"] if rule == "avg5_value" else th["volume_lots"]
    return f"{rule}>{limit}"
