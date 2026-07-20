"""交付輸出：把抽取結果（原始數字 + 衍生比率 + 驗證狀態）轉成 JSON / CSV。"""
from __future__ import annotations

import csv
import io
import json

from app.schemas.extraction import FinancialStatement
from app.services.validation import ValidationReport, validate

# CSV 欄位順序：先原始金額，再衍生比率，最後驗證狀態。
RAW_FIELDS = [
    "stock_code",
    "company_name",
    "period",
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_expenses",
    "operating_income",
    "net_income",
    "eps_basic",
    "eps_diluted",
    "amount_unit",
    "eps_unit",
    "source_page",
    "confidence",
]
DERIVED_FIELDS = ["gross_margin", "operating_margin", "net_margin"]
STATUS_FIELDS = ["validation_ok", "validation_issues"]
CSV_FIELDS = RAW_FIELDS + DERIVED_FIELDS + STATUS_FIELDS


def result_dict(statement: FinancialStatement, report: ValidationReport) -> dict:
    """給 JSON 交付用的完整結構：raw / derived / validation 三段。"""
    return {
        "statement": statement.model_dump(),
        "validation": report.to_dict(),
    }


def to_json(statement: FinancialStatement, report: ValidationReport) -> str:
    return json.dumps(result_dict(statement, report), ensure_ascii=False, indent=2)


def _row(statement: FinancialStatement, report: ValidationReport) -> dict:
    row = statement.model_dump()
    row.update(
        gross_margin=report.derived.gross_margin,
        operating_margin=report.derived.operating_margin,
        net_margin=report.derived.net_margin,
        validation_ok=report.ok,
        validation_issues="; ".join(
            f"[{i.level}]{i.field}:{i.message}" for i in report.issues
        ),
    )
    return row


def to_csv_rows(items: list[tuple[FinancialStatement, ValidationReport]]) -> str:
    """一到多筆（statement, report）轉成 CSV 字串（含表頭）。"""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for statement, report in items:
        writer.writerow(_row(statement, report))
    return buf.getvalue()


def to_csv_statements(statements: list[FinancialStatement]) -> str:
    """便利函式：只有 statement 時，內部自動跑 validation 再輸出。"""
    return to_csv_rows([(s, validate(s)) for s in statements])
