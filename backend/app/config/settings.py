"""App settings, overridable via environment variables (see .env.example)."""

from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application-wide settings loaded from environment variables."""

    # Application
    APP_NAME: str = "DevInbox"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-this-to-a-random-secret-key-in-production"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: str = "sqlite:///./data/devinbox.db"

    # Qwen Cloud (OpenAI-compatible endpoint)
    QWEN_BASE_URL: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen3.7-plus"
    QWEN_MAX_TOKENS: int = 4096
    QWEN_TEMPERATURE: float = 0.2

    # GitHub / Agent behavior
    DEFAULT_BASE_BRANCH: str = "main"
    AGENT_BRANCH_PREFIX: str = "devinbox"
    REQUIRE_APPROVAL: bool = True
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0

    # Security
    KEYS_DIR: str = "./data/keys"

    # Comma-separated list of origins allowed to make cross-origin requests
    # to the API (e.g. "https://example.com,https://app.example.com"). Leave
    # empty (the default) if nothing needs cross-origin access — the
    # dashboard itself is served same-origin and doesn't need CORS at all.
    CORS_ALLOWED_ORIGINS: str = ""

    # Alibaba Cloud OSS (audit trail archival)
    ALIBABA_OSS_ENDPOINT: str = "https://oss-ap-southeast-1.aliyuncs.com"
    ALIBABA_OSS_BUCKET: str = ""
    ALIBABA_OSS_ENABLED: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Singleton accessor for application settings."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
