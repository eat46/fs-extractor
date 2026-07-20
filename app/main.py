"""FastAPI 進入點（MVP：單檔同步抽取，無資料庫、無佇列）。

    uvicorn app.main:app --reload   # http://localhost:8000/

端點：
  GET  /            上傳頁（拖曳單一 PDF）
  POST /api/extract 上傳季報 PDF → 同步抽取 → 回傳 JSON（含 CSV 字串）
  GET  /health      健康檢查（顯示目前 provider）
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.config import settings
from app.services import export
from app.services.errors import ExtractionError
from app.services.extraction_service import ExtractionOutcome, extract_one

app = FastAPI(title="台股季報財務數據萃取 · MVP")

_INDEX = Path(__file__).resolve().parent / "static" / "index.html"


def _run_extraction(pdf_bytes: bytes, filename: str) -> ExtractionOutcome:
    """跑抽取，把錯誤翻成帶狀態碼的 HTTPException（可讀訊息，不外洩 trace）。"""
    try:
        return extract_one(pdf_bytes, filename)
    except ExtractionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 兜底未預期錯誤
        raise HTTPException(status_code=500, detail=f"未預期的內部錯誤：{exc}") from exc


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX.read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": settings.llm_provider}


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)) -> dict:
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail=f"{file.filename} 非 PDF")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="空檔案")

    outcome = _run_extraction(pdf_bytes, file.filename or "upload.pdf")
    result = outcome.to_dict()
    result["csv"] = export.to_csv_rows([(outcome.statement, outcome.report)])
    return result


@app.post("/api/extract.csv", response_class=PlainTextResponse)
async def extract_csv(file: UploadFile = File(...)) -> str:
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail=f"{file.filename} 非 PDF")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="空檔案")
    outcome = _run_extraction(pdf_bytes, file.filename or "upload.pdf")
    return export.to_csv_rows([(outcome.statement, outcome.report)])
