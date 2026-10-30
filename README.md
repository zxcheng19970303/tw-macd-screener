# 台股 MACD 三週期選股

每個交易日盤後，自動篩選符合「日線 2 走 3 修正波」月、週、日三週期 MACD 共振條件的上市普通股，並發布到可用手機瀏覽的儀表板。運算在 GitHub Actions、儀表板在 GitHub Pages，零成本、無伺服器。

規格與規則定義見 [`SPEC.md`](SPEC.md)；維護規則見 [`AGENTS.md`](AGENTS.md)。

- 儀表板：<https://zxcheng19970303.github.io/tw-macd-screener/>（啟用 Pages 後生效）
- 執行紀錄：<https://github.com/zxcheng19970303/tw-macd-screener/actions>

## 目前狀態

- 2026-10-30：T1–T8 完成。`pytest` 64 項通過；`--demo` 產出 JSON，儀表板手機（375px）與桌機、亮色與深色模式檢查完成。
- 2026-10-30：從 `alvin-ai-workspace` 的 `400_Project/` 搬到本 repo，此後以本 repo 為正本。
- `docs/data/` 目前是 **demo 模擬資料**（儀表板會顯示「示範資料」橫幅），第一次正式排程跑完後自動取代。
- 下一步：啟用 Pages、第一次手動執行 workflow，完成 M3 上線驗收（見下方清單）。

## 選股條件

8 個條件全部成立（AND）才入選。羅 = MACD 10/20/10，蜜 = MACD 26/52/13，D-M = DIF − MACD。

| ID | 週期 | 條件 |
| --- | --- | --- |
| M1 | 月線 | 羅 D-M > 0（紅柱） |
| W1 | 週線 | 羅 DIF > 0 |
| W2 | 週線 | 羅 D-M > 0 |
| D1 | 日線 | 羅 MACD > 0 |
| D2 | 日線 | 羅 前一日綠柱，今日縮短或翻紅 |
| D3 | 日線 | 蜜 MACD > 0 |
| D4 | 日線 | 蜜 D-M > 0，或綠柱縮短 |
| V1 | 日線 | 當日成交量 > 300 張 |

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
| `--date YYYY-MM-DD` | 預期交易日，預設為台北時間今天。證交所只提供最新交易日，因此只能用於「隔天補跑前一日」，不能重跑任意過去日期 |
| `--skip-if-current` | 當日已有正式結果就直接結束（補跑排程用） |
| `--config`、`--out` | 改用其他設定檔或輸出資料夾 |

結束碼：`0` 成功、`2` 非交易日或資料未更新（不發布）、`1` 錯誤（不發布，GitHub 會寄信通知）。

## 部署到 GitHub

本 repo 為 public（Pages 免費；程式碼與每日名單任何人都看得到）。程式已在根目錄，`.github/workflows/` 會自動生效。

1. Settings → Pages → Build and deployment → Source 選「Deploy from a branch」，Branch 選 `main`、資料夾 `/docs`
2. Settings → Actions → General → Workflow permissions 設為「Read and write permissions」（workflow 已宣告 `contents: write`，這步是保險）
3. Actions → daily-screener → Run workflow 手動執行一次。非交易日或 18:00 前執行會得到「不發布」，屬正常；週末想測試可在 `date` 填上一個交易日（如週五）
4. 開啟儀表板網址，確認橫幅不再顯示「示範資料」

排程：平日台北 18:00 執行，20:00 補跑（18:00 已成功時自動略過）。GitHub 的排程常延遲 5–30 分鐘。

### 疑難排解

| 狀況 | 處理 |
| --- | --- |
| 18:00 結果都是「資料未更新」 | 證交所當天較晚更新，20:00 補跑會接手；也可以把主排程往後移 |
| 抓價失敗比例超過 20% | 多半是 Yahoo 暫時限流，隔一段時間手動重跑；持續發生時評估改用 FinMind（只需改 `prices.py`） |
| Pages 沒有更新 | 確認 Actions 有產生 `data: YYYY-MM-DD screener results` commit，再看 Actions 頁的 pages-build-deployment 是否成功 |
| 證交所 API 出現 SSL 憑證錯誤 | 維持 Python 3.12；較新的 Python 對憑證檢查較嚴格 |

## M3 上線驗收

本機無法連到證交所與 Yahoo，以下項目需部署後驗證：

- [ ] 2330、2317、2454 的日線羅 DIF、MACD 與看盤軟體同日數值誤差 < 1%（`latest.json` 的 `values` 只列入選與差 1 條件的股票；其他個股可在本機用 `indicators.build_frames` 計算）
- [ ] 抽查 5 檔入選股，人工對照 K 線確認 8 個條件皆成立
- [ ] GitHub Actions 手動觸發成功，總執行時間 < 20 分鐘
- [ ] 連續 5 個交易日自動更新成功
- [ ] 週末或假日執行時不產生 commit
- [ ] 手機實機開啟儀表板（寬 375px）無水平捲動

## 檔案

| 路徑 | 用途 |
| --- | --- |
| `config.yaml` | 參數與開關 |
| `screener/` | 程式（各模組職責見 `AGENTS.md`） |
| `tests/` | pytest 單元與流程測試 |
| `docs/index.html` | 儀表板（原生 JavaScript，無外部字型與追蹤） |
| `docs/data/` | `latest.json`、`history.json`（由 workflow 自動更新） |
| `.github/workflows/daily.yml` | 每日選股排程 |
| `.github/workflows/tests.yml` | push／PR 時執行 pytest |

## 免責聲明

名單依技術條件自動產生，規則尚未經過回測，僅供研究參考，不構成投資建議。
