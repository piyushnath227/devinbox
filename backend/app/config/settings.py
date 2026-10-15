"""App settings, overridable via environment variables (see .env.example)."""

from typing import Optional
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
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

    # LLM provider (any OpenAI-compatible endpoint). Presets: qwen | gemini |
    # groq | openrouter | openai | ollama | custom. Credentials are usually
    # saved from the dashboard; env vars are the headless/CI alternative.
    # Legacy QWEN_* variable names are still honoured.
    LLM_PROVIDER: str = Field("", validation_alias=AliasChoices("LLM_PROVIDER"))
    LLM_BASE_URL: str = Field("", validation_alias=AliasChoices("LLM_BASE_URL", "QWEN_BASE_URL"))
    LLM_MODEL: str = Field("", validation_alias=AliasChoices("LLM_MODEL", "QWEN_MODEL"))
    LLM_API_KEY: str = Field("", validation_alias=AliasChoices("LLM_API_KEY"))
    LLM_FALLBACK_MODELS: str = ""  # comma-separated, tried in order if the primary fails
    LLM_MAX_TOKENS: int = Field(4096, validation_alias=AliasChoices("LLM_MAX_TOKENS", "QWEN_MAX_TOKENS"))
    LLM_TEMPERATURE: float = Field(0.2, validation_alias=AliasChoices("LLM_TEMPERATURE", "QWEN_TEMPERATURE"))
    LLM_TIMEOUT_SECONDS: float = 60.0
    # Max in-flight LLM requests PER PROCESS (each worker container has its
    # own cap; total concurrency = workers x this). Protects provider rate limits.
    LLM_MAX_CONCURRENCY: int = 4

    # Webhook authentication. GitHub signs every delivery with this secret
    # (X-Hub-Signature-256); unsigned/forged requests are rejected. It can
    # also be saved from the dashboard (GitHub card). Set
    # REQUIRE_WEBHOOK_SIGNATURE=false ONLY for local experiments.
    WEBHOOK_SECRET: str = ""
    REQUIRE_WEBHOOK_SIGNATURE: bool = True

    # GitHub / Agent behavior
    DEFAULT_BASE_BRANCH: str = "main"
    AGENT_BRANCH_PREFIX: str = "devinbox"
    REQUIRE_APPROVAL: bool = True
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0

    # Security
    KEYS_DIR: str = "./data/keys"
    # Where encrypted API keys live: auto (db when DATABASE_URL is Postgres, else files) | db | files
    SECRETS_BACKEND: str = "auto"
    # Optional: set the admin password at first startup so nobody can claim an unclaimed instance.
    ADMIN_BOOTSTRAP_PASSWORD: str = ""
    # FIX #2 & #3: Validate SECRET_KEY in production
    TRUSTED_PROXY_IPS: str = ""  # Comma-separated list of proxy IPs to trust X-Forwarded-For from

    # Comma-separated list of origins allowed to make cross-origin requests
    # to the API (e.g. "https://example.com,https://app.example.com"). Leave
    # empty (the default) if nothing needs cross-origin access — the
    # dashboard itself is served same-origin and doesn't need CORS at all.
    CORS_ALLOWED_ORIGINS: str = ""

    # Audit-trail archival (cloud-agnostic). Backends: auto | none | local | s3
    #   auto  -> S3-compatible store if credentials were saved in the dashboard, else local disk
    #   local -> JSON/diffs written under AUDIT_ARCHIVE_LOCAL_DIR
    #   s3    -> AWS S3, Cloudflare R2, MinIO, Backblaze, Supabase, Alibaba OSS (S3 API), ...
    AUDIT_ARCHIVE_BACKEND: str = "auto"
    AUDIT_ARCHIVE_LOCAL_DIR: str = "./data/audit"
    S3_BUCKET: str = ""
    S3_ENDPOINT_URL: str = ""      # blank = AWS S3
    S3_REGION: str = ""
    S3_ADDRESSING_STYLE: str = "auto"  # auto | path | virtual

    # Redis job queue (optional). If empty, webhook processing falls back
    # to FastAPI BackgroundTasks automatically -- Redis is an upgrade for
    # durability/scaling, never a hard requirement to run the app.
    REDIS_URL: str = ""
    MAX_JOBS_PER_MINUTE: int = Field(20, ge=1, le=10000)
    AGENT_REQUIRED_LABEL: str = "devinbox"
    MAX_WEBHOOK_BYTES: int = 1_000_000

    # If Qwen's own reported confidence in a generated fix is below this,
    # skip opening a PR entirely and flag it for a human to look at
    # instead. This avoids spending GitHub API calls (fork/branch/PR) and
    # cluttering a maintainer's repo with a low-quality automated PR --
    # a wrong guess left as a comment is much less costly to everyone
    # than a wrong guess shipped as a pull request.
    MIN_SOLUTION_CONFIDENCE: float = 0.5

    # extra="ignore": a shared .env (e.g. docker-compose-only variables such as
    # MINIO_ROOT_PASSWORD) must not crash the app on startup.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", populate_by_name=True, extra="ignore",
    )

    def __init__(self, **data):
        """Validate SECRET_KEY on initialization (FIX #3)."""
        super().__init__(**data)
        # FIX #3: Warn if using default SECRET_KEY in production.
        # Checks against every placeholder actually shipped in this repo
        # (.env.example, docker-compose.yml's fallback, etc.) -- a single
        # exact-string check silently misses the rest if they ever drift
        # out of sync with each other.
        _KNOWN_INSECURE_DEFAULTS = {
            "change-this-to-a-random-secret-key-in-production",
            "change-this-to-a-random-secret-key",
            "change-this-in-production",
        }
        if self.SECRET_KEY in _KNOWN_INSECURE_DEFAULTS or self.SECRET_KEY.startswith("change-this"):
            if not self.DEBUG:
                raise ValueError(
                    "CRITICAL: SECRET_KEY is using a default/placeholder value in production mode! "
                    "Set SECRET_KEY environment variable to a secure random value immediately "
                    "(e.g. `openssl rand -hex 32`)."
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
