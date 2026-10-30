"""數值比對（M3 驗收）：印出指定個股最近 K 棒的 DIF、MACD、D-M 與條件結果。

python -m screener.compare 2330 2317 2454 [--date YYYY-MM-DD] [--bars 3]
需連網（yfinance）；量能條件以 yfinance 日線量代替證交所數值，僅供參考。
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from . import indicators, prices, rules
from .main import ROOT, load_config, taipei_now


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m screener.compare")
    p.add_argument("codes", nargs="+")
    p.add_argument("--date", type=pd.Timestamp, help="比對日期，預設為最近一個有資料的交易日")
    p.add_argument("--bars", type=int, default=3, help="每個週期顯示幾根 K 棒")
    args = p.parse_args(argv)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    cfg = load_config(ROOT / "config.yaml")
    end = (args.date or pd.Timestamp(taipei_now().date())).date()
    histories = prices.fetch_history(args.codes, cfg["prices"]["years"], end, retries=1, retry_wait=3)
    pd.set_option("display.width", 160)
    for code in args.codes:
        df = histories.get(code)
        if df is None:
            print(f"\n{code}：抓不到資料")
            continue
        day = indicators.clean_daily(df).index[-1]
        daily, weekly, monthly = indicators.build_frames(df, day, cfg)
        print(f"\n=== {code}  {day.date()}  price_source={cfg['switches']['price_source']} ===")
        for label, frame, sets in (("日線", daily, ("ro", "mi", "bo")), ("週線", weekly, ("ro",)), ("月線", monthly, ("ro",))):
            cols = ["Close"] + [f"{s}_{k}" for s in sets for k in ("dif", "macd", "dm")]
            print(f"--- {label} ---")
            print(frame[cols].tail(args.bars).round(4).to_string())
        lots = daily["Volume"].iloc[-1] / 1000
        res = rules.evaluate(daily, weekly, monthly, {"volume_lots": lots, **indicators.volume_averages(daily, lots, lots * 1000 * daily["Close"].iloc[-1])}, cfg)
        marks = "  ".join(f"{cid}{'✓' if ok else '✗'}" for cid, ok in res["conditions"].items())
        print(f"條件：{marks}  → {'入選' if res['selected'] else '未入選'}")


if __name__ == "__main__":
    main()
