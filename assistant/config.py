"""Assistant configuration, read from environment variables."""

import os
from dataclasses import dataclass

DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_API_URL = "http://localhost:8000"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    llm_api_key: str
    llm_base_url: str
    model: str
    api_url: str


def load_settings() -> Settings:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("OPENROUTER_API_KEY is not set. Add it to your .env file (see .env.example).")
    return Settings(
        llm_api_key=api_key,
        llm_base_url=os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_LLM_BASE_URL,
        model=os.environ.get("BEDMED_MODEL") or DEFAULT_MODEL,
        api_url=os.environ.get("BEDMED_API_URL") or DEFAULT_API_URL,
    )
