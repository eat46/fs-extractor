"""抽取結果的 Structured Outputs 契約（只抽『原始申報數字』）。

用 JSON Schema 硬性約束輸出，比在 Prompt 裡口頭要求可靠。
設計原則（依使用者指示）：
  - LLM 只負責抽「合併綜合損益表上『印出來的原始數字』」——營收、成本、毛利、
    營業費用、營業利益、淨利、EPS。
  - 毛利率／營益率／淨利率等『需計算的比率』一律不讓 LLM 產生，改由後端
    validation 層以原始金額計算（見 services/validation.py），避免模型自行四捨五入。

本專案為「單一公司季報」：一份 PDF ＝ 一組原始財報數據。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class FinancialStatement(BaseModel):
    """單一公司單一季別，合併綜合損益表上的原始申報數字。"""

    stock_code: str | None = Field(None, description="股票代號，台股為四位數字如 3008、2330")
    company_name: str | None = Field(None, description="公司名稱，如 大立光電、台積電")
    period: str | None = Field(
        None, description="報告期別，民國年格式『YYY年QN』，例：115年Q1"
    )

    # --- 金額類（原始申報，單位通常為新台幣千元）---
    # 皆為『損益表上直接印出的數字』，去逗號的純數字；括號代表負數。
    revenue: float | None = Field(None, description="營業收入（合計）")
    cost_of_revenue: float | None = Field(None, description="營業成本（合計）；有印才填，否則 null")
    gross_profit: float | None = Field(
        None, description="營業毛利（毛損）淨額；報表印出的那一行，不要自行以營收減成本推算"
    )
    operating_expenses: float | None = Field(
        None, description="營業費用（合計）；有印才填，否則 null"
    )
    operating_income: float | None = Field(None, description="營業利益（損失）；報表印出的那一行")
    net_income: float | None = Field(
        None, description="本期淨利（淨損）；若有『歸屬於母公司業主』則取該行"
    )

    # --- 每股盈餘（單位為新台幣元，與上面金額不同）---
    eps_basic: float | None = Field(None, description="基本每股盈餘（新台幣元）")
    eps_diluted: float | None = Field(None, description="稀釋每股盈餘（新台幣元）")

    # --- 單位標註（提醒模型金額與 EPS 單位不同）---
    amount_unit: str = Field(
        "新台幣千元", description="金額類欄位使用的單位（依報表表頭，通常為新台幣千元）"
    )
    eps_unit: str = Field("新台幣元", description="EPS 欄位使用的單位")

    # --- 複檢輔助 ---
    source_page: int | None = Field(
        None, description="合併綜合損益表所在的 PDF 頁碼，供人工並排複檢"
    )
    confidence: float | None = Field(None, ge=0, le=1, description="整體抽取信心 0~1")
    notes: str | None = Field(None, description="任何不確定或衝突的說明；正常抽取可留 null")


def extraction_json_schema() -> dict:
    """回傳給 LLM provider 當 response schema 的 JSON Schema。"""
    return FinancialStatement.model_json_schema()
