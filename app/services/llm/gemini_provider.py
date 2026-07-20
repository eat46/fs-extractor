"""Gemini provider（替代方案）。

用 google-genai SDK：原生 PDF 輸入 + Structured Outputs（response schema 強制 JSON）
+ token 用量回報。所有 provider 差異都被 base.LLMProvider 吸收。
"""
from __future__ import annotations

from app.config import settings
from app.services.errors import LLMAPIError, LLMServerError, RateLimitError
from app.services.llm.base import LLMProvider, LLMResponse

# 近似單價（USD / 1M tokens）；正式估算請以官方定價為準。
_PRICE_PER_1M = {"input": 0.30, "output": 2.50}


class GeminiProvider(LLMProvider):
    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise RuntimeError("未設定 GEMINI_API_KEY，無法使用 Gemini provider")
        from google import genai

        self._genai = genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model

    def extract(
        self,
        pdf_bytes: bytes,
        system_instruction: str,
        user_context: str,
        json_schema: dict,
    ) -> LLMResponse:
        from google.genai import types

        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_json_schema=json_schema,
            temperature=0.0,
        )
        try:
            resp = self._client.models.generate_content(
                model=self._model,
                contents=[pdf_part, user_context],
                config=config,
            )
        except self._genai.errors.APIError as exc:
            raise self._translate(exc) from exc
        except Exception as exc:  # 網路 / 逾時等非 API 例外 → 視為暫時性
            raise LLMServerError(f"Gemini 連線失敗：{exc}") from exc
        usage = resp.usage_metadata
        return LLMResponse(
            raw_json=resp.text,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            model=self._model,
            provider="gemini",
        )

    def _translate(self, exc: Exception) -> Exception:
        """把 google.genai APIError 翻譯成統一錯誤（帶可讀中文訊息）。"""
        code = getattr(exc, "code", None)
        msg = getattr(exc, "message", None) or str(exc)
        if code == 429:
            return RateLimitError(f"Gemini 配額或速率限制，請稍候再試：{msg}")
        if code in (500, 502, 503, 504):
            return LLMServerError(f"Gemini 服務暫時無法使用（{code}）：{msg}")
        if code == 404:
            return LLMAPIError(
                f"找不到模型「{self._model}」或該模型不支援此呼叫，"
                f"請檢查 .env 的 GEMINI_MODEL：{msg}"
            )
        if code in (401, 403):
            return LLMAPIError(f"Gemini 認證失敗，請檢查 GEMINI_API_KEY：{msg}")
        return LLMAPIError(f"Gemini 呼叫失敗（{code}）：{msg}")

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * _PRICE_PER_1M["input"]
            + output_tokens / 1_000_000 * _PRICE_PER_1M["output"]
        )
