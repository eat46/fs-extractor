"""LLM Provider 抽象層 —— 本架構的核心設計決策。

把 provider 抽象成統一介面後，抽取服務（extraction_service）完全不需要知道
底層是 Claude 還是 Gemini；換模型只改 config.llm_provider 一個開關。
兩者共同能力：原生 PDF 輸入、Structured Outputs、token 用量回報。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """provider 無關的統一回傳。"""

    raw_json: str  # 符合 schema 的 JSON 字串
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


class LLMProvider(ABC):
    """所有 provider 的統一介面。"""

    @abstractmethod
    def extract(
        self,
        pdf_bytes: bytes,
        system_instruction: str,
        user_context: str,
        json_schema: dict,
    ) -> LLMResponse:
        """原生 PDF 輸入 → 依 json_schema 強制輸出結構化 JSON。"""
        raise NotImplementedError

    @abstractmethod
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """依單價估算單篇成本（USD）。"""
        raise NotImplementedError


def get_provider() -> LLMProvider:
    """依 config 回傳對應 provider（工廠）。"""
    from app.config import settings

    if settings.llm_provider == "gemini":
        from app.services.llm.gemini_provider import GeminiProvider

        return GeminiProvider()
    from app.services.llm.claude_provider import ClaudeProvider

    return ClaudeProvider()
