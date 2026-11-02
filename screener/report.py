"""輸出 latest.json、history.json 與 GitHub Actions job summary（規格書第 7、10 節）。

v2 格式：兩組週期與觀察清單各自一份名單（groups），history 依組別記錄。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from . import rules
from .indicators import TF_LINE
from .universe import MARKET_NAMES

log = logging.getLogger(__name__)
VERSION = 2


def _round(values: dict) -> dict:
    return {k: (None if v is None else round(v, 4)) for k, v in values.items()}


def empty_history(demo: bool) -> dict:
    return {"version": VERSION, "demo": demo, "days": [], "names": {}, "markets": {}}


def migrate_history(data: dict, legacy_group: str | None) -> dict:
    """v1 每天只有一份名單（日／週／月），轉成 v2 時歸到 legacy_group。"""
    days = []
    for d in data.get("days", []):
        if "groups" in d:
            days.append(d)
        elif legacy_group:
            days.append({"trade_date": d["trade_date"], "groups": {legacy_group: {"hits": d["hits"], "count": d["count"]}}})
    return {**empty_history(bool(data.get("demo"))), "days": days, "names": data.get("names", {}), "markets": data.get("markets", {})}


def load_history(path: Path, demo: bool, legacy_group: str | None = None) -> dict:
    """讀取歷史紀錄；模式（demo／正式）不同時捨棄，避免模擬資料混進正式紀錄。"""
    if not path.exists():
        return empty_history(demo)
    data = json.loads(path.read_text(encoding="utf-8"))
    if bool(data.get("demo")) != demo:
        log.info("history.json 為%s資料，本次重新開始累積", "模擬" if data.get("demo") else "正式")
        return empty_history(demo)
    return migrate_history(data, legacy_group)


def compute_streaks(history: dict, trade_date: str, codes: list[str], gid: str) -> dict[str, int]:
    """某組連續入選的交易日數（含當日）；往回數到該組名單沒有這檔、或該組當天沒有紀錄為止。"""
    prev = sorted((d for d in history["days"] if d["trade_date"] < trade_date), key=lambda d: d["trade_date"], reverse=True)
    streaks = {}
    for code in codes:
        n = 1
        for day in prev:
            g = day["groups"].get(gid)
            if not g or code not in g["hits"]:
                break
            n += 1
        streaks[code] = n
    return streaks


def update_history(history: dict, trade_date: str, hits_by_group: dict[str, list[dict]], keep_days: int) -> dict:
    """加入當日各組入選名單（同日重跑會覆蓋），只保留最近 keep_days 個交易日；停用的組不寫入。"""
    entry = {"trade_date": trade_date, "groups": {}}
    for gid, hits in hits_by_group.items():
        codes = sorted(h["code"] for h in hits)
        entry["groups"][gid] = {"hits": codes, "count": len(codes)}
    days = [d for d in history["days"] if d["trade_date"] != trade_date] + [entry]
    days = sorted(days, key=lambda d: d["trade_date"], reverse=True)[:keep_days]
    kept = {c for d in days for g in d["groups"].values() for c in g["hits"]}
    all_hits = [h for hits in hits_by_group.values() for h in hits]
    names = {**history.get("names", {}), **{h["code"]: h["name"] for h in all_hits}}
    markets = {**history.get("markets", {}), **{h["code"]: h["market"] for h in all_hits}}
    return {
        **empty_history(history["demo"]),
        "days": days,
        "names": {c: names[c] for c in sorted(kept) if c in names},
        "markets": {c: markets[c] for c in sorted(kept) if c in markets},
    }


def _group_payload(gid: str, meta: dict, result: dict, history: dict, td: str, cfg: dict) -> dict:
    streaks = compute_streaks(history, td, [h["code"] for h in result["hits"]], gid)
    hits = []
    for h in sorted(result["hits"], key=lambda x: x["code"]):
        s = streaks[h["code"]]
        hits.append({**h, "streak": s, "is_new": s == 1, "values": _round(h["values"])})
    near_miss = [{**n, "values": _round(n["values"])} for n in sorted(result["near_miss"], key=lambda x: x["code"])]
    stats = {
        "universe": result["universe"],
        "evaluated": result["evaluated"],
        "insufficient_data": sum(result["insufficient"].values()),
        "hits": len(hits),
        "near_miss": len(near_miss),
        "insufficient_reasons": dict(result["insufficient"]),
    }
    warnings = []
    if not result["available"]:
        warnings.append(result["unavailable_reason"])
    elif meta["kind"] == "macd" and not hits:
        warnings.append("今日入選數為 0，請確認資料來源與規則是否正常")
    elif len(hits) > result["universe"] * cfg["report"]["hits_warn_ratio"]:
        warnings.append(f"入選數 {len(hits)} 超過股票池 {cfg['report']['hits_warn_ratio']:.0%}，請確認資料是否異常")
    return {**meta, "available": result["available"], "stats": stats, "warnings": warnings, "hits": hits, "near_miss": near_miss}


def group_metas(cfg: dict) -> list[dict]:
    """各名單的顯示資訊：兩組週期（kind=macd）與觀察清單（kind=watch）。"""
    short = {"60m": "60 分", "D": "日", "W": "週", "M": "月"}
    metas = []
    for g in cfg["groups"]:
        metas.append(
            {
                "id": g["id"],
                "kind": "macd",
                "name": g["name"],
                "label": "／".join(short[g[s]] for s in ("lower", "middle", "upper")),
                "timeframes": {s: g[s] for s in ("lower", "middle", "upper")},
                "timeframe_names": {s: TF_LINE[g[s]] for s in ("lower", "middle", "upper")},
                "conditions_meta": rules.describe(cfg, g),
            }
        )
    wl = cfg.get("watchlist")
    if wl:
        metas.append(
            {
                "id": wl["id"],
                "kind": "watch",
                "name": wl["name"],
                "label": f"站上 {wl['ma_days']} 日線・量 > {wl['min_volume_lots']} 張",
                "conditions_meta": rules.describe_watchlist(cfg),
            }
        )
    return metas


def build_outputs(
    results: dict[str, dict], market_counts: dict[str, int], trade_date: date, cfg: dict, history: dict, demo: bool, generated_at: str
) -> tuple[dict, dict]:
    """組合 latest.json 內容並更新 history。"""
    td = trade_date.isoformat()
    groups = [_group_payload(m["id"], m, results[m["id"]], history, td, cfg) for m in group_metas(cfg)]
    sw = cfg["switches"]
    latest = {
        "version": VERSION,
        "trade_date": td,
        "generated_at": generated_at,
        "demo": demo,
        "config": {
            "price_source": sw["price_source"],
            "htf_bar_mode": sw["htf_bar_mode"],
            "volume_rule": rules.rule_tag(cfg),
            "d2_mode": sw["d2_mode"],
        },
        "universe": {"total": int(sum(market_counts.values())), "markets": {k: int(v) for k, v in market_counts.items()}},
        "groups": groups,
    }
    available = {g["id"]: g["hits"] for g in groups if g["available"]}
    return latest, update_history(history, td, available, cfg["report"]["history_days"])


def write_json(path: Path, payload: dict) -> None:
    """先寫暫存檔再改名，避免中途失敗留下半份 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_latest(out_dir: Path, latest: dict) -> None:
    write_json(out_dir / "latest.json", latest)


def write_history(out_dir: Path, history: dict) -> None:
    write_json(out_dir / "history.json", history)


def job_summary(latest: dict, fetch_failed: int, patched: int, elapsed_sec: float) -> str:
    u = latest["universe"]
    split = "、".join(f"{MARKET_NAMES.get(k, k)} {v}" for k, v in u["markets"].items())
    lines = [
        f"## MACD 選股 {latest['trade_date']}{'（demo）' if latest['demo'] else ''}",
        "",
        f"股票池 {u['total']} 檔（{split}）；日線抓價失敗 {fetch_failed} 檔；當日 K 棒以交易所官方行情補上 {patched} 檔；耗時 {elapsed_sec:.0f} 秒",
        "",
        "| 名單 | 週期 | 已評估 | 資料不足 | 入選 | 差 1 條件 |",
        "| --- | --- | --: | --: | --: | --: |",
    ]
    for g in latest["groups"]:
        s = g["stats"]
        status = "" if g["available"] else "（停用）"
        lines.append(f"| {g['name']}{status} | {g['label']} | {s['evaluated']} | {s['insufficient_data']} | {s['hits']} | {s['near_miss']} |")
    for g in latest["groups"]:
        s = g["stats"]
        detail = []
        if s["insufficient_reasons"]:
            detail.append("資料不足：" + "、".join(f"{k} {v}" for k, v in s["insufficient_reasons"].items()))
        detail += [f"⚠️ {w}" for w in g["warnings"]]
        if g["hits"]:
            detail.append("入選：" + "、".join(f"{h['code']} {h['name']}" for h in g["hits"][:60]) + ("…" if len(g["hits"]) > 60 else ""))
        if detail:
            lines += ["", f"**{g['name']}（{g['label']}）**", "", *[f"- {d}" for d in detail]]
    return "\n".join(lines) + "\n"


def write_job_summary(text: str) -> None:
    """在 GitHub Actions 中寫入 job summary；本機執行時略過。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
