"""參數試算：同一天的資料用原設定與調整後的設定各跑一次選股，比較結果（不寫入 docs/data）。

python -m screener.whatif --date 2026-10-30 --set "macd.mi={fast: 10, slow: 20, signal: 10}"

--set 可重複，也可用分號一次給多組；值以 YAML 解析，例如 switches.d2_mode=shrink_2d、
thresholds.volume_lots=500。調整只影響選股計算；股票池與抓價範圍沿用原設定。
"""

from __future__ import annotations

import argparse
import copy
import logging
import sys
from datetime import date
from pathlib import Path

import yaml

from . import prices, report, rules, universe
from .main import EXIT_OK, EXIT_SKIP, ROOT, load_config, screen, taipei_now, uses_intraday

log = logging.getLogger("screener.whatif")


def parse_sets(items: list[str]) -> list[tuple[list[str], object]]:
    """把 'a.b=值; c=值' 拆成（路徑, 值）。"""
    out = []
    for chunk in items:
        for item in chunk.split(";"):
            item = item.strip()
            if not item:
                continue
            path, sep, raw = item.partition("=")
            if not sep or not path.strip():
                raise ValueError(f"--set 格式應為 key.path=值：{item}")
            out.append((path.strip().split("."), yaml.safe_load(raw.strip())))
    return out


def apply_overrides(cfg: dict, sets: list[tuple[list[str], object]]) -> dict:
    """回傳套用調整後的設定副本；路徑必須已存在於設定檔，避免打錯字卻默默沒生效。"""
    out = copy.deepcopy(cfg)
    for keys, value in sets:
        node = out
        for k in keys[:-1]:
            if not isinstance(node, dict) or k not in node:
                raise KeyError(f"設定檔沒有 {'.'.join(keys)}")
            node = node[k]
        if not isinstance(node, dict) or keys[-1] not in node:
            raise KeyError(f"設定檔沒有 {'.'.join(keys)}")
        node[keys[-1]] = value
    rules.validate_switches(out["switches"])
    rules.validate_groups(out["groups"])
    return out


def _names(items: list[dict]) -> dict[str, str]:
    return {h["code"]: h["name"] for h in items}


def summary(day: date, sets: list[tuple[list[str], object]], base: dict, variant: dict, metas: list[dict]) -> str:
    """base／variant 為 screen() 的結果（{名單 id: 結果}）；metas 為 report.group_metas()。"""
    def fmt(v):
        return yaml.safe_dump(v, default_flow_style=True, allow_unicode=True, sort_keys=False).strip().removesuffix("...").strip()

    changes = "；".join(f"`{'.'.join(k)} = {fmt(v)}`" for k, v in sets) or "（無）"
    lines = [f"## 參數試算 {day}", "", f"調整：{changes}"]
    for m in metas:
        b, v = base[m["id"]], variant[m["id"]]
        b_hits, v_hits = _names(b["hits"]), _names(v["hits"])
        names = {**_names(b["near_miss"]), **_names(v["near_miss"]), **b_hits, **v_hits}
        both = sorted(b_hits.keys() & v_hits.keys())
        added = sorted(v_hits.keys() - b_hits.keys())
        removed = sorted(b_hits.keys() - v_hits.keys())
        label = lambda codes: "、".join(f"{c} {names.get(c, '')}".strip() for c in codes) or "—"
        ids = list(m["conditions_meta"])
        lines += ["", f"### {m['name']}（{m['label']}）", "", "| 項目 | 原設定 | 試算 | 差 |", "| --- | --: | --: | --: |"]
        rows = [("入選", len(b["hits"]), len(v["hits"]))]
        if m["kind"] == "macd":
            rows.append(("差 1 條件", len(b["near_miss"]), len(v["near_miss"])))
        rows.append(("已評估", b["evaluated"], v["evaluated"]))
        rows += [(f"{cid} 通過", b["passed"][cid], v["passed"][cid]) for cid in ids]
        lines += [f"| {t} | {x} | {y} | {y - x:+d} |" for t, x, y in rows]
        lines += [
            "",
            f"- 兩者都入選（{len(both)}）：{label(both)}",
            f"- 試算新增（{len(added)}）：{label(added)}",
            f"- 試算移除（{len(removed)}）：{label(removed)}",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m screener.whatif", description="參數試算（不寫入 docs/data）")
    p.add_argument("--date", type=date.fromisoformat, help="交易日 YYYY-MM-DD（預設為台北時間今天）")
    p.add_argument("--set", dest="sets", action="append", default=[], help="key.path=值，可重複或以分號分隔")
    p.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)

    base_cfg = load_config(args.config)
    sets = parse_sets(args.sets)
    variant_cfg = apply_overrides(base_cfg, sets)
    day = args.date or taipei_now().date()
    try:
        univ = universe.load_universe(base_cfg["markets"], base_cfg, day)
        universe.check_trade_date(univ, day)
    except universe.DataNotReady as e:
        log.info("證交所沒有 %s 的資料：%s", day, e)
        return EXIT_SKIP
    pc = base_cfg["prices"]
    codes, markets = univ["code"].tolist(), dict(zip(univ["code"], univ["market"]))
    histories = prices.fetch_history(codes, pc["years"], day, pc["batch_size"], pc["retries"], pc["retry_wait_sec"], markets)
    intraday = None
    if uses_intraday(base_cfg) or uses_intraday(variant_cfg):
        intraday = prices.fetch_intraday(codes, pc["intraday_days"], day, pc["batch_size"], pc["intraday_retries"], pc["retry_wait_sec"], markets)
    base = screen(univ, histories, day, base_cfg, intraday)
    variant = screen(univ, histories, day, variant_cfg, intraday)
    text = summary(day, sets, base, variant, report.group_metas(variant_cfg))
    print(text)
    report.write_job_summary(text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
