"""CLI 進入點：python -m screener.main [--demo] [--date YYYY-MM-DD]

結束碼：0 成功、2 非交易日或資料未更新（不發布）、1 錯誤（不發布）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from . import demo, indicators, prices, report, rules, universe

log = logging.getLogger("screener")

ROOT = Path(__file__).resolve().parent.parent
TPE = ZoneInfo("Asia/Taipei")
EXIT_OK, EXIT_ERROR, EXIT_SKIP = 0, 1, 2


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rules.validate_switches(cfg["switches"])
    rules.validate_groups(cfg["groups"])
    return cfg


def taipei_now() -> datetime:
    return datetime.now(TPE)


def uses_intraday(cfg: dict) -> bool:
    return any("60m" in (g["lower"], g["middle"], g["upper"]) for g in cfg["groups"])


def legacy_group(cfg: dict) -> str | None:
    """v1 的歷史紀錄（日／週／月）要歸到哪一組。"""
    return next((g["id"] for g in cfg["groups"] if (g["lower"], g["middle"], g["upper"]) == ("D", "W", "M")), None)


def _new_result(n: int) -> dict:
    return {
        "universe": n,
        "available": True,
        "evaluated": 0,
        "fetch_failed": 0,
        "intraday_failed": 0,
        "patched": 0,
        "insufficient": Counter(),
        "passed": Counter(),  # 各條件通過檔數（已評估的股票）
        "hits": [],
        "near_miss": [],
    }


def screen(
    univ: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
    trade_date: date,
    cfg: dict,
    intraday: dict[str, pd.DataFrame] | None = None,
) -> dict[str, dict]:
    """逐檔計算各週期指標（只算一次），再依每一組的下級／本級／上級套用規則。

    回傳 {組別 id: 入選、差 1 條件與統計}。
    """
    ts = pd.Timestamp(trade_date)
    reference = prices.reference_date(histories, ts)
    min_bars = cfg["universe"]["min_upper_bars"]
    groups = cfg["groups"]
    results = {g["id"]: _new_result(len(univ)) for g in groups}
    wl = cfg.get("watchlist")
    if wl:
        results[wl["id"]] = _new_result(len(univ))
    patched = 0
    for row in univ.itertuples(index=False):
        raw = histories.get(row.code)
        reason = prices.data_status(raw, ts, reference)
        if reason:
            for r in results.values():
                r["insufficient"][reason] += 1
                r["fetch_failed"] += 1
            continue
        df = prices.patch_today(raw, prices.official_bar(row), ts)
        patched += ts not in indicators.clean_daily(raw).index
        intra_reason = None
        if intraday is not None:
            intra_reason = prices.intraday_status(intraday.get(row.code), ts)
        frames = indicators.build_frames(df, ts, cfg, intraday.get(row.code) if intraday is not None and not intra_reason else None)
        today = {"volume_lots": row.volume_lots, **indicators.volume_averages(frames["D"], row.volume_lots, row.value)}
        item = {"code": row.code, "name": row.name, "market": row.market, "close": row.close, "volume_lots": round(row.volume_lots)}
        if wl:
            r = results[wl["id"]]
            if len(frames["D"]) < wl["min_bars"]:
                r["insufficient"][f"未滿 {wl['min_bars']} 日"] += 1
            else:
                res = rules.evaluate_watchlist(frames["D"], cfg)
                r["evaluated"] += 1
                r["passed"].update(cid for cid, ok in res["conditions"].items() if ok)
                if res["selected"]:
                    r["hits"].append({**item, "conditions": res["conditions"], "values": res["values"]})
        for g in groups:
            r = results[g["id"]]
            slots = (g["lower"], g["middle"], g["upper"])
            missing = [tf for tf in slots if tf not in frames]
            if missing:
                r["insufficient"][intra_reason or "60 分無資料"] += 1
                r["intraday_failed"] += 1
                continue
            if len(frames[g["upper"]]) < min_bars:
                r["insufficient"][f"未滿 {min_bars} {indicators.TF_UNIT[g['upper']]}"] += 1
                continue
            res = rules.evaluate(frames[g["lower"]], frames[g["middle"]], frames[g["upper"]], today, cfg)
            r["evaluated"] += 1
            r["passed"].update(cid for cid, ok in res["conditions"].items() if ok)
            failed = [cid for cid in rules.CONDITION_IDS if not res["conditions"][cid]]
            if len(failed) > 1:
                continue
            entry = {**item, "conditions": res["conditions"], "values": res["values"]}
            if failed:
                r["near_miss"].append({**entry, "failed": failed})
            else:
                r["hits"].append(entry)
    for r in results.values():
        r["patched"] = patched
    return results


def guard_intraday(results: dict[str, dict], cfg: dict) -> None:
    """60 分 K 抓取失敗比例過高時停用該組：寧可當日不更新，也不發布殘缺的名單。"""
    limit = cfg["prices"]["max_fail_ratio"]
    for gid, r in results.items():
        base = r["universe"] - r["fetch_failed"]
        ratio = r["intraday_failed"] / base if base else 0
        if ratio > limit:
            r["available"] = False
            r["unavailable_reason"] = f"60 分 K 抓取失敗 {ratio:.0%}（上限 {limit:.0%}），本組今日不更新"
            r["hits"], r["near_miss"] = [], []
            log.warning("%s：%s", gid, r["unavailable_reason"])


def _publish(results: dict[str, dict], univ: pd.DataFrame, trade_date: date, cfg: dict, history: dict, is_demo: bool):
    counts = univ["market"].value_counts().to_dict()
    return report.build_outputs(results, counts, trade_date, cfg, history, is_demo, taipei_now().isoformat(timespec="seconds"))


def run_live(cfg: dict, out_dir: Path, expected: date, skip_if_current: bool) -> int:
    started = time.monotonic()
    latest_path = out_dir / "latest.json"
    if skip_if_current and latest_path.exists():
        current = json.loads(latest_path.read_text(encoding="utf-8"))
        if not current.get("demo") and current.get("trade_date") == expected.isoformat():
            log.info("%s 已有正式結果，略過補跑", expected)
            return EXIT_SKIP

    univ = universe.load_universe(cfg["markets"], cfg, expected)
    trade_date = universe.check_trade_date(univ, expected)
    markets = dict(zip(univ["code"], univ["market"]))
    log.info("股票池 %d 檔（%s），交易日 %s", len(univ), univ["market"].value_counts().to_dict(), trade_date)

    pc = cfg["prices"]
    codes = univ["code"].tolist()
    histories = prices.fetch_history(codes, pc["years"], trade_date, pc["batch_size"], pc["retries"], pc["retry_wait_sec"], markets)
    intraday = None
    if uses_intraday(cfg):
        intraday = prices.fetch_intraday(
            codes, pc["intraday_days"], trade_date, pc["batch_size"], pc["intraday_retries"], pc["retry_wait_sec"], markets
        )
    results = screen(univ, histories, trade_date, cfg, intraday)
    first = next(iter(results.values()))
    fail_ratio = first["fetch_failed"] / len(univ)
    if fail_ratio > pc["max_fail_ratio"]:
        raise RuntimeError(f"日線抓價失敗比例 {fail_ratio:.1%} 超過 {pc['max_fail_ratio']:.0%}，本次不發布")
    guard_intraday(results, cfg)

    history = report.load_history(out_dir / "history.json", demo=False, legacy_group=legacy_group(cfg))
    latest, history = _publish(results, univ, trade_date, cfg, history, False)
    report.write_history(out_dir, history)
    report.write_latest(out_dir, latest)

    elapsed = time.monotonic() - started
    report.write_job_summary(report.job_summary(latest, first["fetch_failed"], first["patched"], elapsed))
    log.info("完成：%s，耗時 %.0f 秒", "、".join(f"{g['name']} 入選 {g['stats']['hits']}" for g in latest["groups"]), elapsed)
    return EXIT_OK


def run_demo(cfg: dict, out_dir: Path, trade_date: date, n_stocks: int = 120, days: int | None = None) -> int:
    """以模擬資料逐日重跑最近 history_days 個交易日，產出完整的 latest 與 history。"""
    started = time.monotonic()
    names, histories = demo.demo_dataset(trade_date, n=n_stocks)
    markets = demo.demo_markets(names)
    intraday = demo.demo_intraday(histories) if uses_intraday(cfg) else None
    dates = pd.bdate_range(end=pd.Timestamp(trade_date), periods=days or cfg["report"]["history_days"])
    uc = cfg["universe"]
    history = report.empty_history(demo=True)
    for i, ts in enumerate(dates, 1):
        if i % 10 == 0:
            log.info("demo 回補 %d/%d 日", i, len(dates))
        day = ts.date()
        univ = universe.combine(
            [
                universe.parse_quotes(demo.quotes(day, names, histories, markets, m), m, uc["code_pattern"], uc["exclude_name_patterns"])
                for m in cfg["markets"]
            ]
        )
        universe.check_trade_date(univ, day)
        results = screen(univ, histories, day, cfg, intraday)
        guard_intraday(results, cfg)
        latest, history = _publish(results, univ, day, cfg, history, True)
    report.write_history(out_dir, history)
    report.write_latest(out_dir, latest)
    elapsed = time.monotonic() - started
    first = next(iter(results.values()))
    report.write_job_summary(report.job_summary(latest, first["fetch_failed"], first["patched"], elapsed))
    log.info("demo 完成：%s，耗時 %.0f 秒", "、".join(f"{g['name']} 入選 {g['stats']['hits']}" for g in latest["groups"]), elapsed)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m screener.main", description="台股 MACD 三週期共振選股")
    p.add_argument("--demo", action="store_true", help="使用模擬資料，不連外部網路")
    p.add_argument("--date", type=date.fromisoformat, help="預期交易日 YYYY-MM-DD（預設為台北時間今天）")
    p.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    p.add_argument("--out", type=Path, default=ROOT / "docs" / "data")
    p.add_argument("--skip-if-current", action="store_true", help="latest.json 已是當日正式結果時直接結束（補跑排程用）")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    try:
        cfg = load_config(args.config)
        if args.demo:
            d = pd.Timestamp(args.date or taipei_now().date())
            last_bday = d if d.dayofweek < 5 else d - pd.offsets.BDay(1)
            return run_demo(cfg, args.out, last_bday.date())
        return run_live(cfg, args.out, args.date or taipei_now().date(), args.skip_if_current)
    except universe.DataNotReady as e:
        log.info("非交易日或資料未更新：%s", e)
        return EXIT_SKIP
    except Exception:
        log.exception("執行失敗，本次不發布")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
