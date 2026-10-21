"""Environment-based configuration.

Every secret comes from the environment (or a local, git-ignored key file that is
generated on first run). Nothing sensitive is hardcoded.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = BACKEND_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(BACKEND_DIR.parent / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"  # development | production | test
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    data_dir: Path = DEFAULT_DATA_DIR
    database_url: str = ""
    log_level: str = "INFO"
    log_json: bool = False

    # Security
    secret_key: str = ""  # signs sessions, CSRF and unsubscribe tokens
    encryption_key: str = ""  # Fernet key for stored credentials
    admin_password: str = ""  # optional bootstrap; otherwise set via first-run setup
    cookie_secure: bool = False  # set true behind HTTPS
    session_max_age_hours: int = 72
    max_request_bytes: int = 1_000_000
    max_upload_bytes: int = 10_000_000
    cors_origins: list[str] = Field(default_factory=list)

    # Public URL used to build unsubscribe links. Empty => mailto/reply-based opt-out only.
    public_base_url: str = ""

    # AI (all optional — the app runs without them)
    ai_provider: str = "template"  # template | openai_compatible | mock
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"
    ai_timeout_seconds: float = 60.0

    # OAuth apps (optional)
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "common"
    oauth_redirect_base_url: str = "http://localhost:8000"

    # Lead discovery (all optional; keys can also be stored encrypted from the UI)
    apollo_api_key: str = ""
    google_search_api_key: str = ""  # Custom Search JSON API — closed to new customers, ends 2027-01-01
    google_search_engine_id: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_max_input_chars: int = 12_000
    gemini_max_output_tokens: int = 1_024
    discovery_request_timeout_seconds: float = 15.0
    discovery_max_pages_per_domain: int = 5
    discovery_max_page_bytes: int = 1_500_000
    discovery_domain_delay_seconds: float = 1.0
    discovery_user_agent: str = "PersonalOutreachBot/1.0 (private single-user research tool)"

    # Worker
    worker_poll_seconds: float = 5.0
    worker_batch_size: int = 10
    inbox_sync_interval_minutes: int = 10
    stale_job_minutes: int = 15

    # Serve built frontend from FastAPI when present
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'outreach.db').as_posix()}"


def _load_or_create_key(path: Path, factory) -> str:
    """Persist a generated key in the data dir so restarts keep working.

    Only used when the operator did not supply the key via environment.
    """
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = factory()
    path.write_text(value, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - Windows
        pass
    return value


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.secret_key:
        s.secret_key = _load_or_create_key(s.data_dir / "secret.key", lambda: secrets.token_urlsafe(48))
    if not s.encryption_key:
        from cryptography.fernet import Fernet

        s.encryption_key = _load_or_create_key(s.data_dir / "encryption.key", lambda: Fernet.generate_key().decode())
    return s
