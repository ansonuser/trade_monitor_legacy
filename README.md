# Global Tracker 交易系統

盤中量價掃描與資金流監控系統，實時監控台股市場動態。

## 快速開始（盤中監控）

### 系統需求

- **Python 3.11+** (建議 3.12)
- **Windows 作業系統**（因 tkinter 在 Windows 上表現最佳）

### 環境安裝（5分鐘快速版）

#### 1️⃣ 建立虛擬環境

在專案根目錄開啟 PowerShell 並執行：

```powershell
# 建立虛擬環境
python -m venv .venv

# 啟動虛擬環境
.\.venv\Scripts\Activate.ps1
```

如遇權限問題，執行：
```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

啟動成功後，提示字元前會出現 `(.venv)`。

#### 2️⃣ 安裝依賴

```powershell
# 升級 pip
python -m pip install --upgrade pip

# 安裝專案依賴
pip install -e .
```

**關鍵依賴說明：**
- `aiohttp` / `asyncio` - 異步 HTTP 請求（台交所資料抓取）
- `lxml` - HTML 解析（股票清單取得）
- `matplotlib` - 繪圖（盤中走勢圖）
- `tkinter` - GUI 框架（內建 Python，無需另裝）

### 快速運行

#### 🔍 啟動盤中掃描器

盤中 9:00~13:30 每 5 分鐘掃描全市場量價，估算資金流向：

```powershell
# 預設每 60 秒掃描一次
python monitor/scanner.py

# 自訂掃描間隔（秒）
python monitor/scanner.py --interval 30

# 單次掃描後退出
python monitor/scanner.py --once
```

掃描結果保存至：`logs/scanner_YYYYMMDD.log`

#### 📊 啟動盤中監控 GUI

即時展示：個股 & 族群資金流、成交量、價格變化

```powershell
# 預設每 60 秒更新一次
python monitor/gui.py

# 自訂更新間隔（秒）
python monitor/gui.py --interval 30
```

**GUI 功能：**
- 📈 即時走勢圖（資金流、成交量、價格）
- 🏪 族群排行榜（按資金流排序）
- ⏰ 市場營業時間檢查（自動暫停非營業時段）
- 🎨 深色主題介面（護眼舒適）

---

## 🚀 推薦方式 - run_local.ps1（全部本機一起跑）

**目前唯一支援的正式運行方式：Redis/InfluxDB/Grafana 跑 Docker（基礎設施），
scanner（現貨）+ taifex_fetcher（股期）+ morning_runner（早盤訊號）+ GUI 全部在本機同時啟動。**

> ⚠️ **`docker-compose.yml` 裡的 `scanner` service 已停用（註解掉）**，不要手動
> `docker-compose up -d scanner`。原因見下方「常見問題 / 踩坑紀錄」。

```
Windows 本地（run_local.ps1 一次全部啟動）
├─ monitor/scanner.py         ← 現貨即時報價（mis.twse.com.tw）
├─ monitor/taifex_fetcher.py  ← 股期即時報價（mis.taifex.com.tw）
├─ monitor/morning_runner.py  ← 早盤訊號（讀 Redis + 查遠端 PostgreSQL 歷史資料）
└─ monitor/gui.py (tkinter)   ← 即時反饋、快速繪圖

Docker（僅基礎設施）
├─ Redis      ← 消息隊列（本機 process 與 GUI 都連 127.0.0.1:6379）
├─ InfluxDB   ← 歷史快照（Grafana 用）
└─ Grafana    ← 儀表板
```

### 一鍵啟動

```powershell
.\run_local.ps1
# 或自訂間隔秒數
.\run_local.ps1 -Interval 30
```

關閉 GUI 視窗時，scanner / taifex_fetcher / morning_runner 會自動一併停止。

### 參數調整

```powershell
# 修改所有背景程式與 GUI 的更新間隔
.\run_local.ps1 -Interval 30
```

## 市場營業時間

| 時段 | 時間 |
|------|------|
| 開盤 | 9:00 |
| 收盤 | 13:30 |

**非營業時段掃描器會自動跳過**

## 掃描器工作流程

```
1. 每 N 秒執行一次（預設 60 秒）
   │
2. 檢查市場營業時間
   ├─ 未開盤/已收盤？ → 跳過
   └─ 營業中？ → 繼續
   │
3. 取得全市場股票清單
   ├─ 台灣交易所 (TSE)
   └─ 櫃檯買賣中心 (OTC)
   │
4. 並行抓取各股 5 分鐘快照（資料來源：mis.twse.com.tw）
   ├─ 成交量、成交價
   ├─ 買賣價差
   └─ 漲跌狀態
   │
5. 估算個股資金流
   └─ 資金流 = 成交量 × 價格 × 漲跌方向
   │
6. 聚合族群資金流
   └─ 按產業別彙總
   │
7. 輸出結果
   ├─ 日誌檔：logs/scanner_YYYYMMDD.log
   ├─ JSON：scanner_results.json
   └─ 控制台實時顯示
```

## 目錄結構

```
trade_sys_v1/
├── monitor/                    # 🎯 盤中監控層
│   ├── __init__.py
│   ├── scanner.py             # 台交所資料掃描 & 資金流估算
│   └── gui.py                 # tkinter 監控介面
│
├── logs/                       # 掃描日誌
├── Dockerfile                  # Docker 應用鏡像
├── docker-compose.yml          # Docker 編排（Redis + Scanner）
├── .env.example               # 環境變數範本
├── .dockerignore
├── HYBRID_SETUP.md            # 混合方案部署指南
├── pyproject.toml             # 專案設定 & 依賴
├── README.md                  # 本文件
└── scanner_results.json       # 掃描結果快取
```

## 依賴套件一覽

| 套件 | 版本 | 用途 |
|------|------|------|
| `aiohttp` | ≥5.0 | 異步 HTTP 請求 |
| `lxml` | 最新 | HTML/XML 解析 |
| `matplotlib` | 最新 | 繪圖 & 走勢圖 |
| `pandas` | 最新 | 資料處理 |
| `redis` | ≥5.0 | Redis 客戶端 |
| `psycopg` | ≥3.0 | PostgreSQL 驅動 |
| `sqlalchemy` | ≥2.0 | ORM |
| `pydantic` | ≥2.0 | 資料驗證 |
| `fastapi` | 最新 | API 框架 |
| `yfinance` | ≥0.2.0 | Yahoo Finance 資料 |

## 常見問題

### Q: tkinter 導入失敗？

**A:** tkinter 通常內建 Python，但 Windows 可能需要重新安裝：

```powershell
# 重新執行 Python 安裝程式，勾選 "tcl/tk and IDLE"
```

或檢查是否正確安裝：

```powershell
python -c "import tkinter; print('OK')"
```

### Q: "無法連接台灣交易所"？

**A:** 檢查網路連線與防火牆，掃描器需要訪問 `mis.twse.com.tw`。

### Q: GUI 字型模糊或亂碼？

**A:** 確保系統已安裝中文字型（如微軟正黑體或 Noto Sans TC），在 `monitor/gui.py` 中修改：

```python
"font.family": ["Microsoft JhengHei", "Noto Sans TC", "sans-serif"],
```

### Q: 如何與後端連結（Docker）？

**A:** 見 [DOCKER_SETUP.md](DOCKER_SETUP.md)。Monitor 層可獨立運行，也可將掃描結果推送至 Redis 供後端處理。

### Q: GUI 上的現價/權值股 K棒/股期量比資料怪怪的，忽有忽無、時對時錯？

**A:** 2026-09-07 踩坑紀錄，三個獨立問題疊在一起造成的：

1. **`docker-compose.yml` 的 `scanner` service 曾經同時跟本機 `run_local.ps1` 啟動的
   scanner 一起跑**，兩者各自獨立輪詢 `mis.twse.com.tw`、交替寫入同一個本機 Redis，
   導致抓取時間點、逾時股票不同步，資料忽好忽壞。
   **→ 現已將 `scanner` service 從 `docker-compose.yml` 停用（註解掉）**，
   本專案唯一支援的正式方式是 `run_local.ps1`，不要再手動
   `docker-compose up -d scanner`。

2. **Docker 版 `scanner` 容器沒有掛載 `./data` 目錄**，導致容器內讀不到
   `data/weights.json`（市值權重表），`load_weights()` 回傳空字典，讓每支股票的
   `index_contrib`（指數貢獻）恆為 `0`。GUI「權值股 K棒」面板依
   `abs(index_contrib)` 排序，分數 0 時被擠到最後、排出榜單，
   造成權值股（如台積電、聯發科）看起來「忽然消失又出現」。
   （即使已停用該容器，`docker-compose.yml` 裡也已補上
   `- ./data:/app/data` 掛載，供日後需要時使用。）

3. **股期均量快取（`cache:fut_vol_avg`）永遠重建成 0 支。** 真正原因是
   `monitor/morning_signal.py` 的查詢邏輯與資料庫實際格式不一致：
   舊邏輯試圖把 TAIFEX 合約代號（`symbol_id`，如 `DQFE6-F`）反推成
   Capital 商品代號（如 `DQF00`）去查 `fut_1min_table`，但寫入端
   （`tw_crawl` 專案的 `sync_1min_capital.py`）早已用
   `capital_code_to_stock_id()` 把代號轉換成**真實股票代號**（如 `2330`）
   才存入資料庫，兩邊代碼系統完全對不上，查詢自然找不到資料。
   **→ 已修正 `_query_fut_vol_avg_full()` / `_calc_fut_vol_ratio()`**，
   直接用真實股票代號（`spot_id`）查詢，與 `taifex_fetcher.py` 寫入
   Redis 的 `spot_id` 格式一致。

**資料流向備忘**（避免日後又搞混本機/遠端）：
- 現貨即時報價：`scanner.py` → 打 `mis.twse.com.tw` API →（不經資料庫）
- 股期即時報價：`taifex_fetcher.py` → 打 `mis.taifex.com.tw` API →（不經資料庫）
- 股期歷史均量：`morning_signal.py` → 查**遠端** PostgreSQL（`fut_1min_table`）
- 市值權重表：`scanner.py` `load_weights()` → 讀**本機**靜態檔 `data/weights.json`
- 現貨歷史K棒/RS/法人資料：`morning_signal.py` / `rs_monitor.py` → 查**遠端** PostgreSQL / MySQL

### Q: `run_local.ps1` 跑著，但現貨報價（GUI 上的現價、權值股 K棒）完全沒在動？

**A:** 2026-10-12 踩坑紀錄：

**症狀**：`taifex_fetcher.py`、`morning_runner.py`、`gui.py` 都還活著，唯獨
`scanner.py`（負責抓現貨、寫 `tracker:stocks` 的程式）**自己無聲消失**，
`logs/scanner_YYYYMMDD.log` 裡最後一輪成功紀錄之後完全沒有任何新內容，
也沒有任何錯誤或 Traceback。

**排查時的兩個岔路**（記錄下來避免之後又被誤導）：
1. 一度以為 `.venv` 跟 `pyenv-win` 各自多開了一份重複 process 是衝突來源——
   **這是誤判**。`.venv\Scripts\python.exe` 本身只是個轉接 stub，
   `pyvenv.cfg` 裡的 `home` 指到 `pyenv-win` 的實際解譯器，執行時一定會
   衍生出對應的子 process（`ParentProcessId` 可證實），這是 venv 的標準
   機制，不是重複啟動。
2. 一度以為是早盤成交量大造成 API 變慢——**也不是**。10/12 當天盤面量
   其實不大，耗時變慢跟量能無關。

**真正原因**：比對 5 月到 10 月的 `logs/scanner_*.log`，`poll_all()`
（全市場批次輪詢）的耗時有明顯的數個月漸進惡化趨勢：
5月 37-39s → 8月 41-43s → 9月 42-48s → 10月初 50-60s+，10/5 甚至平均
58.2s、單次最高 223.6s。用 `curl -w` 量測 DNS/Connect/TLS 都是毫秒級，
瓶頸集中在 **TTFB**（伺服器開始回應第一個 byte 的時間，實測 0.3-4s）。
結論：**`mis.twse.com.tw` 伺服器端處理請求本身變慢了**，是外部因素，
非本機網路、非 `curl_cffi` 封裝層的問題。

單輪耗時逼近/超過舊有 `POLL_TIMEOUT=60s`，導致 `asyncio.wait_for` 頻繁
強制取消尚未完成的 `poll_all()`；`curl_cffi` 底層的 HTTP 連線在被取消時
未必乾淨釋放，連續取消 20+ 次後資源耗盡，最終讓整個 scanner process
無聲崩潰（`taifex_fetcher` 等其他獨立 process 不受影響，因為瓶頸只在
`scanner.py` 這支抓全市場現貨的程式）。

**修正**（[monitor/scanner.py](monitor/scanner.py)）：
1. `poll_all()` 從「序列模式：每 1.667s 發 1 個請求」改為
   **併發批次模式**：每組併發 `REQ_CONCURRENCY=3` 個請求，組間補足等待到
   `REQ_WINDOW=5.0` 秒整，同樣符合 TWSE「5秒內最多3個請求」的限流規則，
   但讓多批的伺服器等待時間重疊，實測全市場總耗時從常態 65-100+ 秒
   降到穩定的 31-37 秒。
2. `fetch_batch()` 加上 `BATCH_RETRIES=2` 重試機制，單批逾時/失敗自動
   重試，避免偶發網路波動造成資料遺漏（三次測試皆完整抓到全部
   ~1982-1990 支，無遺漏）。
3. `POLL_TIMEOUT` 由 60s 提高到 100s，作為新模式下的安全邊際。

**尚未驗證**：目前只做過 3 次 `--once` 短時間測試，都在同一時段內
連續執行。**尚未驗證長時間連續運行**（例如連續跑 1-2 小時，涵蓋開盤
到收盤不同時段的伺服器負載差異）下是否仍然穩定、連線池是否會隨時間
累積異常。實際掛著跑一段時間後，建議回頭確認
`logs/scanner_YYYYMMDD.log` 裡耗時是否維持在 30-40 秒區間、有無再度
出現逾時或無聲消失的情況。

## 進階：完整環境（含 Backend）

### 啟動 Docker 基礎設施

```powershell
docker-compose up -d
```

詳見 [DOCKER_SETUP.md](DOCKER_SETUP.md)

### 系統架構

```
┌─────────────────────────────────────────────────────────────┐
│                   Monitor 盤中監控層                        │
│                   (Windows 本地)                            │
│                                                             │
│  GUI (tkinter)              Scanner (aiohttp)              │
│  ├─ 實時走勢圖               ├─ 台交所 API 輪詢           │
│  ├─ 族群排行                 ├─ 量價快照收集              │
│  └─ 資金流展示               └─ 資金流估算                │
│           │                        │                       │
│           └────────────┬───────────┘                       │
│                        │                                   │
│              logs/scanner_*.log                           │
│              scanner_results.json                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                           │
                           │ (可選)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              Backend 核心策略層 (Docker)                    │
│                                                             │
│  Strategy Brain (src/tracker/)                             │
│  ├─ 訂閱 Redis 行情                                       │
│  ├─ 執行策略運算                                          │
│  └─ 落庫 PostgreSQL                                      │
│           │              │                                │
│           ▼              ▼                                │
│         Redis        PostgreSQL                           │
│         (消息隊列)     (歷史資料)                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 開發

### 執行測試

```powershell
pip install -e ".[dev]"
pytest
```

### 代碼檢查

```powershell
ruff check .
mypy src/
```

## 授權

MIT License
