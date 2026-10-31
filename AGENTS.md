# AGENTS.md

給 Claude Code／Codex 的維護規則。

## 規則

- 規則來源：`SPEC.md` 第 2 節；修改條件前先更新規格書，並在第 14 節記錄修訂
- 程式邏輯（流程、資料處理、判斷細節）改動時，同步更新 `LOGIC.md`
- 條件只寫在 `screener/rules.py`，參數只放 `config.yaml`，不可寫死在程式中
- 歷史價格一律使用還原權值；量能一律以「張」比較
- 任何改動都要通過 `pytest`；新增條件必須附通過／不通過測試案例
- 不得在程式或 commit 中放入 API token；需要時使用 GitHub Secrets

## 常用指令

| 用途 | 指令 |
| --- | --- |
| 測試 | `pytest -q` |
| 模擬資料（不連網，約 2 分鐘，覆寫 `docs/data/`） | `python -m screener.main --demo` |
| 正式執行 | `python -m screener.main [--date YYYY-MM-DD]` |
| 預覽儀表板 | `python -m http.server -d docs`，開 http://localhost:8000 |
| 參數試算（不寫入 `docs/data/`） | `python -m screener.whatif --date YYYY-MM-DD --set "key.path=值"` |

## 模組分工

| 檔案 | 職責 | 修改時注意 |
| --- | --- | --- |
| `universe.py` | 證交所每日收盤行情（MI_INDEX）、股票池過濾、資料日期檢查 | `stat` 非 OK 或日期不符丟 `DataNotReady`（結束碼 2）；不要改回 OpenAPI `STOCK_DAY_ALL`（隔日才更新） |
| `prices.py` | yfinance 還原權值日線、當日 K 棒補值、落後判斷 | 換資料源只改這裡，維持 `dict[code, DataFrame(Open/High/Low/Close/Volume)]`；Yahoo 當晚常缺當日開高低收 |
| `indicators.py` | EMA、MACD、週月線轉換 | `resample` 必須與 pandas `W-FRI`／`ME` 一致（`test_matches_pandas_resample`） |
| `rules.py` | 8 個條件、開關驗證、條件說明 | 條件說明經 `conditions_meta` 輸出給儀表板，前端不重複定義 |
| `report.py` | latest／history JSON、job summary | `demo` 旗標隔離模擬與正式資料 |
| `demo.py` | 模擬資料 | 只供 `--demo` 與測試使用 |
| `whatif.py` | 參數試算 | 只比較、不發布；調整只影響選股計算，股票池與抓價範圍沿用原設定 |
| `main.py` | CLI、流程、結束碼 | 0 成功、2 不發布、1 錯誤 |

## 環境

- GitHub Actions 固定 Python 3.12；本機用 3.12 以上皆可
- 結果由 workflow commit 回 `docs/data/`；手動修改該資料夾會在下次排程被覆寫
