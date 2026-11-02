"""數值比對（M3 驗收、人工抽查）：印出指定個股各週期最近 K 棒的 DIF、MACD、D-M，與兩組及觀察清單的條件結果。

python -m screener.compare 2330 6488 [--date YYYY-MM-DD] [--bars 3]
需連網。與每日選股相同：Yahoo 缺當日日 K 時以交易所官方行情補上；上櫃自動用 .TWO。
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from . import indicators, prices, report, rules, universe
from .main import ROOT, load_config, taipei_now, uses_intraday


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
        print(f"交易所 {day.date()} 沒有資料（{e}），只用 Yahoo 既有 K 棒")
        official = None
    markets = official["market"].to_dict() if official is not None else {}
    pc = cfg["prices"]
    histories = prices.fetch_history(args.codes, pc["years"], day.date(), retries=1, retry_wait=3, markets=markets)
    intraday = prices.fetch_intraday(args.codes, pc["intraday_days"], day.date(), markets=markets) if uses_intraday(cfg) else {}
    pd.set_option("display.width", 180)
    metas = {m["id"]: m for m in report.group_metas(cfg)}
    for code in args.codes:
        raw = histories.get(code)
        if raw is None:
            print(f"\n{code}：Yahoo 抓不到資料（若為上櫃股，請確認當日交易所資料可取得）")
            continue
        if official is not None and code in official.index:
            row = next(official.loc[[code]].itertuples())
            df, asof = prices.patch_today(raw, prices.official_bar(row), day), day
            lots, value = row.volume_lots, row.value
        else:
            df = indicators.clean_daily(raw)
            df = df[df.index <= day]
            asof = df.index[-1]
            lots = df["Volume"].iloc[-1] / 1000
            value = lots * 1000 * df["Close"].iloc[-1]
        intra = intraday.get(code)
        frames = indicators.build_frames(df, asof, cfg, intra if not prices.intraday_status(intra, asof) else None)
        today = {"volume_lots": lots, **indicators.volume_averages(frames["D"], lots, value)}
        market = markets.get(code, "TWSE")
        print(f"\n=== {code} {universe.MARKET_NAMES.get(market, market)}  {asof.date()}  price_source={cfg['switches']['price_source']} ===")
        for tf in indicators.TIMEFRAMES:
            if tf not in frames:
                if tf == "60m":
                    print(f"--- {indicators.TF_LINE[tf]} ---\n（{prices.intraday_status(intra, asof)}）")
                continue
            frame = frames[tf]
            sets = [k for k in ("ro", "mi", "bo") if f"{k}_dm" in frame.columns]
            cols = ["Close"] + [f"{s}_{k}" for s in sets for k in ("dif", "macd", "dm")]
            print(f"--- {indicators.TF_LINE[tf]} ---")
            print(frame[cols].tail(args.bars).round(4).to_string())
        for g in cfg["groups"]:
            m = metas[g["id"]]
            if any(s not in frames for s in (g["lower"], g["middle"], g["upper"])):
                print(f"{m['name']}（{m['label']}）：缺 60 分資料，無法判斷")
                continue
            res = rules.evaluate(frames[g["lower"]], frames[g["middle"]], frames[g["upper"]], today, cfg)
            marks = "  ".join(f"{cid}{'✓' if ok else '✗'}" for cid, ok in res["conditions"].items())
            print(f"{m['name']}（{m['label']}）：{marks}  → {'入選' if res['selected'] else '未入選'}")
        if cfg.get("watchlist"):
            wl = cfg["watchlist"]
            res = rules.evaluate_watchlist(frames["D"], today, cfg)
            marks = "  ".join(f"{cid}{'✓' if ok else '✗'}" for cid, ok in res["conditions"].items())
            v = res["values"]
            print(
                f"{wl['name']}：{marks}  → {'列入' if res['selected'] else '未列入'}"
                f"（{wl['ma_days']} 日線 {v['ma'] and round(v['ma'], 2)}，收盤 {v['close'] and round(v['close'], 2)}）"
            )


if __name__ == "__main__":
    main()
