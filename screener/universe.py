"""股票池：證交所 OpenAPI 當日行情、代號過濾與資料日期檢查（規格書第 4 節）。"""

from __future__ import annotations

import logging
import re
import time
from datetime import date

import pandas as pd
import requests

log = logging.getLogger(__name__)

STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
COLUMNS = ["code", "name", "market", "close", "volume_lots", "value", "trade_date"]


class DataNotReady(Exception):
    """OpenAPI 資料日期不等於預期交易日：非交易日或資料尚未更新。"""


def roc_to_date(text: str) -> date:
    """民國 YYYMMDD（如 1151030）轉西元日期。"""
    s = str(text).strip()
    return date(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:]))


def _num(x) -> float | None:
    s = str(x if x is not None else "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None  # 空字串、"--" 等無成交標記


def parse_stock_day_all(
    records: list[dict], code_pattern: str, exclude_name_patterns: list[str], market: str = "TWSE"
) -> pd.DataFrame:
    """套用股票池過濾規則，成交量換算為張。"""
    code_re = re.compile(code_pattern)
    name_res = [re.compile(p) for p in exclude_name_patterns]
    rows = []
    for r in records:
        code = str(r.get("Code", "")).strip()
        name = str(r.get("Name", "")).strip()
        if not code_re.fullmatch(code) or any(p.search(name) for p in name_res):
            continue
        close = _num(r.get("ClosingPrice"))
        if close is None:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "market": market,
                "close": close,
                "volume_lots": (_num(r.get("TradeVolume")) or 0.0) / 1000,
                "value": _num(r.get("TradeValue")) or 0.0,
                "trade_date": roc_to_date(r["Date"]),
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)


def fetch_stock_day_all(timeout: float, retries: int, retry_wait: float) -> list[dict]:
    """抓取上市當日全部行情；失敗時重試 retries 次。"""
    headers = {"User-Agent": "tw-macd-screener/1.0", "Accept": "application/json"}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(STOCK_DAY_ALL_URL, timeout=timeout, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                raise ValueError("回傳內容為空或格式不是陣列")
            return data
        except (requests.RequestException, ValueError) as e:
            last_error = e
            log.warning("證交所 OpenAPI 第 %d 次失敗：%s", attempt + 1, e)
            if attempt < retries:
                time.sleep(retry_wait)
    raise RuntimeError(f"證交所 OpenAPI 連線失敗（已重試 {retries} 次）：{last_error}")


def load_universe(markets: list[str], cfg: dict) -> pd.DataFrame:
    """回傳 DataFrame：code、name、market、close、volume_lots、value、trade_date。"""
    unsupported = set(markets) - {"TWSE"}
    if unsupported:
        raise NotImplementedError(f"v1 只支援 TWSE，不支援：{sorted(unsupported)}")
    oc, uc = cfg["openapi"], cfg["universe"]
    records = fetch_stock_day_all(oc["timeout_sec"], oc["retries"], oc["retry_wait_sec"])
    return parse_stock_day_all(records, uc["code_pattern"], uc["exclude_name_patterns"])


def check_trade_date(universe: pd.DataFrame, expected: date) -> date:
    """OpenAPI 的資料日期必須等於預期交易日，否則丟出 DataNotReady。"""
    if universe.empty:
        raise RuntimeError("股票池為空，請檢查 OpenAPI 回傳內容或過濾規則")
    dates = sorted(set(universe["trade_date"]))
    if len(dates) != 1:
        raise RuntimeError(f"OpenAPI 資料日期不一致：{dates}")
    if dates[0] != expected:
        raise DataNotReady(f"OpenAPI 資料日期 {dates[0]} 不等於預期交易日 {expected}")
    return dates[0]
