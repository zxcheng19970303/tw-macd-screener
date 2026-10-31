"""暫時的資料源偵測：櫃買中心當日行情、Yahoo 60 分 K。跑完即移除。"""
import time
from collections import Counter

import requests

UA = {"User-Agent": "Mozilla/5.0 tw-macd-screener/1.0", "Accept": "application/json", "Referer": "https://www.tpex.org.tw/"}


def section(t):
    print(f"\n===== {t} =====", flush=True)


def show(name, url, params=None):
    try:
        r = requests.get(url, params=params, headers=UA, timeout=30)
        print(f"[{name}] status {r.status_code} final={r.url[:150]}")
        for h in ("Last-Modified", "Content-Type"):
            print(f"  {h}: {r.headers.get(h)}")
        j = r.json()
        return j
    except Exception as e:
        print(f"[{name}] ERROR {type(e).__name__}: {str(e)[:200]}")
        return None


section("1a. TPEx OpenAPI tpex_mainboard_daily_close_quotes")
j = show("openapi", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes")
if isinstance(j, list) and j:
    print("  records", len(j), "keys", list(j[0])[:20])
    print("  dates", Counter(str(d.get("Date")) for d in j).most_common(3))
    print("  6488:", [d for d in j if str(d.get("SecuritiesCompanyCode")) == "6488"][:1])

section("1b. TPEx 新網站 afterTrading/otc 2026/10/30")
j = show("www-otc", "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc", {"date": "2026/10/30", "type": "EW", "id": "", "response": "json"})
if isinstance(j, dict):
    print("  keys", list(j)[:12], "stat", j.get("stat"), "date", j.get("date"))
    for t in j.get("tables", [])[:6]:
        print("  table:", t.get("title"), "| rows", len(t.get("data", [])), "| fields", t.get("fields"))
        rows = t.get("data", [])
        print("  first:", rows[:2])
        print("  6488:", [r for r in rows if r and str(r[0]).strip() == "6488"][:1])
time.sleep(3)

section("1c. TPEx 舊 API stk_wn1430_result.php 115/10/30")
j = show("legacy", "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php", {"l": "zh-tw", "d": "115/10/30", "se": "EW", "o": "json"})
if isinstance(j, dict):
    print("  keys", list(j)[:15], "reportDate", j.get("reportDate"), "iTotalRecords", j.get("iTotalRecords"))
    for t in j.get("tables", [])[:3]:
        print("  table:", t.get("title"), "| rows", len(t.get("data", [])), "| fields", t.get("fields"))
    rows = j.get("aaData") or []
    print("  aaData rows", len(rows), "first", rows[:2])
time.sleep(3)

section("1d. TPEx 新網站 afterTrading/otc 2026/10/31（週六，預期無資料）")
j = show("www-otc-sat", "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc", {"date": "2026/10/31", "type": "EW", "id": "", "response": "json"})
if isinstance(j, dict):
    print("  stat", j.get("stat"), "date", j.get("date"), "tables", [len(t.get("data", [])) for t in j.get("tables", [])])

section("2. Yahoo 60 分 K")
import yfinance as yf

df = yf.download(["2330.TW", "6488.TWO", "2609.TW"], period="10d", interval="60m", auto_adjust=True, group_by="ticker", progress=False)
print("index tz:", df.index.tz, "rows", len(df))
print(df.tail(12).round(2).to_string())
for s in ("2330.TW", "6488.TWO"):
    sub = df[s].dropna(subset=["Close"])
    per_day = Counter(str(t.date()) for t in sub.index)
    print(s, "bars per day", dict(sorted(per_day.items())[-6:]), "times", sorted({t.strftime("%H:%M") for t in sub.index}))

section("2b. Yahoo 60 分 K 可抓多久（730d）與速度（200 檔 × 180 天）")
long = yf.download("2330.TW", period="730d", interval="60m", progress=False)
print("2330 730d rows", len(long), "first", long.index[:1].tolist())
r = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", params={"date": "20261030", "type": "ALLBUT0999", "response": "json"}, headers=UA, timeout=30).json()
table = next(t for t in r["tables"] if "證券代號" in (t.get("fields") or []))
codes = [row[0] for row in table["data"] if len(row[0]) == 4 and row[0][0] != "0" and row[0].isdigit()][:200]
t0 = time.time()
got = 0
for i in range(0, 200, 100):
    batch = [c + ".TW" for c in codes[i : i + 100]]
    d = yf.download(batch, period="180d", interval="60m", auto_adjust=True, group_by="ticker", progress=False, threads=True)
    got += sum(1 for s in batch if s in d.columns.get_level_values(0) and d[s]["Close"].notna().any())
print(f"200 檔 × 180 天 60 分 K：成功 {got}，耗時 {time.time() - t0:.1f} 秒")

section("3. Yahoo 上櫃日線 6488.TWO")
print(yf.download("6488.TWO", period="7d", interval="1d", auto_adjust=True, progress=False).round(2).to_string())
