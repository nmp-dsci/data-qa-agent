"""Central settings loader (pydantic-settings). Never hardcode secrets."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = three levels up from this file: src/data_qa_agent/config.py
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW_DIR = REPO_ROOT / "data" / "raw"


class Settings(BaseSettings):
    """Environment-driven settings, loaded from `.env` at the repo root."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/data_qa",
        alias="DATABASE_URL",
    )
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    langsmith_api_key: str | None = Field(default=None, alias="LANGSMITH_API_KEY")


def get_settings() -> Settings:
    return Settings()
