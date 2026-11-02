"""股票池：證交所與櫃買中心的每日收盤行情、代號過濾與資料日期檢查（規格書第 4 節）。

不使用兩邊的 OpenAPI：證交所 STOCK_DAY_ALL 實測隔日清晨才更新，趕不上盤後排程；
改用網站端點，收盤當天即有資料，且可指定日期（規格書第 14 節）。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime

import pandas as pd
import requests

log = logging.getLogger(__name__)

COLUMNS = ["code", "name", "market", "open", "high", "low", "close", "volume_lots", "value", "trade_date"]
MARKET_NAMES = {"TWSE": "上市", "TPEX": "上櫃"}

# 各市場的端點與收盤行情表欄位名稱（以名稱定位，不依賴欄位順序；比對前先去掉空白與 <br>）
SOURCES = {
    "TWSE": {
        "url": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
        "params": lambda d: {"date": d.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
        "fields": {"code": "證券代號", "name": "證券名稱", "volume": "成交股數", "value": "成交金額",
                   "open": "開盤價", "high": "最高價", "low": "最低價", "close": "收盤價"},
    },
    "TPEX": {
        "url": "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc",
        "params": lambda d: {"date": d.strftime("%Y/%m/%d"), "type": "EW", "id": "", "response": "json"},
        "fields": {"code": "代號", "name": "名稱", "volume": "成交股數", "value": "成交金額(元)",
                   "open": "開盤", "high": "最高", "low": "最低", "close": "收盤"},
    },
}


class DataNotReady(Exception):
    """交易所沒有預期交易日的資料：非交易日或資料尚未更新。"""


def _num(x) -> float | None:
    s = str(x if x is not None else "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None  # 空字串、"--" 等無成交標記


def _norm(field: str) -> str:
    return re.sub(r"<br\s*/?>|\s", "", str(field))


def parse_quotes(
    payload: dict, market: str, code_pattern: str, exclude_name_patterns: list[str]
) -> pd.DataFrame:
    """解析證交所 MI_INDEX 或櫃買中心收盤行情，套用股票池過濾規則，成交量換算為張。"""
    if str(payload.get("stat", "")).lower() != "ok":
        raise DataNotReady(f"{MARKET_NAMES[market]}回應：{payload.get('stat')}")
    fields = SOURCES[market]["fields"]
    table = next((t for t in payload.get("tables", []) if fields["code"] in map(_norm, t.get("fields") or [])), None)
    if table is None:
        # 櫃買中心在非交易日仍回 ok，但收盤行情表是空的（沒有欄位或沒有資料）
        if market == "TPEX" and not any(t.get("data") for t in payload.get("tables", [])):
            raise DataNotReady(f"{MARKET_NAMES[market]}沒有 {payload.get('date')} 的收盤行情")
        raise RuntimeError(f"{MARKET_NAMES[market]}找不到每日收盤行情表，回傳格式可能已變更")
    if not table.get("data"):
        raise DataNotReady(f"{MARKET_NAMES[market]}沒有 {payload.get('date')} 的收盤行情")
    names = [_norm(f) for f in table["fields"]]
    missing = [v for v in fields.values() if v not in names]
    if missing:
        raise RuntimeError(f"{MARKET_NAMES[market]}收盤行情缺少欄位 {missing}，回傳格式可能已變更")
    col = {k: names.index(v) for k, v in fields.items()}
    trade_date = datetime.strptime(str(payload["date"]), "%Y%m%d").date()

    code_re = re.compile(code_pattern)
    name_res = [re.compile(p) for p in exclude_name_patterns]
    rows = []
    for r in table["data"]:
        code = str(r[col["code"]]).strip()
        name = str(r[col["name"]]).strip()
        if not code_re.fullmatch(code) or any(p.search(name) for p in name_res):
            continue
        close = _num(r[col["close"]])
        if close is None:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "market": market,
                "open": _num(r[col["open"]]) or close,
                "high": _num(r[col["high"]]) or close,
                "low": _num(r[col["low"]]) or close,
                "close": close,
                "volume_lots": (_num(r[col["volume"]]) or 0.0) / 1000,
                "value": _num(r[col["value"]]) or 0.0,
                "trade_date": trade_date,
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)


def fetch_quotes(market: str, day: date, timeout: float, retries: int, retry_wait: float) -> dict:
    """抓取指定市場、指定日期的每日收盤行情；連線失敗時重試 retries 次。"""
    src = SOURCES[market]
    headers = {"User-Agent": "Mozilla/5.0 tw-macd-screener/1.0", "Accept": "application/json"}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(src["url"], params=src["params"](day), timeout=timeout, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict) or "stat" not in data:
                raise ValueError("回傳內容不是預期的 JSON 物件")
            return data
        except (requests.RequestException, ValueError) as e:
            last_error = e
            log.warning("%s行情第 %d 次失敗：%s", MARKET_NAMES[market], attempt + 1, e)
            if attempt < retries:
                time.sleep(retry_wait)
    raise RuntimeError(f"{MARKET_NAMES[market]}行情連線失敗（已重試 {retries} 次）：{last_error}")


def load_universe(markets: list[str], cfg: dict, day: date) -> pd.DataFrame:
    """回傳上市、上櫃合併的股票池：code、name、market、open、high、low、close、volume_lots、value、trade_date。

    任一市場沒有當日資料就丟出 DataNotReady，避免只發布半個市場。
    """
    unsupported = set(markets) - set(SOURCES)
    if unsupported:
        raise NotImplementedError(f"不支援的市場：{sorted(unsupported)}")
    ec, uc = cfg["exchange"], cfg["universe"]
    frames = []
    for m in markets:
        payload = fetch_quotes(m, day, ec["timeout_sec"], ec["retries"], ec["retry_wait_sec"])
        frames.append(parse_quotes(payload, m, uc["code_pattern"], uc["exclude_name_patterns"]))
    return combine(frames)


def combine(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """合併各市場股票池；同一代號同時出現時（理論上不會）保留先出現者。"""
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    return df.drop_duplicates("code", keep="first").reset_index(drop=True)


def has_quotes(payload: dict) -> bool:
    """回應是否含當日收盤行情（stat 為 ok 且有資料列）。"""
    return str(payload.get("stat", "")).lower() == "ok" and any(t.get("data") for t in payload.get("tables", []))


def previous_trading_day(day: date, cfg: dict, max_back: int = 10) -> date | None:
    """往前逐一詢問證交所，找出 day 之前最近一個有收盤行情的日子（不需交易日曆）。

    用來確認 Yahoo 的日線至少更新到這一天：若所有股票一起落後，「多數股票的最後日期」也會跟著落後，單靠眾數察覺不到。
    """
    ec = cfg["exchange"]
    d = pd.Timestamp(day)
    for _ in range(max_back):
        d -= pd.offsets.BDay(1)
        if has_quotes(fetch_quotes("TWSE", d.date(), ec["timeout_sec"], 1, 3)):
            return d.date()
        time.sleep(1)  # 證交所限制查詢頻率
    return None


def check_universe_size(universe: pd.DataFrame, previous: dict[str, int], min_ratio: float) -> None:
    """任一市場股票數明顯少於上次發布：多半是交易所資料還在更新中，先不發布，等下一次排程。"""
    counts = universe["market"].value_counts().to_dict()
    for market, prev in previous.items():
        now = counts.get(market, 0)
        if prev and now < prev * min_ratio:
            raise DataNotReady(
                f"{MARKET_NAMES.get(market, market)}股票數 {now} 少於上次發布的 {prev}（門檻 {min_ratio:.0%}），疑似交易所資料尚未更新完整"
            )


def check_trade_date(universe: pd.DataFrame, expected: date) -> date:
    """交易所資料日期必須等於預期交易日，否則丟出 DataNotReady。"""
    if universe.empty:
        raise RuntimeError("股票池為空，請檢查交易所回傳內容或過濾規則")
    dates = sorted(set(universe["trade_date"]))
    if len(dates) != 1:
        raise RuntimeError(f"交易所資料日期不一致：{dates}")
    if dates[0] != expected:
        raise DataNotReady(f"交易所資料日期 {dates[0]} 不等於預期交易日 {expected}")
    return dates[0]
