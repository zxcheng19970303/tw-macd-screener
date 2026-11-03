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
| `universe.py` | 證交所 MI_INDEX、櫃買中心收盤行情、股票池過濾、資料日期檢查 | 無資料或日期不符丟 `DataNotReady`（結束碼 2）；櫃買中心非交易日回 ok 但表格為空；不要改用 OpenAPI（證交所實測隔日才更新） |
| `prices.py` | yfinance 日線與 60 分 K、上市 `.TW`／上櫃 `.TWO`、當日 K 棒補值、落後判斷 | 換資料源只改這裡，維持 `dict[code, DataFrame(Open/High/Low/Close/Volume)]`；Yahoo 當晚常缺當日日 K 開高低收；60 分 K 09:00 那根量為 0，不可刪 |
| `indicators.py` | EMA、MACD、均線、週月線轉換、各週期 K 棒 | `resample` 必須與 pandas `W-FRI`／`ME` 一致（`test_matches_pandas_resample`）；`build_frames` 回傳 {週期: K 棒} |
| `rules.py` | 8 個條件、觀察清單、組別與開關驗證、條件說明 | 條件代號 M／W／D 代表上級／本級／下級；條件說明經 `conditions_meta` 輸出給儀表板，前端不重複定義 |
| `report.py` | latest／history JSON（v2，依名單分開）、job summary | `demo` 旗標隔離模擬與正式資料；v1 歷史自動歸到日／週／月那一組 |
| `demo.py` | 模擬資料 | 只供 `--demo` 與測試使用 |
| `whatif.py` | 參數試算 | 只比較、不發布；調整只影響選股計算，股票池與抓價範圍沿用原設定 |
| `main.py` | CLI、流程、結束碼 | 0 成功、2 不發布（暫時性：資料未就緒）、1 錯誤（會寄信）；補跑舊日期不可覆寫 `latest.json` |

## 環境

- GitHub Actions 固定 Python 3.12；本機用 3.12 以上皆可
- 結果由 workflow commit 回 `docs/data/`；手動修改該資料夾會在下次排程被覆寫
- 主要由 cron-job.org 呼叫 `workflow_dispatch`（`scheduled=true`）觸發；GitHub 排程只是備援，時間不要設在整點或台北傍晚尖峰
- `daily.yml` 的 `scheduled` 輸入與 `--auto-date` 是外部排程的介面，改名或移除前要同步更新 cron-job.org 設定
