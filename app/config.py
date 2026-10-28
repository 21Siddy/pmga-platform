import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    PMGA Configuration with environment-variable support.
    Follows Safe & Sound principles: secure defaults, strict rate-limiting options,
    and support for both Gemini and University KI-Connect (Mistral Small 4).
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8080
    APP_BASE_URL: str = "http://localhost:8080"
    APP_SECRET_KEY: str = "change-me-to-a-secure-random-token-in-production"

    ALLOWED_ORIGINS: str = "http://localhost:8080,http://127.0.0.1:8080,https://oldisserving.live"

    # LLM Provider Configuration ('gemini' or 'kiconnect')
    DEFAULT_LLM_PROVIDER: str = "gemini"

    # Google Gemini Settings
    GEMINI_API_KEY: str = ""
    DEFAULT_GEMINI_MODEL: str = "gemini-3.8-flash"

    # University KI-Connect / OpenAI-Compatible Endpoint
    API_BASE: str = ""
    API_KEY: str = ""
    API_CHAT_MODEL: str = "kiconnect.mistralai-mistral-small-4-119b-2603"

    # Rate Limiting & Pacing Guards (Slow is fine - Reliability is top priority)
    LLM_MIN_INTERVAL_SEC: float = 4.0
    LLM_MAX_RETRIES: int = 4

    # Storage Paths
    STORAGE_DIR: str = "storage"
    DATA_DIR: str = "data"

    @property
    def allowed_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def uploads_path(self) -> str:
        path = os.path.join(self.STORAGE_DIR, "uploads")
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def exports_path(self) -> str:
        path = os.path.join(self.STORAGE_DIR, "exports")
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def db_path(self) -> str:
        os.makedirs(self.STORAGE_DIR, exist_ok=True)
        return os.path.join(self.STORAGE_DIR, "pmga.db")


settings = Settings()
