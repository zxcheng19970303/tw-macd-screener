# 台股 MACD 三週期選股

每個交易日盤後，自動篩選符合「2 走 3 修正波」三週期 MACD 共振條件的上市＋上櫃普通股（兩組週期），另產出一份「剛站上 10 日線」觀察清單，並發布到可用手機瀏覽的儀表板。運算在 GitHub Actions、儀表板在 GitHub Pages，零成本、無伺服器。

規格與規則定義見 [`SPEC.md`](SPEC.md)；程式實際怎麼運作見 [`LOGIC.md`](LOGIC.md)；維護規則見 [`AGENTS.md`](AGENTS.md)。

- 儀表板：<https://zxcheng19970303.github.io/tw-macd-screener/>
- 執行紀錄：<https://github.com/zxcheng19970303/tw-macd-screener/actions>

## 目前狀態

- 2026-10-30：T1–T8 完成。`pytest` 64 項通過；`--demo` 產出 JSON，儀表板手機（375px）與桌機、亮色與深色模式檢查完成。
- 2026-10-30：從 `alvin-ai-workspace` 的 `400_Project/` 搬到本 repo，此後以本 repo 為正本。
- 2026-10-31：v1.0.1 資料源修正。首次部署實測發現證交所 OpenAPI 隔日才更新、Yahoo 當晚缺當日 K 棒，改用證交所 MI_INDEX 並以官方行情補當日 K 棒（詳見 `SPEC.md` 第 14 節）。
- 2026-10-31：啟用 GitHub Pages；手動執行 2026-10-30 正式資料成功發布（股票池 1,084、入選 6），bot 的資料 commit 會自動觸發 Pages 重新部署。
- 2026-10-31：儀表板改版為暗色毛玻璃風格，差 1 條件加入搜尋與分頁（詳見 `SPEC.md` 第 14 節）。
- 2026-10-31：v1.1。股票池加入上櫃；同一套條件套用兩組週期（第 1 組 60 分／日／週、第 2 組 日／週／月）；新增獨立的觀察清單（詳見 `SPEC.md` 第 14 節、`LOGIC.md`）。
- 2026-11-03：v1.1.1。11/2 兩次整點排程沒有觸發，排程改為避開整點並加開補跑；修正 7 項資料防護問題（詳見 `SPEC.md` 第 14 節）。
- 下一步：完成 M3 上線驗收（見下方清單），觀察連續 5 個交易日自動更新。

## 選股條件

8 個條件全部成立（AND）才入選。羅 = MACD 10/20/10，蜜 = MACD 26/52/13，D-M = DIF − MACD。條件代號 M／W／D 代表上級／本級／下級週期，兩組套用同一套條件：

| ID | 條件 | 第 1 組 | 第 2 組 |
| --- | --- | --- | --- |
| M1 | 上級 羅 D-M > 0（紅柱） | 週線 | 月線 |
| W1 | 本級 羅 DIF > 0 | 日線 | 週線 |
| W2 | 本級 羅 D-M > 0 | 日線 | 週線 |
| D1 | 下級 羅 MACD > 0 | 60 分線 | 日線 |
| D2 | 下級 羅 前一根綠柱，這根縮短或翻紅 | 60 分線 | 日線 |
| D3 | 下級 蜜 MACD > 0 | 60 分線 | 日線 |
| D4 | 下級 蜜 D-M > 0，或綠柱縮短 | 60 分線 | 日線 |
| V1 | 當日成交量 > 300 張 | 日成交量 | 日成交量 |

**觀察清單**（獨立於上面 8 個條件，全股票池）：A1 日線羅 MACD > 0（不看 DIF）、A2 今天剛站上 10 日線（昨收 ≤ 10 日均線、今收 > 10 日均線），且 A3 當日成交量 > 200 張。

開關（`config.yaml` 的 `switches`）：`price_source`（close／hlcc4）、`htf_bar_mode`（completed／current）、`volume_rule`（day_lots／avg5_lots／avg5_value）、`d2_mode`（shrink_1d／shrink_2d）。

## 本機使用

```bash
pip install -r requirements.txt
```

```bash
pytest -q
```

```bash
python -m screener.main --demo
```

```bash
python -m http.server -d docs
```

開啟 http://localhost:8000 預覽儀表板。直接雙擊 `index.html` 會因瀏覽器限制讀不到 JSON。

| 參數 | 用途 |
| --- | --- |
| `--demo` | 模擬資料，不連網；逐日回補 60 個交易日（約 2 分鐘） |
| `--date YYYY-MM-DD` | 預期交易日，預設為台北時間今天；可重跑過去日期（連續在榜天數依 `history.json` 既有紀錄計算） |
| `--skip-if-current` | 當日已有正式結果就直接結束（補跑排程用） |
| `--config`、`--out` | 改用其他設定檔或輸出資料夾 |

參數試算（不寫入 `docs/data/`）：`python -m screener.whatif --date 2026-10-30 --set "macd.mi={fast: 10, slow: 20, signal: 10}"`。`--set` 可重複或用分號分隔；GitHub 上可用 Actions → whatif → Run workflow。

結束碼：`0` 成功、`2` 非交易日或資料未更新（不發布）、`1` 錯誤（不發布，GitHub 會寄信通知）。

## 部署到 GitHub

本 repo 為 public（Pages 免費；程式碼與每日名單任何人都看得到）。程式已在根目錄，`.github/workflows/` 會自動生效。

1. Settings → Pages → Build and deployment → Source 選「Deploy from a branch」，Branch 選 `main`、資料夾 `/docs`
2. Settings → Actions → General → Workflow permissions 設為「Read and write permissions」（workflow 已宣告 `contents: write`，這步是保險）
3. Actions → daily-screener → Run workflow 手動執行一次。非交易日或 18:00 前執行會得到「不發布」，屬正常；週末想測試可在 `date` 填上一個交易日（如週五）
4. 開啟儀表板網址，確認橫幅不再顯示「示範資料」

## 自動執行

| 觸發方式 | 時間（台北） | 角色 |
| --- | --- | --- |
| cron-job.org 呼叫 GitHub API | 平日 18:17、19:17、21:17 | **主要**：準時到分鐘 |
| GitHub 內建排程 | 平日 18:17、23:37，隔天 02:13、05:41、07:23 | 備援：GitHub 排程常延後數小時或被丟棄（11/2、11/3 傍晚全部沒觸發） |

兩種觸發都會自動判斷日期（台北平日 17:00 以後處理當天，其他時間處理前一個平日），且當日已有完整結果時直接略過，所以多次觸發不會重複發布。

### 設定 cron-job.org（一次性，約 10 分鐘）

**1. 建立 GitHub 權杖（只能觸發這個 repo 的 workflow）**

GitHub 右上角頭像 → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token：

| 欄位 | 填寫 |
| --- | --- |
| Token name | `tw-macd-screener cron` |
| Expiration | 最長可選的期限（到期前要換新，建議在行事曆設提醒） |
| Repository access | Only select repositories → `tw-macd-screener` |
| Permissions → Repository permissions | **Actions：Read and write**（其他維持 No access；Metadata 會自動設為 Read-only） |

產生後複製權杖（只會顯示一次）。權杖只貼在 cron-job.org，不要放進 repo 或對話裡。

**2. 在 cron-job.org 建立排程**

註冊 <https://cron-job.org>（免費）→ Create cronjob：

| 欄位 | 填寫 |
| --- | --- |
| Title | `tw-macd-screener daily` |
| URL | `https://api.github.com/repos/zxcheng19970303/tw-macd-screener/actions/workflows/daily.yml/dispatches` |
| Execution schedule | Custom：Days of week 選一～五；Hours 選 18、19、21；Minutes 只選 17（＝18:17、19:17、21:17）；時區選 `Asia/Taipei` |
| Advanced → Request method | `POST` |
| Advanced → Headers | `Accept: application/vnd.github+json`<br>`Authorization: Bearer <你的權杖>`<br>`X-GitHub-Api-Version: 2022-11-28`<br>`Content-Type: application/json` |
| Advanced → Request body | `{"ref":"main","inputs":{"scheduled":"true"}}` |

存檔後按 **Test run**：回應 `204` 即成功，GitHub Actions 頁會出現一筆 daily-screener 執行（非交易時段會顯示「本次不發布」，屬正常）。回應 `401`／`403` 多半是權杖貼錯或權限沒給 Actions 寫入。

### 疑難排解

| 狀況 | 處理 |
| --- | --- |
| 某次執行顯示「本次不發布」 | 非交易日、交易所或 Yahoo 尚未更新完整時屬正常，後面的補跑排程會接手；連假或颱風假也會如此 |
| 晚上沒有更新 | 到 cron-job.org 看執行紀錄是否回 204、Actions 頁是否有對應執行；可手動 Run workflow（填日期）補跑。權杖過期時 cron-job.org 會回 401，需換新權杖 |
| 抓價失敗比例超過 20% | 多半是 Yahoo 暫時限流，隔一段時間手動重跑；持續發生時評估改用 FinMind（只需改 `prices.py`） |
| 儀表板第 1 組顯示「本組未更新」 | 當晚 Yahoo 的 60 分 K 抓取失敗超過 20%，第 2 組與觀察清單仍照常發布；可隔一段時間手動重跑當天 |
| Pages 沒有更新 | 確認 Actions 有產生 `data: YYYY-MM-DD screener results` commit，再看 Actions 頁的 pages-build-deployment 是否成功 |
| 證交所連線出現 SSL 憑證錯誤 | 維持 Python 3.12；較新的 Python 對憑證檢查較嚴格 |

## M3 上線驗收

數值比對與抽查可在 GitHub 上完成，不需本機 Python：Actions → compare → Run workflow，輸入代號（以空白分隔）與日期，結果在該次執行的 Summary。本機則是 `python -m screener.compare 2330 2317 2454 --date YYYY-MM-DD`。

- [ ] 2330、2317、2454 的日線羅 DIF、MACD 與看盤軟體同日數值誤差 < 1%
- [ ] 抽查 5 檔入選股，人工對照 K 線確認 8 個條件皆成立
- [x] GitHub Actions 手動觸發成功，總執行時間 < 20 分鐘（2026-10-30：約 2 分鐘）
- [ ] 連續 5 個交易日自動更新成功
- [ ] 週末或假日執行時不產生 commit
- [ ] 手機實機開啟儀表板（寬 375px）無水平捲動

## 檔案

| 路徑 | 用途 |
| --- | --- |
| `SPEC.md` | 開發規格書（規則正本與修訂紀錄） |
| `LOGIC.md` | 程式邏輯說明（流程、公式、條件判斷與範例） |
| `config.yaml` | 參數與開關 |
| `screener/` | 程式（各模組職責見 `AGENTS.md`） |
| `tests/` | pytest 單元與流程測試 |
| `docs/index.html` | 儀表板（暗色毛玻璃風格，原生 JavaScript，無外部字型與追蹤） |
| `docs/data/` | `latest.json`、`history.json`（由 workflow 自動更新） |
| `.github/workflows/daily.yml` | 每日選股排程 |
| `.github/workflows/tests.yml` | push／PR 時執行 pytest |
| `.github/workflows/compare.yml` | 手動比對指定個股的三週期數值與條件 |
| `.github/workflows/whatif.yml` | 參數試算：同一天資料比較原設定與調整後的選股結果，不更新儀表板 |

## 免責聲明

名單依技術條件自動產生，規則尚未經過回測，僅供研究參考，不構成投資建議。
