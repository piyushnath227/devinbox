"""App settings, overridable via environment variables (see .env.example)."""

from typing import Optional
from pydantic_settings import BaseSettings
import structlog

logger = structlog.get_logger()


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
    # FIX #2 & #3: Validate SECRET_KEY in production
    TRUSTED_PROXY_IPS: str = ""  # Comma-separated list of proxy IPs to trust X-Forwarded-For from

    # Comma-separated list of origins allowed to make cross-origin requests
    # to the API (e.g. "https://example.com,https://app.example.com"). Leave
    # empty (the default) if nothing needs cross-origin access — the
    # dashboard itself is served same-origin and doesn't need CORS at all.
    CORS_ALLOWED_ORIGINS: str = ""

    # Alibaba Cloud OSS (audit trail archival)
    ALIBABA_OSS_ENDPOINT: str = "https://oss-ap-southeast-1.aliyuncs.com"
    ALIBABA_OSS_BUCKET: str = ""
    ALIBABA_OSS_ENABLED: bool = False

    # Redis job queue (optional). If empty, webhook processing falls back
    # to FastAPI BackgroundTasks automatically -- Redis is an upgrade for
    # durability/scaling, never a hard requirement to run the app.
    REDIS_URL: str = ""
    MAX_JOBS_PER_MINUTE: int = 20

    # If Qwen's own reported confidence in a generated fix is below this,
    # skip opening a PR entirely and flag it for a human to look at
    # instead. This avoids spending GitHub API calls (fork/branch/PR) and
    # cluttering a maintainer's repo with a low-quality automated PR --
    # a wrong guess left as a comment is much less costly to everyone
    # than a wrong guess shipped as a pull request.
    MIN_SOLUTION_CONFIDENCE: float = 0.5

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def __init__(self, **data):
        """Validate SECRET_KEY on initialization (FIX #3)."""
        super().__init__(**data)
        # FIX #3: Warn if using default SECRET_KEY in production
        if self.SECRET_KEY == "change-this-to-a-random-secret-key-in-production":
            if not self.DEBUG:
                raise ValueError(
                    "CRITICAL: SECRET_KEY is using the default value in production mode! "
                    "Set SECRET_KEY environment variable to a secure random value immediately."
                )
            else:
                logger.warning(
                    "settings_default_secret_key",
                    message="Using default SECRET_KEY. Change this in production!"
                )


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Singleton accessor for application settings."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
