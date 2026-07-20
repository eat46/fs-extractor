"""validation 層單元測試（純邏輯，不呼叫 LLM）。"""
from __future__ import annotations

from app.schemas.extraction import FinancialStatement
from app.services import validation


def _largan_q1() -> FinancialStatement:
    """以樣本 3008 115年Q1 的原始數字建構（金額為新台幣千元）。"""
    return FinancialStatement(
        stock_code="3008",
        company_name="大立光電",
        period="115年Q1",
        revenue=15544079,
        gross_profit=7679569,
        operating_income=5812218,
        net_income=6175912,
        eps_basic=46.63,
        eps_diluted=46.11,
    )


def test_derive_margins_from_raw_amounts():
    s = _largan_q1()
    d = validation.derive(s)
    assert d.gross_margin == 49.41     # 7679569 / 15544079 * 100
    assert d.operating_margin == 37.39
    assert d.net_margin == 39.73


def test_valid_statement_passes():
    report = validation.validate(_largan_q1())
    assert report.ok
    assert not [i for i in report.issues if i.level == "error"]


def test_missing_revenue_is_error():
    s = _largan_q1()
    s.revenue = None
    report = validation.validate(s)
    assert not report.ok
    assert any(i.field == "revenue" and i.level == "error" for i in report.issues)


def test_gross_profit_exceeds_revenue_warns():
    s = _largan_q1()
    s.gross_profit = s.revenue + 1
    report = validation.validate(s)
    assert any(i.field == "gross_profit" and i.level == "warning" for i in report.issues)


def test_additive_consistency_warns_when_gross_mismatches_cost():
    s = _largan_q1()
    s.cost_of_revenue = 1_000_000  # 與毛利/營收明顯對不上
    report = validation.validate(s)
    issue = next(i for i in report.issues if i.field == "gross_profit")
    # expected = 15,544,079 − 1,000,000 = 14,544,079；actual(毛利) = 7,679,569
    assert issue.detail["expected"] == 14_544_079
    assert issue.detail["actual"] == 7_679_569
    assert issue.detail["gap"] == 14_544_079 - 7_679_569
    # gap 佔營收 % 也一併回報供 reviewer 判斷是否為合理調整
    assert issue.detail["gap_pct_of_revenue"] == round(
        (14_544_079 - 7_679_569) / 15_544_079 * 100, 2
    )


def test_additive_consistency_ok_within_tolerance():
    s = _largan_q1()
    s.cost_of_revenue = s.revenue - s.gross_profit  # 剛好一致
    report = validation.validate(s)
    assert report.ok
    assert not any("營收−成本" in i.message for i in report.issues)


def test_gross_tolerance_is_strict_operating_is_loose():
    """兩層容忍度：同樣 ~0.3% 相對營收的落差，毛利觸發、營業利益不觸發。"""
    s = _largan_q1()
    gap = round(s.revenue * 0.003)  # 0.3% of 營收：> 毛利 0.1%，< 營業利益 2%
    # 毛利：營收−成本 與 毛利淨額 差 0.3% → 應觸發
    s.cost_of_revenue = s.revenue - s.gross_profit + gap
    # 營業利益：毛利−費用 與 營業利益 差 0.3% → 不應觸發
    s.operating_expenses = s.gross_profit - s.operating_income + gap
    report = validation.validate(s)
    fields = {i.field for i in report.issues}
    assert "gross_profit" in fields          # 嚴（0.1%）→ 觸發
    assert "operating_income" not in fields  # 寬（2%）→ 不觸發


def test_tolerances_are_overridable():
    """毛利落差落在自訂寬容忍度內時，不觸發（驗證參數可覆寫 / .env 可調）。"""
    s = _largan_q1()
    s.cost_of_revenue = s.revenue - s.gross_profit + round(s.revenue * 0.003)
    strict = validation.validate(s, gross_tol=0.001)
    loose = validation.validate(s, gross_tol=0.01)
    assert any(i.field == "gross_profit" for i in strict.issues)
    assert not any(i.field == "gross_profit" for i in loose.issues)
