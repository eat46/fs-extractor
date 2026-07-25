# fs-extractor · 台股季報財務數據萃取（MVP 核心）

從台灣上市公司**季報 PDF**（合併綜合損益表）抽取本期關鍵財務指標，輸出結構化 JSON / CSV。
**不含資料庫、佇列、雲端儲存、複檢 UI**——單檔同步抽取，聚焦「PDF → LLM → 結構化輸出」這條主線。

> **建議來源**：[公開資訊觀測站](https://mops.twse.com.tw/mops/web/index) → 財務報告書。
>
> **目前範圍**：僅抽取**綜合損益表**；**資產負債表**與**現金流量表**即將推出。

## 設計原則：先抽原始數字，再驗證與衍生

LLM **只抽合併綜合損益表上印出來的原始數字**；需要計算的比率（毛利率／營益率／
淨利率）不讓模型產生，改由後端 [validation 層](app/services/validation.py) 以原始金額計算，
並用損益表的內部關係交叉驗證，避免模型自行四捨五入或臆造。

### 原始欄位（LLM 抽取）

| 欄位 | 說明 | 單位 |
|---|---|---|
| `stock_code` / `company_name` / `period` | 股票代號 / 公司 / 期別（YYY年QN） | — |
| `revenue` / `cost_of_revenue` | 營業收入 / 營業成本 | 新台幣千元 |
| `gross_profit` / `operating_expenses` / `operating_income` | 營業毛利 / 營業費用 / 營業利益 | 新台幣千元 |
| `net_income` | 本期淨利（歸屬母公司業主） | 新台幣千元 |
| `eps_basic` / `eps_diluted` | 基本 / 稀釋每股盈餘 | 新台幣元 |

找不到的欄位一律填 `null`，不臆造。另附 `source_page` / `confidence` / `notes` 供人工複檢。

### 衍生欄位（後端計算）＋ 驗證

| 衍生 | 公式 |
|---|---|
| `gross_margin` | 毛利 ÷ 營收 ×100 |
| `operating_margin` | 營業利益 ÷ 營收 ×100 |
| `net_margin` | 淨利 ÷ 營收 ×100 |

驗證檢查（`error` 擋件、`warning` 提示人工複檢）：營收存在且為正、毛利 ≤ 營收、
營業利益 ≤ 毛利、`毛利 ≈ 營收−成本`、`營業利益 ≈ 毛利−營業費用`（含容忍度）、
比率落在合理範圍、稀釋 EPS ≤ 基本 EPS。輸出含 `validation.ok` 與 `validation.issues`。

## 架構

```
app/
  config.py                 # pydantic-settings，所有行為讀 .env
  prompts/system_prompt.py  # 抽取指令 + build_user_context（檔名提示）
  schemas/extraction.py     # Pydantic → JSON Schema 結構化輸出契約（只含原始欄位）
  services/
    llm/base.py             # Provider 抽象層（換模型只改 .env）
    llm/gemini_provider.py  # Gemini（預設，response schema）
    llm/claude_provider.py  # Claude（替代，document block + 強制 tool use）
    validation.py           # 衍生比率 + 內部一致性驗證（error/warning）
    extraction_service.py   # 主流程：PDF → LLM → 驗證 → ExtractionOutcome
    export.py               # JSON / CSV 輸出（原始 + 衍生 + 驗證狀態）
tests/test_validation.py    # validation 層單元測試（純邏輯，不呼叫 LLM）
  main.py                   # FastAPI：上傳頁 + /api/extract
  static/index.html         # 拖曳上傳的最小前端
scripts/extract_cli.py      # CLI：單檔抽取
```

## 安裝與設定

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 填入 API_KEY
```

`.env` 關鍵設定：

```
LLM_PROVIDER=gemini          # gemini | claude
GEMINI_API_KEY=
CLAUDE_MODEL=gemini-3.1-flash-lite
```

換模型／換 provider 只改 `.env`，不動程式碼。

## 使用

### CLI

```bash
python -m scripts.extract_cli path/to/your_report.pdf --json out.json --csv out.csv
```

### Web

```bash
uvicorn app.main:app --reload      # http://localhost:8000/
```

拖曳一份季報 PDF（來源：[公開資訊觀測站](https://mops.twse.com.tw/mops/web/index) → 財務報告書）
→ 抽取 → 左側並排檢視原始 PDF、右側檢視結果、下載 JSON / CSV。

- `POST /api/extract` — 回傳 `{statement, meta, csv}`
- `POST /api/extract.csv` — 直接回傳 CSV
- `GET /health` — 顯示目前 provider

![Upload interface for the financial statement extractor](docs/fs-extractor-upload.png)


![Extraction result showing raw figures alongside computed ratios and validation status](docs/fs-extractor-result.png)

## 後續規劃

- **資產負債表**、**現金流量表**抽取（目前僅綜合損益表）。
- 批次處理與佇列、雲端儲存、每日排程、人工複檢介面、資料庫持久化。
