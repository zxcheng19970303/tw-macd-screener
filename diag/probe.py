"""暫時的指數資料源偵測，跑完即移除。"""
import json
import time
import warnings

import requests

warnings.simplefilter("ignore")
UA = {"User-Agent": "Mozilla/5.0 tw-macd-screener/1.0", "Accept": "application/json", "Referer": "https://www.tpex.org.tw/"}


def get(name, url, params=None):
    try:
        r = requests.get(url, params=params, headers=UA, timeout=30)
        print(f"\n[{name}] {r.status_code} {r.headers.get('Content-Type')} final={r.url[:160]}")
        try:
            return r.json()
        except ValueError:
            print("  非 JSON：", r.text[:200].replace("\n", " "))
    except Exception as e:
        print(f"\n[{name}] ERROR {type(e).__name__}: {str(e)[:200]}")
    return None


def show_tables(j, keep=3):
    if isinstance(j, dict):
        print("  keys", list(j)[:12], "stat", j.get("stat"), "date", j.get("date") or j.get("reportDate"))
        for t in (j.get("tables") or [])[:8]:
            rows = t.get("data") or []
            print("  table:", t.get("title"), "| rows", len(rows), "| fields", t.get("fields"))
            for r in rows[:keep]:
                print("    ", r)
        for k in ("aaData", "data"):
            if isinstance(j.get(k), list):
                print(f"  {k}", len(j[k]), j[k][:keep])
    elif isinstance(j, list):
        print("  list", len(j), "keys", list(j[0])[:20] if j and isinstance(j[0], dict) else None)
        for r in j[:keep]:
            print("    ", r)


print("===== 1. 證交所 MI_INDEX 2026-11-18：加權指數列、個股漲跌欄 =====")
j = get("MI_INDEX", "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", {"date": "20261118", "type": "ALLBUT0999", "response": "json"})
if j:
    for t in j.get("tables", []):
        rows = t.get("data") or []
        if t.get("fields") and "指數" in t["fields"]:
            print("  table:", t.get("title"), t["fields"])
            print("    ", [r for r in rows if "發行量加權" in str(r[0])][:2])
        if t.get("fields") and "證券代號" in t["fields"]:
            print("  quote fields", t["fields"])
            signs = {}
            for r in rows:
                signs[r[9]] = signs.get(r[9], 0) + 1
            print("  漲跌(+/-) 值分布", signs)
            print("    2330", [r for r in rows if r[0] == "2330"])
time.sleep(3)

print("\n===== 2. 櫃買中心個股漲跌欄（afterTrading/otc 2026/11/18）=====")
j = get("otc", "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc", {"date": "2026/11/18", "type": "EW", "id": "", "response": "json"})
if j and j.get("tables"):
    rows = j["tables"][0].get("data") or []
    vals = {}
    for r in rows:
        v = str(r[3]).strip()
        key = v[:1] if v and v[0] in "+-" else v
        vals[key] = vals.get(key, 0) + 1
    print("  漲跌 開頭字元分布", vals)
    print("    6488", [r[:6] for r in rows if r[0] == "6488"])
time.sleep(3)

print("\n===== 3. 櫃買指數候選端點 =====")
cands = [
    ("新網站 indexInfo/inx", "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx", {"date": "2026/11/18", "response": "json"}),
    ("新網站 afterTrading/indexSummary", "https://www.tpex.org.tw/www/zh-tw/afterTrading/indexSummary", {"date": "2026/11/18", "response": "json"}),
    ("舊 index_summary", "https://www.tpex.org.tw/web/stock/aftertrading/index_summary/summary_result.php", {"l": "zh-tw", "d": "115/11/18", "o": "json"}),
    ("舊 inxh 月歷史", "https://www.tpex.org.tw/web/stock/iNdex_info/inxh/Inx_result.php", {"l": "zh-tw", "d": "115/11", "o": "json"}),
    ("OpenAPI tpex_index", "https://www.tpex.org.tw/openapi/v1/tpex_index", None),
]
for name, url, params in cands:
    show_tables(get(name, url, params))
    time.sleep(3)

print("\n===== 4. Yahoo ^TWII、^TWOII 一年日線 =====")
import yfinance as yf

df = yf.download(["^TWII", "^TWOII"], period="1y", interval="1d", auto_adjust=True, group_by="ticker", progress=False)
for s in ("^TWII", "^TWOII"):
    if s in df.columns.get_level_values(0):
        sub = df[s]
        print(s, "rows", len(sub), "非空收盤", int(sub["Close"].notna().sum()), "first", sub.index[0].date())
        print(sub.tail(4).round(2).to_string())
    else:
        print(s, "沒有資料")
