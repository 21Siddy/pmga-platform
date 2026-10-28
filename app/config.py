import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration with environment-variable overrides.
    Follows ISO 27001 principles: secure defaults, strict CORS,
    and ephemeral BYOK key precedence over disk/server configuration.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_BASE_URL: str = "http://localhost:8000"
    APP_SECRET_KEY: str = "change-me-to-a-secure-random-token-in-production"

    ALLOWED_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000,https://oldisserving.live"

    # Optional server fallback for demo mode
    GEMINI_API_KEY: str = ""
    DEFAULT_GEMINI_MODEL: str = "gemini-2.5-flash"

    # Optional Webhook URL
    WEBHOOK_URL: str = ""

    # Storage paths
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
        return os.path.join(self.STORAGE_DIR, "civic_bpm.db")


settings = Settings()
