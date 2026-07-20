"""CLI：抽取單一季報 PDF，輸出 JSON（與可選 CSV）。

用法：
    python -m scripts.extract_cli path/to/report.pdf
    python -m scripts.extract_cli path/to/report.pdf --csv out.csv --json out.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services import export
from app.services.errors import ExtractionError
from app.services.extraction_service import extract_one


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="台股季報財務數據萃取（單檔）")
    parser.add_argument("pdf", type=Path, help="季報 PDF 路徑")
    parser.add_argument("--json", type=Path, default=None, help="另存 JSON 到此路徑")
    parser.add_argument("--csv", type=Path, default=None, help="另存 CSV 到此路徑")
    args = parser.parse_args(argv)

    if not args.pdf.exists():
        print(f"找不到檔案：{args.pdf}", file=sys.stderr)
        return 1

    pdf_bytes = args.pdf.read_bytes()
    try:
        outcome = extract_one(pdf_bytes, args.pdf.name)
    except ExtractionError as exc:
        print(f"抽取失敗：{exc}", file=sys.stderr)
        return 2

    json_text = export.to_json(outcome.statement, outcome.report)
    print(json_text)

    m = outcome
    print(
        f"\n[{m.provider}/{m.model}] in {m.input_tokens} / out {m.output_tokens} tok "
        f"· ~${m.cost_usd:.6f}",
        file=sys.stderr,
    )
    # 驗證摘要（人工複檢用）
    report = outcome.report
    status = "PASS" if report.ok else "FAIL"
    print(f"[validate] {status}｜issues={len(report.issues)}", file=sys.stderr)
    for i in report.issues:
        print(f"  - [{i.level}] {i.field}: {i.message}", file=sys.stderr)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json_text, encoding="utf-8")
        print(f"已寫入 {args.json}", file=sys.stderr)
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        args.csv.write_text(
            export.to_csv_rows([(outcome.statement, outcome.report)]), encoding="utf-8"
        )
        print(f"已寫入 {args.csv}", file=sys.stderr)
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
