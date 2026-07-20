"""Claude provider（預設）。

原生 PDF 以 document block（base64）輸入；結構化輸出用「強制 tool use」達成：
把 json_schema 當成單一 tool 的 input_schema，並用 tool_choice 強制模型呼叫它，
模型的 tool input 即為符合 schema 的 JSON。切 provider 只改 .env 的 LLM_PROVIDER。
"""
from __future__ import annotations

import base64
import json

from app.config import settings
from app.services.errors import LLMAPIError, LLMServerError, RateLimitError
from app.services.llm.base import LLMProvider, LLMResponse

# 近似單價（USD / 1M tokens），以模型 ID 前綴比對；找不到時用預設。
_PRICE_PER_1M = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
}
_PRICE_DEFAULT = {"input": 3.00, "output": 15.00}

# 新一代模型（Sonnet 5 / Opus 4.7 / 4.8）：不接受非預設 temperature（傳了會 400），
# 且省略 thinking 時預設啟用 adaptive thinking。改用 adaptive thinking + tool_choice=auto，
# 在需要推理定位的多頁財務表上比「強制工具輸出＋關 thinking」穩定。
# 舊模型（Haiku 4.5 等）維持 temperature=0 + 強制工具輸出的決定性行為。
_NEXT_GEN_PREFIXES = ("claude-sonnet-5", "claude-opus-4-7", "claude-opus-4-8")


class ClaudeProvider(LLMProvider):
    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError("未設定 ANTHROPIC_API_KEY，無法使用 Claude provider")
        from anthropic import Anthropic

        self._client = Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.claude_model
        self._next_gen = self._model.startswith(_NEXT_GEN_PREFIXES)

    def extract(
        self,
        pdf_bytes: bytes,
        system_instruction: str,
        user_context: str,
        json_schema: dict,
    ) -> LLMResponse:
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")

        tool = {
            "name": "emit_extraction",
            "description": "回傳符合財報數據抽取契約的結構化結果。",
            "input_schema": json_schema,
        }

        kwargs: dict = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system_instruction,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": "emit_extraction"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": user_context},
                    ],
                }
            ],
        }
        if self._next_gen:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["tool_choice"] = {"type": "auto"}
            kwargs["max_tokens"] = 8192
        else:
            kwargs["temperature"] = 0.0

        try:
            msg = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise self._translate(exc) from exc

        tool_input = next(
            (block.input for block in msg.content if block.type == "tool_use"), {}
        )
        return LLMResponse(
            raw_json=json.dumps(tool_input, ensure_ascii=False),
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            model=self._model,
            provider="claude",
        )

    def _translate(self, exc: Exception) -> Exception:
        """把 anthropic 例外翻譯成統一錯誤（帶可讀中文訊息）。"""
        import anthropic

        if isinstance(exc, anthropic.APIConnectionError):
            return LLMServerError(f"Claude 連線失敗：{exc}")
        status = getattr(exc, "status_code", None)
        if status == 429:
            return RateLimitError(f"Claude 配額或速率限制，請稍候再試：{exc}")
        if status in (500, 502, 503, 504):
            return LLMServerError(f"Claude 服務暫時無法使用（{status}）：{exc}")
        if status == 404:
            return LLMAPIError(
                f"找不到模型「{self._model}」，請檢查 .env 的 CLAUDE_MODEL：{exc}"
            )
        if status in (401, 403):
            return LLMAPIError(f"Claude 認證失敗，請檢查 ANTHROPIC_API_KEY：{exc}")
        if status is not None:
            return LLMAPIError(f"Claude 呼叫失敗（{status}）：{exc}")
        return LLMAPIError(f"Claude 呼叫失敗：{exc}")

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        price = next(
            (p for prefix, p in _PRICE_PER_1M.items() if self._model.startswith(prefix)),
            _PRICE_DEFAULT,
        )
        return (
            input_tokens / 1_000_000 * price["input"]
            + output_tokens / 1_000_000 * price["output"]
        )
