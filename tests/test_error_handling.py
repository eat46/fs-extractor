"""錯誤處理單元測試（不呼叫真實 LLM）。

涵蓋三層：
  1. provider 例外翻譯（google.genai / anthropic → 統一 ExtractionError）。
  2. _call_llm 的重試策略（暫時性錯誤退避重試、永久性錯誤快速失敗）。
  3. _parse_statement 對壞輸出的防護（空回應 / 壞 JSON / 不符 schema）。
"""
from __future__ import annotations

import pytest

from app.services import extraction_service
from app.services.errors import (
    BadOutputError,
    LLMAPIError,
    LLMServerError,
    RateLimitError,
)
from app.services.extraction_service import _call_llm, _parse_statement
from app.services.llm.base import LLMProvider, LLMResponse
from app.services.llm.claude_provider import ClaudeProvider
from app.services.llm.gemini_provider import GeminiProvider


# --- 測試替身 -----------------------------------------------------------------

class _FakeProvider(LLMProvider):
    """依 script 逐次拋出例外或回傳結果，並記錄呼叫次數。"""

    def __init__(self, script: list) -> None:
        self._script = script
        self.calls = 0

    def extract(self, **_) -> LLMResponse:
        self.calls += 1
        item = self._script[self.calls - 1]
        if isinstance(item, Exception):
            raise item
        return item

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0


def _ok_response() -> LLMResponse:
    return LLMResponse(raw_json="{}", input_tokens=1, output_tokens=1, model="m", provider="p")


class _FakeGeminiError(Exception):
    """模擬 google.genai.errors.APIError（帶 code / message）。"""

    def __init__(self, code: int, message: str = "boom") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _FakeAnthropicError(Exception):
    """模擬帶 status_code 的 anthropic APIStatusError。"""

    def __init__(self, status_code: int, message: str = "boom") -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """重試測試不真的睡；把退避 sleep 換成 no-op。"""
    monkeypatch.setattr(extraction_service.time, "sleep", lambda _s: None)


# --- 1. provider 例外翻譯 ------------------------------------------------------

@pytest.mark.parametrize(
    "code, expected, retryable",
    [
        (429, RateLimitError, True),
        (503, LLMServerError, True),
        (500, LLMServerError, True),
        (404, LLMAPIError, False),
        (401, LLMAPIError, False),
        (400, LLMAPIError, False),
    ],
)
def test_gemini_translate_maps_status_codes(code, expected, retryable):
    provider = object.__new__(GeminiProvider)  # 略過 __init__（免 API key）
    provider._model = "gemini-x"
    err = provider._translate(_FakeGeminiError(code))
    assert type(err) is expected
    assert err.retryable is retryable


def test_gemini_translate_404_mentions_model_and_env():
    provider = object.__new__(GeminiProvider)
    provider._model = "gemini-3.1-flash-live"
    err = provider._translate(_FakeGeminiError(404))
    assert "gemini-3.1-flash-live" in str(err)
    assert "GEMINI_MODEL" in str(err)


@pytest.mark.parametrize(
    "status, expected, retryable",
    [
        (429, RateLimitError, True),
        (503, LLMServerError, True),
        (404, LLMAPIError, False),
        (401, LLMAPIError, False),
    ],
)
def test_claude_translate_maps_status_codes(status, expected, retryable):
    provider = object.__new__(ClaudeProvider)
    provider._model = "claude-x"
    err = provider._translate(_FakeAnthropicError(status))
    assert type(err) is expected
    assert err.retryable is retryable


# --- 2. _call_llm 重試策略 -----------------------------------------------------

def test_retryable_error_retries_then_succeeds():
    provider = _FakeProvider([RateLimitError("429"), _ok_response()])
    resp = _call_llm(provider, "f.pdf", b"%PDF")
    assert resp.provider == "p"
    assert provider.calls == 2  # 第一次 429、第二次成功


def test_retryable_error_exhausts_attempts_then_raises():
    provider = _FakeProvider([RateLimitError("429")] * 5)
    with pytest.raises(RateLimitError):
        _call_llm(provider, "f.pdf", b"%PDF")
    assert provider.calls == extraction_service._MAX_ATTEMPTS  # 用滿次數才放棄


def test_non_retryable_error_fails_fast_without_retry():
    provider = _FakeProvider([LLMAPIError("404")] * 5)
    with pytest.raises(LLMAPIError):
        _call_llm(provider, "f.pdf", b"%PDF")
    assert provider.calls == 1  # 永久性錯誤不重試


# --- 3. _parse_statement 壞輸出防護 -------------------------------------------

def test_empty_response_raises_bad_output():
    resp = LLMResponse(raw_json="", input_tokens=0, output_tokens=0, model="m", provider="p")
    with pytest.raises(BadOutputError):
        _parse_statement(resp)


def test_malformed_json_raises_bad_output():
    resp = LLMResponse(raw_json="not json{", input_tokens=1, output_tokens=1, model="m", provider="p")
    with pytest.raises(BadOutputError):
        _parse_statement(resp)


def test_schema_mismatch_raises_bad_output():
    # period 應為字串；給 dict 觸發 pydantic 驗證失敗 → BadOutputError
    resp = LLMResponse(
        raw_json='{"stock_code": "3008", "period": {"bad": 1}}',
        input_tokens=1,
        output_tokens=1,
        model="m",
        provider="p",
    )
    with pytest.raises(BadOutputError):
        _parse_statement(resp)


def test_valid_json_parses_to_statement():
    resp = LLMResponse(
        raw_json='{"stock_code": "3008", "company_name": "大立光電", "period": "115年Q1", "revenue": 15544079}',
        input_tokens=1,
        output_tokens=1,
        model="m",
        provider="p",
    )
    statement = _parse_statement(resp)
    assert statement.stock_code == "3008"
    assert statement.revenue == 15544079
