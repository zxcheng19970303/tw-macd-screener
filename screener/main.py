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
    return cfg


def taipei_now() -> datetime:
    return datetime.now(TPE)


def screen(univ: pd.DataFrame, histories: dict[str, pd.DataFrame], trade_date: date, cfg: dict) -> dict:
    """逐檔計算指標並套用規則，回傳入選、差 1 條件與統計。"""
    ts = pd.Timestamp(trade_date)
    min_months = cfg["universe"]["min_months"]
    hits, near_miss = [], []
    insufficient: Counter[str] = Counter()
    fetch_failed = evaluated = 0
    for row in univ.itertuples(index=False):
        reason = prices.data_status(histories.get(row.code), ts)
        if reason:
            insufficient[reason] += 1
            fetch_failed += 1
            continue
        daily, weekly, monthly = indicators.build_frames(histories[row.code], ts, cfg)
        if len(monthly) < min_months:
            insufficient[f"未滿 {min_months} 個月"] += 1
            continue
        today = {"volume_lots": row.volume_lots, **indicators.volume_averages(daily, row.volume_lots, row.value)}
        res = rules.evaluate(daily, weekly, monthly, today, cfg)
        evaluated += 1
        failed = [cid for cid in rules.CONDITION_IDS if not res["conditions"][cid]]
        if len(failed) > 1:
            continue
        item = {
            "code": row.code,
            "name": row.name,
            "market": row.market,
            "close": row.close,
            "volume_lots": round(row.volume_lots),
            "conditions": res["conditions"],
            "values": res["values"],
        }
        if failed:
            near_miss.append({**item, "failed": failed})
        else:
            hits.append(item)
    return {
        "universe": len(univ),
        "evaluated": evaluated,
        "fetch_failed": fetch_failed,
        "insufficient": insufficient,
        "hits": hits,
        "near_miss": near_miss,
    }


def run_live(cfg: dict, out_dir: Path, expected: date, skip_if_current: bool) -> int:
    started = time.monotonic()
    latest_path = out_dir / "latest.json"
    if skip_if_current and latest_path.exists():
        current = json.loads(latest_path.read_text(encoding="utf-8"))
        if not current.get("demo") and current.get("trade_date") == expected.isoformat():
            log.info("%s 已有正式結果，略過補跑", expected)
            return EXIT_SKIP

    univ = universe.load_universe(cfg["markets"], cfg)
    trade_date = universe.check_trade_date(univ, expected)
    log.info("股票池 %d 檔，交易日 %s", len(univ), trade_date)

    pc = cfg["prices"]
    histories = prices.fetch_history(
        univ["code"].tolist(), pc["years"], trade_date, pc["batch_size"], pc["retries"], pc["retry_wait_sec"]
    )
    result = screen(univ, histories, trade_date, cfg)
    fail_ratio = result["fetch_failed"] / len(univ)
    if fail_ratio > pc["max_fail_ratio"]:
        raise RuntimeError(f"抓價失敗比例 {fail_ratio:.1%} 超過 {pc['max_fail_ratio']:.0%}，本次不發布")

    history = report.load_history(out_dir / "history.json", demo=False)
    latest, history = report.build_outputs(result, trade_date, cfg, history, False, taipei_now().isoformat(timespec="seconds"))
    report.write_history(out_dir, history)
    report.write_latest(out_dir, latest)

    elapsed = time.monotonic() - started
    summary = report.job_summary(latest, result["fetch_failed"], elapsed)
    report.write_job_summary(summary)
    log.info("完成：入選 %d、差 1 條件 %d，耗時 %.0f 秒", latest["stats"]["hits"], latest["stats"]["near_miss"], elapsed)
    return EXIT_OK


def run_demo(cfg: dict, out_dir: Path, trade_date: date, n_stocks: int = 150, days: int | None = None) -> int:
    """以模擬資料逐日重跑最近 history_days 個交易日，產出完整的 latest 與 history。"""
    started = time.monotonic()
    names, histories = demo.demo_dataset(trade_date, n=n_stocks)
    dates = pd.bdate_range(end=pd.Timestamp(trade_date), periods=days or cfg["report"]["history_days"])
    uc = cfg["universe"]
    history = report.empty_history(demo=True)
    for i, ts in enumerate(dates, 1):
        if i % 10 == 0:
            log.info("demo 回補 %d/%d 日", i, len(dates))
        day = ts.date()
        univ = universe.parse_stock_day_all(demo.stock_day_all(day, names, histories), uc["code_pattern"], uc["exclude_name_patterns"])
        universe.check_trade_date(univ, day)
        result = screen(univ, histories, day, cfg)
        latest, history = report.build_outputs(result, day, cfg, history, True, taipei_now().isoformat(timespec="seconds"))
    report.write_history(out_dir, history)
    report.write_latest(out_dir, latest)
    elapsed = time.monotonic() - started
    report.write_job_summary(report.job_summary(latest, result["fetch_failed"], elapsed))
    log.info("demo 完成：%s 入選 %d、差 1 條件 %d，耗時 %.0f 秒", trade_date, latest["stats"]["hits"], latest["stats"]["near_miss"], elapsed)
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
