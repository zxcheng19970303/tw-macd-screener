"""數值比對（M3 驗收、人工抽查）：印出指定個股最近 K 棒的 DIF、MACD、D-M 與條件結果。

python -m screener.compare 2330 2317 2454 [--date YYYY-MM-DD] [--bars 3]
需連網。與每日選股相同：Yahoo 缺當日 K 棒時以證交所官方行情補上。
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from . import indicators, prices, rules, universe
from .main import ROOT, load_config, taipei_now


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m screener.compare")
    p.add_argument("codes", nargs="+")
    p.add_argument("--date", type=pd.Timestamp, help="比對日期，預設為台北時間今天")
    p.add_argument("--bars", type=int, default=3, help="每個週期顯示幾根 K 棒")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    cfg = load_config(ROOT / "config.yaml")
    day = (args.date or pd.Timestamp(taipei_now().date())).normalize()
    try:
        official = universe.load_universe(cfg["markets"], cfg, day.date()).set_index("code")
    except universe.DataNotReady as e:
        print(f"證交所 {day.date()} 沒有資料（{e}），只用 Yahoo 既有 K 棒")
        official = None
    histories = prices.fetch_history(args.codes, cfg["prices"]["years"], day.date(), retries=1, retry_wait=3)
    pd.set_option("display.width", 160)
    for code in args.codes:
        raw = histories.get(code)
        if raw is None:
            print(f"\n{code}：Yahoo 抓不到資料")
            continue
        if official is not None and code in official.index:
            row = next(official.loc[[code]].itertuples())
            df, asof = prices.patch_today(raw, prices.official_bar(row), day), day
            today = {"volume_lots": row.volume_lots, **indicators.volume_averages(df[df.index <= day], row.volume_lots, row.value)}
        else:
            df = indicators.clean_daily(raw)
            df = df[df.index <= day]
            asof = df.index[-1]
            lots = df["Volume"].iloc[-1] / 1000
            today = {"volume_lots": lots, **indicators.volume_averages(df, lots, lots * 1000 * df["Close"].iloc[-1])}
        daily, weekly, monthly = indicators.build_frames(df, asof, cfg)
        print(f"\n=== {code}  {asof.date()}  price_source={cfg['switches']['price_source']} ===")
        for label, frame, sets in (("日線", daily, ("ro", "mi", "bo")), ("週線", weekly, ("ro",)), ("月線", monthly, ("ro",))):
            cols = ["Close"] + [f"{s}_{k}" for s in sets for k in ("dif", "macd", "dm")]
            print(f"--- {label} ---")
            print(frame[cols].tail(args.bars).round(4).to_string())
        res = rules.evaluate(daily, weekly, monthly, today, cfg)
        marks = "  ".join(f"{cid}{'✓' if ok else '✗'}" for cid, ok in res["conditions"].items())
        print(f"條件：{marks}  → {'入選' if res['selected'] else '未入選'}")


if __name__ == "__main__":
    main()
