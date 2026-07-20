"""集中設定（pydantic-settings，讀 .env）。

抽取服務、provider 工廠都只依賴這裡的 `settings`；
換模型、調門檻都只改 .env，不動程式碼。
本專案為 MVP 核心：無資料庫、無佇列，單檔同步抽取。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM provider 切換（claude | gemini）---
    llm_provider: str = "gemini"
    anthropic_api_key: str | None = None
    claude_model: str = "claude-haiku-4-5-20251001"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-flash-lite"

    # --- 抽取行為 ---
    confidence_threshold: float = 0.75  # 僅供人工複檢參考

    # --- 驗證層一致性容忍度（相對營收；見 services/validation.py）---
    # 毛利＝營收−成本 是純恆等式，理論上精確相等 → 收很嚴（0.1%）；超過多半是抽取錯誤
    #   （抓錯期別、抓到附註而非主表），或該公司有未實現/已實現銷貨損益調整。
    # 營業利益＝毛利−營業費用 之間可能有其他收益及費損等中間科目，且 IFRS 18（台灣 117
    #   會計年度接軌）會讓營業利益分類更細 → 留餘裕（2%）。
    gross_tolerance: float = 0.001      # 毛利一致性容忍度（相對營收）
    operating_tolerance: float = 0.02   # 營業利益一致性容忍度（相對營收）

    # --- 輸出 ---
    output_dir: str = "./_out"


@lru_cache
def _load() -> Settings:
    return Settings()


settings = _load()
