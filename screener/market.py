"""大盤指數：加權指數與櫃買指數的當日收盤與近一年日線，輸出 market.json 給儀表板。

只用來顯示，不影響選股；任何一步失敗都保留上次的資料，不擋住每日發布。

資料源（2026-11-19 實測）：
- 加權指數：當日收盤取證交所 MI_INDEX 的「價格指數(臺灣證券交易所)」表（股票池已抓過，不必再連線）；
  歷史取 Yahoo ^TWII（當晚常缺當天，由官方值補上）。
- 櫃買指數：櫃買中心「櫃買指數(月查詢)」，一次回傳一個月的日線；Yahoo ^TWOII 已沒有資料。
"""

from __future__ import annotations

import json
import logging
import re
import time
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)
VERSION = 1
INDICES = {"TAIEX": "加權指數", "TPEX": "櫃買指數"}
TPEX_MONTH_URL = "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx"
HEADERS = {"User-Agent": "Mozilla/5.0 tw-macd-screener/1.0", "Accept": "application/json"}

Series = dict[str, float]  # {"YYYY-MM-DD": 收盤}


def _num(x) -> float | None:
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def _iso(text: str) -> str | None:
    """'2026/11/18'、'115/11/18'、'20261118' 轉成 ISO 日期；民國年自動加 1911。"""
    parts = re.findall(r"\d+", str(text))
    if len(parts) == 1 and len(parts[0]) == 8:
        parts = [parts[0][:4], parts[0][4:6], parts[0][6:]]
    if len(parts) != 3:
        return None
    y, m, d = (int(p) for p in parts)
    try:
        return date(y + 1911 if y < 1911 else y, m, d).isoformat()
    except ValueError:
        return None


def taiex_from_mi_index(payload: dict | None) -> Series:
    """從證交所 MI_INDEX 回應取出當日加權指數收盤；格式不符時回傳空的。"""
    if not payload:
        return {}
    day = _iso(payload.get("date", ""))
    for t in payload.get("tables", []):
        fields = [re.sub(r"\s", "", f) for f in t.get("fields") or []]
        if "指數" not in fields or "收盤指數" not in fields:
            continue
        for row in t.get("data") or []:
            if str(row[fields.index("指數")]).strip() == "發行量加權股價指數":
                close = _num(row[fields.index("收盤指數")])
                return {day: close} if day and close else {}
    return {}


def parse_tpex_month(payload: dict) -> Series:
    """櫃買指數月查詢：每列 [日期, 開市, 最高, 最低, 收市, 漲/跌]。"""
    if str(payload.get("stat", "")).lower() != "ok":
        return {}
    out = {}
    for t in payload.get("tables", []):
        fields = [re.sub(r"\s", "", f) for f in t.get("fields") or []]
        if "日期" not in fields or "收市" not in fields:
            continue
        for row in t.get("data") or []:
            day, close = _iso(row[fields.index("日期")]), _num(row[fields.index("收市")])
            if day and close:
                out[day] = close
    return out


def fetch_tpex_month(month: date, timeout: float) -> Series:
    params = {"date": f"{month:%Y/%m}/01", "response": "json"}
    resp = requests.get(TPEX_MONTH_URL, params=params, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return parse_tpex_month(resp.json())


def months_back(end: date, n: int) -> list[date]:
    """end 所在月份往前共 n 個月的月初（新到舊）。"""
    out, y, m = [], end.year, end.month
    for _ in range(n):
        out.append(date(y, m, 1))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def tpex_history(old: Series, end: date, cfg: dict, errors: list[str]) -> Series:
    """每次抓當月與上個月（補當天與月初的缺漏）；更早的月份只在舊資料沒有時回補（首次執行約 14 次請求）。

    當月或上個月失敗時丟出例外；更早月份失敗只記錄，下次執行會再補。
    """
    mc = cfg["market"]
    have = {d[:7] for d in old}
    new: Series = {}
    for i, month in enumerate(months_back(end, mc["history_days"] // 28 + 1)):
        if i >= 2 and f"{month:%Y-%m}" in have:
            continue
        if i:
            time.sleep(mc["request_wait_sec"])  # 櫃買中心限制查詢頻率
        try:
            new.update(fetch_tpex_month(month, mc["timeout_sec"]))
        except (requests.RequestException, ValueError) as e:
            if i < 2:
                raise
            errors.append(f"櫃買指數 {month:%Y-%m} 回補失敗：{e}")
    return new


def yahoo_history(symbol: str, end: date, days: int) -> Series:
    import yfinance as yf

    start = end - timedelta(days=days)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(symbol, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                         interval="1d", auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return {}
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return {ts.date().isoformat(): float(v) for ts, v in close.dropna().items() if v > 0}


def merge_series(old: Series, new: Series, end: date, days: int) -> Series:
    """合併新舊資料（新的覆蓋同一天），去掉 end 之後與 days 天以前的資料，依日期排序。"""
    start = (end - timedelta(days=days)).isoformat()
    merged = {**old, **new}
    return {d: round(merged[d], 2) for d in sorted(merged) if start <= d <= end.isoformat()}


def summarize(index_id: str, series: Series) -> dict:
    """最新一天的收盤、漲跌與漲跌幅（與序列中前一天比較）。"""
    dates = sorted(series)
    entry = {"id": index_id, "name": INDICES[index_id], "date": None, "close": None, "change": None, "change_pct": None,
             "dates": dates, "closes": [series[d] for d in dates]}
    if not dates:
        return entry
    close = series[dates[-1]]
    entry.update(date=dates[-1], close=close)
    if len(dates) > 1:
        prev = series[dates[-2]]
        entry.update(change=round(close - prev, 2), change_pct=round((close - prev) / prev * 100, 2))
    return entry


def load_market(path: Path, demo: bool) -> dict[str, Series]:
    """讀取上次的指數序列；模式（demo／正式）不同或檔案損壞時從頭開始。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if bool(data.get("demo")) != demo:
        return {}
    return {i["id"]: dict(zip(i.get("dates", []), i.get("closes", []))) for i in data.get("indices", []) if i.get("id") in INDICES}


def taiex_history(old: Series, end: date, cfg: dict, mi_payload: dict | None, errors: list[str]) -> Series:
    """Yahoo 一年日線打底，已存的官方值優先，當天以證交所官方收盤為準；Yahoo 失敗時仍可只用官方值。"""
    try:
        yahoo = yahoo_history(cfg["market"]["taiex_symbol"], end, cfg["market"]["history_days"])
    except Exception as e:  # noqa: BLE001 - yfinance 的錯誤型別不固定
        errors.append(f"加權指數 Yahoo 歷史抓取失敗：{e}")
        yahoo = {}
    return {**yahoo, **old, **taiex_from_mi_index(mi_payload)}


def build_market(trade_date: date, cfg: dict, old: dict[str, Series], mi_payload: dict | None, demo: bool,
                 generated_at: str) -> tuple[dict, list[str]]:
    """更新兩個指數；個別失敗時沿用舊資料並回報錯誤訊息。"""
    errors: list[str] = []
    fetchers = {
        "TAIEX": lambda prev: taiex_history(prev, trade_date, cfg, mi_payload, errors),
        "TPEX": lambda prev: tpex_history(prev, trade_date, cfg, errors),
    }
    indices = []
    for index_id, fetch in fetchers.items():
        prev = old.get(index_id, {})
        try:
            new = fetch(prev)
        except Exception as e:  # noqa: BLE001 - 大盤只是顯示用，任何錯誤都不能擋住選股結果發布
            log.warning("%s更新失敗，沿用上次資料：%s", INDICES[index_id], e)
            errors.append(f"{INDICES[index_id]}：{e}")
            new = {}
        series = merge_series(prev, new, trade_date, cfg["market"]["history_days"])
        if series and max(series) < trade_date.isoformat():
            errors.append(f"{INDICES[index_id]}資料只到 {max(series)}")
        indices.append(summarize(index_id, series))
    payload = {"version": VERSION, "demo": demo, "trade_date": trade_date.isoformat(), "generated_at": generated_at, "indices": indices}
    return payload, errors


def summary_line(market: dict, errors: list[str]) -> str:
    parts = []
    for i in market["indices"]:
        if i["close"] is None:
            parts.append(f"{i['name']} 無資料")
            continue
        chg = "" if i["change_pct"] is None else f"（{i['change_pct']:+.2f}%）"
        parts.append(f"{i['name']} {i['close']:,.2f}{chg}")
    line = "大盤：" + "・".join(parts)
    if errors:
        line += "\n\n" + "\n".join(f"- ⚠️ {e}" for e in errors)
    return "\n" + line + "\n"


def demo_market(trade_date: date, cfg: dict, generated_at: str, seed: int = 7) -> dict:
    """模擬的指數走勢（隨機漫步），讓 --demo 也能預覽大盤區塊。"""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=pd.Timestamp(trade_date), periods=int(cfg["market"]["history_days"] * 5 / 7))
    indices = []
    for index_id, base in (("TAIEX", 22000.0), ("TPEX", 250.0)):
        closes = base * np.exp(np.cumsum(rng.normal(0.0006, 0.011, len(days))))
        indices.append(summarize(index_id, {d.date().isoformat(): round(float(c), 2) for d, c in zip(days, closes)}))
    return {"version": VERSION, "demo": True, "trade_date": trade_date.isoformat(), "generated_at": generated_at, "indices": indices}


def write_market(out_dir: Path, market: dict) -> None:
    """與 latest.json 相同，先寫暫存檔再改名；陣列放同一行，檔案小一些。"""
    path = out_dir / "market.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(market, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(path)
