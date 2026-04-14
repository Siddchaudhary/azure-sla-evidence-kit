"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "Azure SLA Dashboard"
    debug: bool = False
    log_level: str = "INFO"

    # Database
    database_url: str = "sqlite+aiosqlite:///~/.azsla/sla_data.db"

    # Azure
    azure_subscription_ids: Optional[str] = None  # Comma-separated
    azure_client_id: Optional[str] = None
    azure_client_secret: Optional[str] = None
    azure_tenant_id: Optional[str] = None

    # Scheduler
    collection_enabled: bool = True
    collection_interval_hours: int = 6  # Collect every 6 hours
    collection_lookback_days: int = 1   # Collect last 1 day of data each run

    # Web server
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def subscription_list(self) -> list[str]:
        """Parse subscription IDs into a list."""
        if not self.azure_subscription_ids:
            return []
        return [s.strip() for s in self.azure_subscription_ids.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
