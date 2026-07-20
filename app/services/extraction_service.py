"""核心抽取服務（單檔同步，無資料庫）。

單篇 PDF 的完整流程：
  原生 PDF → LLM(provider) 抽原始數字 → Pydantic 驗證 → validation 層(衍生比率+一致性檢查)
             → ExtractionOutcome
所有 provider 差異都被 base.LLMProvider 吸收，本流程與底層模型無關。
無 DB／佇列：直接回傳結果物件，由呼叫端（API / CLI）決定如何輸出。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from pydantic import ValidationError

from app.prompts.system_prompt import SYSTEM_INSTRUCTION, build_user_context
from app.schemas.extraction import FinancialStatement, extraction_json_schema
from app.services import validation
from app.services.errors import BadOutputError, ExtractionError
from app.services.llm.base import LLMProvider, LLMResponse, get_provider
from app.services.validation import ValidationReport

# 暫時性錯誤（速率限制、5xx、斷線）自動重試；固定次數 + 指數退避。
_MAX_ATTEMPTS = 3
_BASE_DELAY_S = 2.0


@dataclass
class ExtractionOutcome:
    """一次抽取的完整結果（原始數據 + 衍生/驗證 + 用量成本）。"""

    statement: FinancialStatement       # LLM 抽出的原始申報數字
    report: ValidationReport            # 衍生比率 + 一致性檢查
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    filename: str

    def to_dict(self) -> dict:
        return {
            "statement": self.statement.model_dump(),
            "validation": self.report.to_dict(),
            "meta": {
                "filename": self.filename,
                "provider": self.provider,
                "model": self.model,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost_usd, 6),
            },
        }


def _call_llm(provider: LLMProvider, filename: str, pdf_bytes: bytes) -> LLMResponse:
    """呼叫 provider，對暫時性錯誤（速率限制／5xx／斷線）指數退避重試。"""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return provider.extract(
                pdf_bytes=pdf_bytes,
                system_instruction=SYSTEM_INSTRUCTION,
                user_context=build_user_context(filename),
                json_schema=extraction_json_schema(),
            )
        except ExtractionError as exc:
            if not exc.retryable or attempt == _MAX_ATTEMPTS:
                raise
            time.sleep(_BASE_DELAY_S * 2 ** (attempt - 1))
    raise AssertionError("unreachable")  # for 迴圈必以 return 或 raise 收尾


def _parse_statement(resp: LLMResponse) -> FinancialStatement:
    """把 LLM 回傳的 JSON 解析成契約物件；壞內容一律轉成 BadOutputError。"""
    if not resp.raw_json or not resp.raw_json.strip():
        raise BadOutputError("LLM 回傳空內容（可能被安全機制攔截或逾時），無法抽取。")
    try:
        data = json.loads(resp.raw_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BadOutputError(f"LLM 回傳非合法 JSON，無法解析：{exc}") from exc
    try:
        return FinancialStatement(**data)
    except (ValidationError, TypeError) as exc:
        raise BadOutputError(f"LLM 回傳的欄位不符抽取契約：{exc}") from exc


def extract_one(pdf_bytes: bytes, filename: str) -> ExtractionOutcome:
    """處理單篇季報 PDF：抽原始數字 → 驗證 → 回傳結果與用量成本。

    失敗時一律拋出 `ExtractionError` 子類（帶可讀訊息、HTTP 狀態碼、可否重試）；
    呼叫端（CLI / API）據此呈現，不外洩 stack trace。
    """
    provider = get_provider()
    resp = _call_llm(provider, filename, pdf_bytes)
    statement = _parse_statement(resp)
    report = validation.validate(statement)
    return ExtractionOutcome(
        statement=statement,
        report=report,
        provider=resp.provider,
        model=resp.model,
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        cost_usd=provider.estimate_cost(resp.input_tokens, resp.output_tokens),
        filename=filename,
    )
