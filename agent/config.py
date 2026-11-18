"""Load configuration from environment variables. Secrets never appear in source or logs."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = "https://nakywgkztvzkihdjnwbc.supabase.co/functions/v1/helpdesk"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable {name} (see .env.example)")
    return value


@dataclass(frozen=True)
class Settings:
    helpdesk_base_url: str
    helpdesk_api_key: str
    candidate_email: str
    candidate_name: str
    groq_api_key: str
    groq_model: str


def load_settings(require_groq: bool = True) -> Settings:
    return Settings(
        helpdesk_base_url=os.environ.get("HELPDESK_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        helpdesk_api_key=_require("HELPDESK_API_KEY"),
        candidate_email=_require("CANDIDATE_EMAIL"),
        candidate_name=os.environ.get("CANDIDATE_NAME", "").strip(),
        groq_api_key=_require("GROQ_API_KEY") if require_groq else os.environ.get("GROQ_API_KEY", ""),
        groq_model=os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
    )
