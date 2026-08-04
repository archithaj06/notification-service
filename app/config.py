"""
Centralized application configuration.

All values are overridable via environment variables (or a .env file in local dev).
This keeps the same codebase portable between local dev, Docker Compose, and CI.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---
    database_url: str = "postgresql://notif_user:notif_pass@localhost:5432/notification_service"

    # --- Redis / Queue ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Rate limiting ---
    rate_limit_max_per_hour: int = 100
    rate_limit_window_seconds: int = 3600

    # --- Retry policy ---
    max_retries: int = 3
    retry_base_delay_seconds: int = 30  # exponential backoff base: 30s, 60s, 120s

    # --- Circuit breaker (per channel) ---
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_seconds: int = 60

    # --- App ---
    app_name: str = "Notification Service"
    log_level: str = "INFO"
    environment: str = "development"


settings = Settings()
