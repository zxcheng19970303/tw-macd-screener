"""股票池：證交所每日收盤行情（MI_INDEX）、代號過濾與資料日期檢查（規格書第 4 節）。

不使用 OpenAPI 的 STOCK_DAY_ALL：實測該端點隔日清晨才更新，趕不上盤後排程（規格書第 14 節）。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime

import pandas as pd
import requests

log = logging.getLogger(__name__)

MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
COLUMNS = ["code", "name", "market", "open", "high", "low", "close", "volume_lots", "value", "trade_date"]
# 每日收盤行情表的欄位名稱；以名稱定位，不依賴欄位順序
FIELDS = {
    "code": "證券代號",
    "name": "證券名稱",
    "volume": "成交股數",
    "value": "成交金額",
    "open": "開盤價",
    "high": "最高價",
    "low": "最低價",
    "close": "收盤價",
}


class DataNotReady(Exception):
    """證交所沒有預期交易日的資料：非交易日或資料尚未更新。"""


def _num(x) -> float | None:
    s = str(x if x is not None else "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None  # 空字串、"--" 等無成交標記


def _quote_table(payload: dict) -> dict:
    for table in payload.get("tables", []):
        if FIELDS["code"] in (table.get("fields") or []):
            return table
    raise RuntimeError("MI_INDEX 找不到每日收盤行情表，回傳格式可能已變更")


def parse_mi_index(
    payload: dict, code_pattern: str, exclude_name_patterns: list[str], market: str = "TWSE"
) -> pd.DataFrame:
    """套用股票池過濾規則，成交量換算為張。"""
    if payload.get("stat") != "OK":
        raise DataNotReady(f"證交所回應：{payload.get('stat')}")
    trade_date = datetime.strptime(str(payload["date"]), "%Y%m%d").date()
    table = _quote_table(payload)
    missing = [v for v in FIELDS.values() if v not in table["fields"]]
    if missing:
        raise RuntimeError(f"MI_INDEX 缺少欄位 {missing}，回傳格式可能已變更")
    col = {k: table["fields"].index(v) for k, v in FIELDS.items()}

    code_re = re.compile(code_pattern)
    name_res = [re.compile(p) for p in exclude_name_patterns]
    rows = []
    for r in table.get("data", []):
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


def fetch_mi_index(day: date, timeout: float, retries: int, retry_wait: float) -> dict:
    """抓取指定日期的上市每日收盤行情（不含權證、牛熊證）；連線失敗時重試 retries 次。"""
    params = {"date": day.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"}
    headers = {"User-Agent": "tw-macd-screener/1.0", "Accept": "application/json"}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(MI_INDEX_URL, params=params, timeout=timeout, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict) or "stat" not in data:
                raise ValueError("回傳內容不是預期的 JSON 物件")
            return data
        except (requests.RequestException, ValueError) as e:
            last_error = e
            log.warning("證交所 MI_INDEX 第 %d 次失敗：%s", attempt + 1, e)
            if attempt < retries:
                time.sleep(retry_wait)
    raise RuntimeError(f"證交所 MI_INDEX 連線失敗（已重試 {retries} 次）：{last_error}")


def load_universe(markets: list[str], cfg: dict, day: date) -> pd.DataFrame:
    """回傳 DataFrame：code、name、market、open、high、low、close、volume_lots、value、trade_date。"""
    unsupported = set(markets) - {"TWSE"}
    if unsupported:
        raise NotImplementedError(f"v1 只支援 TWSE，不支援：{sorted(unsupported)}")
    tc, uc = cfg["twse"], cfg["universe"]
    payload = fetch_mi_index(day, tc["timeout_sec"], tc["retries"], tc["retry_wait_sec"])
    return parse_mi_index(payload, uc["code_pattern"], uc["exclude_name_patterns"])


def check_trade_date(universe: pd.DataFrame, expected: date) -> date:
    """證交所資料日期必須等於預期交易日，否則丟出 DataNotReady。"""
    if universe.empty:
        raise RuntimeError("股票池為空，請檢查證交所回傳內容或過濾規則")
    dates = sorted(set(universe["trade_date"]))
    if len(dates) != 1:
        raise RuntimeError(f"證交所資料日期不一致：{dates}")
    if dates[0] != expected:
        raise DataNotReady(f"證交所資料日期 {dates[0]} 不等於預期交易日 {expected}")
    return dates[0]
