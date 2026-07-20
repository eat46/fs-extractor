"""抽取流程的錯誤型別。

把各家 provider 底層例外（google.genai / anthropic）翻譯成統一、帶人類可讀
訊息的錯誤，讓 CLI 與 API 呈現一致：
  - `status_code`：API 對應的 HTTP 狀態碼。
  - `retryable`：是否值得自動重試（速率限制、暫時性 5xx）。
訊息一律寫成可直接秀給使用者的中文，不外洩 stack trace。
"""
from __future__ import annotations


class ExtractionError(Exception):
    """抽取流程可預期的失敗基底；訊息供 CLI / API 直接呈現。"""

    status_code = 502   # API 對應的 HTTP 狀態碼
    retryable = False   # 是否值得自動重試


class LLMAPIError(ExtractionError):
    """呼叫 LLM API 失敗（認證、找不到模型、請求格式等，非暫時性）。"""


class RateLimitError(LLMAPIError):
    """LLM 配額或速率限制（免費額度用罄最常見）。"""

    status_code = 429
    retryable = True


class LLMServerError(LLMAPIError):
    """LLM 服務端暫時性錯誤或連線問題（5xx、逾時、斷線）。"""

    retryable = True


class BadOutputError(ExtractionError):
    """LLM 回傳內容無法解析或不符 schema（空回應、壞 JSON、缺欄位）。"""
