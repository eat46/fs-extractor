"""驗證與衍生層：對『原始申報數字』做內部一致性檢查，並計算需要衍生的比率。

依使用者指示：LLM 只抽原始數字，這裡負責
  1) derive()   —— 由原始金額計算毛利率／營益率／淨利率（不讓模型自行四捨五入）。
  2) validate() —— 用損益表的內部關係交叉驗證抽取結果，標出可疑處供人工複檢。

所有金額同單位（通常新台幣千元），故比率＝子項 ÷ 營收 ×100。

一致性檢查採「兩層容忍度」（皆相對營收，預設值在 config，可用 .env 調）：
  - 毛利＝營收−成本：純恆等式，理論上精確相等 → 收很嚴（預設 0.1%）。超過多半是
    抽取錯誤（抓錯期別、抓到附註而非主表），或該公司有未實現/已實現銷貨損益調整
    （我們抽的是「營業毛利淨額」，位於銷貨損益調整之後）。
  - 營業利益＝毛利−營業費用：中間可能有其他收益及費損等科目，且 IFRS 18（台灣 117
    會計年度接軌）會讓營業利益分類更細 → 留餘裕（預設 2%）。
偏差一律視為 warning（提示人工複檢）而非硬性 error。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.schemas.extraction import FinancialStatement

_MARGIN_MIN, _MARGIN_MAX = -100.0, 100.0


@dataclass
class DerivedMetrics:
    """由原始金額計算出的比率（%），非 LLM 產生。"""

    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None


@dataclass
class ValidationIssue:
    level: str  # "error" | "warning"
    field: str
    message: str
    detail: dict | None = None  # 機器可讀補充（如一致性檢查的 expected/actual/gap）


@dataclass
class ValidationReport:
    derived: DerivedMetrics
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """無 error 級問題即視為通過（warning 僅提示人工複檢）。"""
        return not any(i.level == "error" for i in self.issues)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "derived": {
                "gross_margin": self.derived.gross_margin,
                "operating_margin": self.derived.operating_margin,
                "net_margin": self.derived.net_margin,
            },
            "issues": [
                {"level": i.level, "field": i.field, "message": i.message, "detail": i.detail}
                for i in self.issues
            ],
        }


def _ratio(numerator: float | None, revenue: float | None) -> float | None:
    if numerator is None or not revenue:
        return None
    return round(numerator / revenue * 100, 2)


def _consistency_detail(expected: float, actual: float, revenue: float) -> dict:
    """一致性檢查的機器可讀補充：差額（絕對 + 佔營收 %）。

    gap ＝ expected − actual：正值代表報表數字比『重算值』小（可能被某調整項扣減）。
    reviewer 可據此判斷差額是否等於某筆銷貨損益/其他收益費損調整（正常），
    還是像抄錯行/位數的離群值（需更正）。
    """
    gap = expected - actual
    return {
        "expected": round(expected, 2),
        "actual": round(actual, 2),
        "gap": round(gap, 2),
        "gap_pct_of_revenue": round(gap / revenue * 100, 2),
    }


def derive(s: FinancialStatement) -> DerivedMetrics:
    """由原始金額計算三大比率。"""
    return DerivedMetrics(
        gross_margin=_ratio(s.gross_profit, s.revenue),
        operating_margin=_ratio(s.operating_income, s.revenue),
        net_margin=_ratio(s.net_income, s.revenue),
    )


def validate(
    s: FinancialStatement,
    derived: DerivedMetrics | None = None,
    gross_tol: float | None = None,
    operating_tol: float | None = None,
) -> ValidationReport:
    """對原始數字做內部一致性檢查，回傳報告（含衍生比率）。

    gross_tol / operating_tol 為相對營收的容忍度；預設取 config（可用 .env 調），
    保留參數以利測試覆寫。毛利那條嚴（純恆等式）、營業利益那條寬（可能有中間科目）。
    """
    derived = derived or derive(s)
    gross_tol = settings.gross_tolerance if gross_tol is None else gross_tol
    operating_tol = settings.operating_tolerance if operating_tol is None else operating_tol
    issues: list[ValidationIssue] = []

    # 1) 必要欄位：營收是所有比率的分母，缺了無法驗證。
    if s.revenue is None:
        issues.append(ValidationIssue("error", "revenue", "缺少營業收入，無法計算比率與驗證"))
    elif s.revenue <= 0:
        issues.append(ValidationIssue("error", "revenue", f"營業收入非正數：{s.revenue}"))

    # 2) 期別／代碼：不擋，但提示。
    if not s.period:
        issues.append(ValidationIssue("warning", "period", "缺少期別（YYY年QN）"))
    if not s.stock_code:
        issues.append(ValidationIssue("warning", "stock_code", "缺少股票代號"))

    # 3) 大小關係：毛利不應大於營收；營業利益不應大於毛利。
    if s.revenue and s.gross_profit is not None and s.gross_profit > s.revenue:
        issues.append(
            ValidationIssue("warning", "gross_profit", f"毛利 {s.gross_profit} 大於營收 {s.revenue}")
        )
    if (
        s.gross_profit is not None
        and s.operating_income is not None
        and s.operating_income > s.gross_profit
    ):
        issues.append(
            ValidationIssue(
                "warning",
                "operating_income",
                f"營業利益 {s.operating_income} 大於毛利 {s.gross_profit}（少見，請複檢）",
            )
        )

    # 4) 加法一致性：毛利 ≈ 營收 − 成本；營業利益 ≈ 毛利 − 營業費用。
    if s.revenue and s.cost_of_revenue is not None and s.gross_profit is not None:
        expected = s.revenue - s.cost_of_revenue
        if abs(expected - s.gross_profit) > gross_tol * abs(s.revenue):
            d = _consistency_detail(expected, s.gross_profit, s.revenue)
            issues.append(
                ValidationIssue(
                    "warning",
                    "gross_profit",
                    f"毛利淨額 {s.gross_profit:,.0f} 與『營收−成本』{expected:,.0f} 相差 "
                    f"{d['gap']:+,.0f}（{d['gap_pct_of_revenue']:+.2f}% of 營收）；"
                    "若等於未實現/已實現銷貨損益調整屬正常，否則請查是否抄錯行或位數。",
                    detail=d,
                )
            )
    if (
        s.revenue
        and s.gross_profit is not None
        and s.operating_expenses is not None
        and s.operating_income is not None
    ):
        expected = s.gross_profit - s.operating_expenses
        if abs(expected - s.operating_income) > operating_tol * abs(s.revenue):
            d = _consistency_detail(expected, s.operating_income, s.revenue)
            issues.append(
                ValidationIssue(
                    "warning",
                    "operating_income",
                    f"營業利益 {s.operating_income:,.0f} 與『毛利−營業費用』{expected:,.0f} 相差 "
                    f"{d['gap']:+,.0f}（{d['gap_pct_of_revenue']:+.2f}% of 營收）；"
                    "差額或來自其他收益及費損淨額，否則請複檢。",
                    detail=d,
                )
            )

    # 5) 衍生比率落在合理範圍。
    for name, value in (
        ("gross_margin", derived.gross_margin),
        ("operating_margin", derived.operating_margin),
        ("net_margin", derived.net_margin),
    ):
        if value is not None and not (_MARGIN_MIN <= value <= _MARGIN_MAX):
            issues.append(
                ValidationIssue("error", name, f"{name} 計算結果 {value}% 超出合理範圍")
            )

    # 6) EPS：稀釋通常 ≤ 基本；EPS 應存在。
    if s.eps_basic is not None and s.eps_diluted is not None and s.eps_diluted > s.eps_basic + 0.01:
        issues.append(
            ValidationIssue(
                "warning", "eps_diluted", f"稀釋 EPS {s.eps_diluted} 大於基本 EPS {s.eps_basic}（少見）"
            )
        )
    if s.eps_basic is None:
        issues.append(ValidationIssue("warning", "eps_basic", "缺少基本每股盈餘"))

    return ValidationReport(derived=derived, issues=issues)
