"""輸出 latest.json、history.json 與 GitHub Actions job summary（規格書第 7、10 節）。"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from . import rules

log = logging.getLogger(__name__)


def _round(values: dict) -> dict:
    return {k: (None if v is None else round(v, 4)) for k, v in values.items()}


def empty_history(demo: bool) -> dict:
    return {"demo": demo, "days": [], "names": {}}


def load_history(path: Path, demo: bool) -> dict:
    """讀取歷史紀錄；模式（demo／正式）不同時捨棄，避免模擬資料混進正式紀錄。"""
    if not path.exists():
        return empty_history(demo)
    data = json.loads(path.read_text(encoding="utf-8"))
    if bool(data.get("demo")) != demo:
        log.info("history.json 為%s資料，本次重新開始累積", "模擬" if data.get("demo") else "正式")
        return empty_history(demo)
    data.setdefault("names", {})
    return data


def compute_streaks(history: dict, trade_date: str, codes: list[str]) -> dict[str, int]:
    """連續入選的交易日數（含當日）；以 history 中早於 trade_date 的紀錄往回數。"""
    prev = sorted((d for d in history["days"] if d["trade_date"] < trade_date), key=lambda d: d["trade_date"], reverse=True)
    streaks = {}
    for code in codes:
        n = 1
        for day in prev:
            if code not in day["hits"]:
                break
            n += 1
        streaks[code] = n
    return streaks


def update_history(history: dict, trade_date: str, hits: list[dict], keep_days: int) -> dict:
    """加入當日入選名單（同日重跑會覆蓋），只保留最近 keep_days 個交易日。"""
    codes = sorted(h["code"] for h in hits)
    days = [d for d in history["days"] if d["trade_date"] != trade_date]
    days.append({"trade_date": trade_date, "hits": codes, "count": len(codes)})
    days = sorted(days, key=lambda d: d["trade_date"], reverse=True)[:keep_days]
    kept = {c for d in days for c in d["hits"]}
    names = {**history.get("names", {}), **{h["code"]: h["name"] for h in hits}}
    return {"demo": history["demo"], "days": days, "names": {c: names[c] for c in sorted(kept) if c in names}}


def build_outputs(result: dict, trade_date: date, cfg: dict, history: dict, demo: bool, generated_at: str) -> tuple[dict, dict]:
    """組合 latest.json 內容並更新 history。"""
    td = trade_date.isoformat()
    streaks = compute_streaks(history, td, [h["code"] for h in result["hits"]])
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
    if not hits:
        warnings.append("今日入選數為 0，請確認資料來源與規則是否正常")
    elif len(hits) > result["universe"] * cfg["report"]["hits_warn_ratio"]:
        warnings.append(f"入選數 {len(hits)} 超過股票池 {cfg['report']['hits_warn_ratio']:.0%}，請確認資料是否異常")

    sw = cfg["switches"]
    latest = {
        "trade_date": td,
        "generated_at": generated_at,
        "demo": demo,
        "config": {
            "price_source": sw["price_source"],
            "htf_bar_mode": sw["htf_bar_mode"],
            "volume_rule": rules.rule_tag(cfg),
            "d2_mode": sw["d2_mode"],
        },
        "stats": stats,
        "warnings": warnings,
        "conditions_meta": rules.describe(cfg),
        "hits": hits,
        "near_miss": near_miss,
    }
    return latest, update_history(history, td, hits, cfg["report"]["history_days"])


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


def job_summary(latest: dict, fetch_failed: int, elapsed_sec: float) -> str:
    s = latest["stats"]
    lines = [
        f"## MACD 選股 {latest['trade_date']}{'（demo）' if latest['demo'] else ''}",
        "",
        "| 股票池 | 已評估 | 資料不足 | 抓價失敗 | 入選 | 差 1 條件 | 耗時 |",
        "| --: | --: | --: | --: | --: | --: | --: |",
        f"| {s['universe']} | {s['evaluated']} | {s['insufficient_data']} | {fetch_failed} | {s['hits']} | {s['near_miss']} | {elapsed_sec:.0f} 秒 |",
    ]
    if s["insufficient_reasons"]:
        lines += ["", "資料不足原因：" + "、".join(f"{k} {v}" for k, v in s["insufficient_reasons"].items())]
    if latest["warnings"]:
        lines += ["", *[f"> ⚠️ {w}" for w in latest["warnings"]]]
    if latest["hits"]:
        lines += ["", "入選：" + "、".join(f"{h['code']} {h['name']}" for h in latest["hits"])]
    return "\n".join(lines) + "\n"


def write_job_summary(text: str) -> None:
    """在 GitHub Actions 中寫入 job summary；本機執行時略過。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
