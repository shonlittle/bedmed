"""Assistant configuration, read from environment variables."""

import os
from dataclasses import dataclass

DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_API_URL = "http://localhost:8000"
MISSING_KEY_MESSAGE = "OPENROUTER_API_KEY is not set. Add it to your .env file (see .env.example) and restart."


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    llm_api_key: str
    llm_base_url: str
    model: str
    api_url: str

    def require_llm_key(self) -> None:
        if not self.llm_api_key:
            raise ConfigError(MISSING_KEY_MESSAGE)


def load_settings() -> Settings:
    return Settings(
        llm_api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
        llm_base_url=os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_LLM_BASE_URL,
        model=os.environ.get("BEDMED_MODEL") or DEFAULT_MODEL,
        api_url=os.environ.get("BEDMED_API_URL") or DEFAULT_API_URL,
    )
